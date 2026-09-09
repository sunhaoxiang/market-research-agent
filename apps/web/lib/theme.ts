export const THEME_STORAGE_KEY = "mra-theme";

export const THEME_PREFERENCES = ["system", "light", "dark"] as const;

export type ThemePreference = (typeof THEME_PREFERENCES)[number];

export type ResolvedTheme = "light" | "dark";

export const THEME_LABELS: Record<ThemePreference, string> = {
  system: "跟随系统",
  light: "浅色",
  dark: "深色",
};

export function parseTheme(raw: string | null | undefined): ThemePreference {
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

/** 顶栏只有一格：系统 → 浅色 → 深色 → 系统。默认跟系统，点一下才锁死。 */
export function nextTheme(current: ThemePreference): ThemePreference {
  if (current === "system") return "light";
  if (current === "light") return "dark";
  return "system";
}

export function prefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function resolveTheme(
  preference: ThemePreference,
  systemDark = prefersDark(),
): ResolvedTheme {
  if (preference === "system") return systemDark ? "dark" : "light";
  return preference;
}

export function applyTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement) {
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

export function readStoredTheme(): ThemePreference {
  try {
    return parseTheme(localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

const listeners = new Set<() => void>();

export function persistTheme(preference: ThemePreference) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    /* 隐私模式写不进去时，这次会话内切换仍然生效 */
  }
  for (const listener of listeners) listener();
}

/** 同页 persist 不会触发 `storage`，要自己广播；跨页靠原生事件。 */
export function subscribeTheme(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange);
  if (typeof window === "undefined") {
    return () => {
      listeners.delete(onStoreChange);
    };
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key !== THEME_STORAGE_KEY && event.key !== null) return;
    onStoreChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function themeButtonLabel(preference: ThemePreference): string {
  const next = nextTheme(preference);
  return `外观：${THEME_LABELS[preference]}。点击切换到${THEME_LABELS[next]}`;
}

/**
 * 进 `<head>` 的同步脚本。必须和 `parseTheme` / `resolveTheme` / `applyTheme`
 * 同一套规则，否则刷新会闪错色。
 */
export const THEME_BOOTSTRAP = `(function(){try{var p=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});var d=p==="dark"||(p!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);var r=document.documentElement;r.classList.toggle("dark",d);r.style.colorScheme=d?"dark":"light";}catch(e){}})();`;

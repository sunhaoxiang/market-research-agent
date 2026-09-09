import { afterEach, describe, expect, it, vi } from "vitest";

import {
  THEME_BOOTSTRAP,
  THEME_STORAGE_KEY,
  applyTheme,
  nextTheme,
  parseTheme,
  persistTheme,
  prefersDark,
  readStoredTheme,
  resolveTheme,
  themeButtonLabel,
} from "@/lib/theme";

afterEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
  vi.unstubAllGlobals();
});

describe("parseTheme / nextTheme / resolveTheme", () => {
  it("无法识别的值当成跟随系统", () => {
    expect(parseTheme(null)).toBe("system");
    expect(parseTheme("")).toBe("system");
    expect(parseTheme("auto")).toBe("system");
    expect(parseTheme("dark")).toBe("dark");
  });

  it("循环是系统 → 浅色 → 深色", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });

  it("跟随系统时按媒体查询解析", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });
});

describe("applyTheme / storage", () => {
  it("深色打 html.dark 并设 color-scheme", () => {
    applyTheme("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
    applyTheme("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
  });

  it("读写 localStorage，坏值回落到系统", () => {
    persistTheme("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(readStoredTheme()).toBe("dark");
    localStorage.setItem(THEME_STORAGE_KEY, "nope");
    expect(readStoredTheme()).toBe("system");
  });
});

describe("prefersDark / 按钮文案 / 首屏脚本", () => {
  it("没有 matchMedia 时不当深色", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersDark()).toBe(false);
  });

  it("按钮说明当前项和下一项", () => {
    expect(themeButtonLabel("system")).toBe("外观：跟随系统。点击切换到浅色");
    expect(themeButtonLabel("light")).toBe("外观：浅色。点击切换到深色");
    expect(themeButtonLabel("dark")).toBe("外观：深色。点击切换到跟随系统");
  });

  it("首屏脚本带存储键，且把非 light 交给系统", () => {
    expect(THEME_BOOTSTRAP).toContain(THEME_STORAGE_KEY);
    expect(THEME_BOOTSTRAP).toContain('p==="dark"');
    expect(THEME_BOOTSTRAP).toContain('p!=="light"');
  });
});

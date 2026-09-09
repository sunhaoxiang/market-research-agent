"use client";

import { useLayoutEffect, useSyncExternalStore } from "react";

import {
  type ThemePreference,
  applyTheme,
  nextTheme,
  persistTheme,
  readStoredTheme,
  resolveTheme,
  subscribeTheme,
  themeButtonLabel,
} from "@/lib/theme";

export function ThemeToggle() {
  const preference = useSyncExternalStore(
    subscribeTheme,
    readStoredTheme,
    (): ThemePreference => "system",
  );

  useLayoutEffect(() => {
    applyTheme(resolveTheme(preference));
    if (preference !== "system") return;
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onMedia = () => applyTheme(resolveTheme("system"));
    media?.addEventListener("change", onMedia);
    return () => media?.removeEventListener("change", onMedia);
  }, [preference]);

  return (
    <button
      type="button"
      onClick={() => persistTheme(nextTheme(preference))}
      aria-label={themeButtonLabel(preference)}
      title={themeButtonLabel(preference)}
      className="focus-visible:ring-accent rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 focus-visible:ring-2 focus-visible:outline-none dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
    >
      <ThemeGlyph preference={preference} />
    </button>
  );
}

function ThemeGlyph({ preference }: { preference: ThemePreference }) {
  if (preference === "light") {
    return (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
        <circle cx="8" cy="8" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M8 1.75v1.4M8 12.85v1.4M1.75 8h1.4M12.85 8h1.4M3.22 3.22l.99.99M11.79 11.79l.99.99M3.22 12.78l.99-.99M11.79 4.21l.99-.99"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  if (preference === "dark") {
    return (
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
        <path
          d="M13.2 10.4A5.4 5.4 0 0 1 5.6 2.8 4.35 4.35 0 1 0 13.2 10.4z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden>
      <rect
        x="2.25"
        y="3"
        width="11.5"
        height="7.5"
        rx="1.4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M5 13.25h6M8 10.5v2.75"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

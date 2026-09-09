import Link from "next/link";
import type { Route } from "next";

import { cn } from "@/lib/utils";

export function AppHeader({ current }: { current: "research" | "history" | "settings" | "debug" }) {
  return (
    <header className="mb-8 flex flex-wrap items-center justify-between gap-4 border-b border-zinc-200 pb-5 dark:border-zinc-800">
      <Link
        href="/"
        aria-label="Market Research Agent — Crypto 与美股研究"
        className="focus-visible:ring-accent flex items-center gap-3 rounded-md focus-visible:ring-2 focus-visible:outline-none"
      >
        <BrandMark />
        <span className="flex flex-col">
          <span className="text-[15px] leading-none font-semibold tracking-tight">
            Market Research Agent
          </span>
          <span className="mt-1.5 text-[11px] leading-none text-zinc-500">Crypto · 美股研究</span>
        </span>
      </Link>
      <nav aria-label="主导航" className="flex gap-4 text-sm">
        <NavLink href={"/" as Route} active={current === "research"}>
          研究
        </NavLink>
        <NavLink href={"/history" as Route} active={current === "history"}>
          历史
        </NavLink>
        <NavLink href={"/settings" as Route} active={current === "settings"}>
          设置
        </NavLink>
        <NavLink href={"/debug" as Route} active={current === "debug"}>
          调试
        </NavLink>
      </nav>
    </header>
  );
}

function BrandMark() {
  return (
    <svg viewBox="0 0 32 32" width="32" height="32" aria-hidden className="text-accent shrink-0">
      <rect width="32" height="32" rx="8" fill="currentColor" />
      <polyline
        points="6.5,21.5 12,15.5 16.5,19 25.5,9.5"
        fill="none"
        stroke="var(--accent-contrast)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function NavLink({ href, active, children }: { href: Route; active: boolean; children: string }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "focus-visible:ring-accent rounded-sm underline-offset-4 focus-visible:ring-2 focus-visible:outline-none",
        active
          ? "text-accent-text font-medium"
          : "text-zinc-500 hover:text-zinc-800 hover:underline dark:hover:text-zinc-200",
      )}
    >
      {children}
    </Link>
  );
}

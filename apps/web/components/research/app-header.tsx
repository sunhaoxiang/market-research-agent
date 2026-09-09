import Link from "next/link";
import type { Route } from "next";

import { cn } from "@/lib/utils";

export function AppHeader({ current }: { current: "research" | "history" | "settings" | "debug" }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold">
          <Link href="/" className="hover:underline">
            Market Research Agent
          </Link>
        </h1>
        <p className="mt-1 text-sm text-zinc-500">
          AI Financial Research Platform — Crypto &amp; US Stocks
        </p>
      </div>
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

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Route } from "next";

import { BrandMark } from "@/components/brand-mark";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/" as Route, id: "research", label: "研究" },
  { href: "/history" as Route, id: "history", label: "历史" },
  { href: "/settings" as Route, id: "settings", label: "设置" },
  { href: "/debug" as Route, id: "debug", label: "调试" },
] as const;

export type AppNavId = (typeof NAV)[number]["id"];

export function navFromPath(path: string): AppNavId {
  if (path.startsWith("/history")) return "history";
  if (path.startsWith("/settings")) return "settings";
  if (path.startsWith("/debug")) return "debug";
  return "research";
}

/**
 * 全站应用外壳：56px 细顶栏，不承担页面标题。
 *
 * 副标题只在首页 hero；当前研究问题在会话区单独一行。
 */
export function AppHeader() {
  const current = navFromPath(usePathname() ?? "/");

  return (
    <header className="bg-background/90 sticky top-0 z-40 h-14 border-b border-zinc-200 backdrop-blur-md dark:border-zinc-800">
      <div className="mx-auto flex h-full max-w-6xl items-center justify-between gap-4 px-6">
        <Link
          href={"/" as Route}
          aria-label="Market Research Agent"
          className="focus-visible:ring-accent flex min-w-0 items-center gap-2.5 rounded-md focus-visible:ring-2 focus-visible:outline-none"
        >
          <BrandMark size={24} />
          <span className="truncate text-[15px] font-semibold tracking-tight">
            Market Research Agent
          </span>
        </Link>
        <div className="flex shrink-0 items-center gap-3 sm:gap-4">
          <nav aria-label="主导航" className="flex gap-3 text-sm sm:gap-4">
            {NAV.map((item) => (
              <NavLink key={item.id} href={item.href} active={current === item.id}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <AgentStatus />
        </div>
      </div>
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

function AgentStatus() {
  const [ok, setOk] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/health")
      .then((response) => {
        if (!cancelled) setOk(response.ok);
      })
      .catch(() => {
        if (!cancelled) setOk(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (ok === null) return null;

  return (
    <span
      className="flex items-center"
      title={ok ? "Agent 正常" : "Agent 不可用"}
      aria-label={ok ? "Agent 正常" : "Agent 不可用"}
    >
      <span
        aria-hidden
        className={cn("size-1.5 rounded-full", ok ? "bg-emerald-500" : "bg-amber-500")}
      />
    </span>
  );
}

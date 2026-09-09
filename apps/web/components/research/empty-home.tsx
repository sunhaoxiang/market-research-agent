"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import type { Route } from "next";

import {
  EXAMPLE_PROMPTS,
  HOME_DISCLAIMER,
  QUESTION_TYPE_LABELS,
  SESSION_ROW_STATUS_LABELS,
  formatSessionDay,
  type RecentSessionPreview,
} from "@/lib/research/empty-home";
import { cn } from "@/lib/utils";

type EmptyHomeProps = {
  children: ReactNode;
  onPickExample: (question: string) => void;
  recent: RecentSessionPreview[];
};

export function EmptyHome({ children, onPickExample, recent }: EmptyHomeProps) {
  return (
    <div className="mx-auto flex min-h-[calc(100dvh-11rem)] w-full max-w-3xl flex-col justify-center py-6">
      <header className="space-y-2 text-center">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          带来源、区分事实与推测
        </h1>
        <p className="text-sm leading-relaxed text-zinc-500">
          加密与美股研究。引用可点回原文，冲突的数字并列列出，而不是取平均。
        </p>
      </header>

      <div className="mt-8">{children}</div>

      <section className="mt-8" aria-labelledby="example-prompts-heading">
        <h2
          id="example-prompts-heading"
          className="text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase"
        >
          试试这些
        </h2>
        <ul className="mt-3 grid gap-2 sm:grid-cols-3">
          {EXAMPLE_PROMPTS.map((example) => (
            <li key={example.question}>
              <button
                type="button"
                onClick={() => onPickExample(example.question)}
                className={cn(
                  "h-full w-full rounded-lg border border-zinc-200 px-3 py-3 text-left transition",
                  "hover:border-accent/40 hover:bg-accent-subtle",
                  "focus-visible:ring-accent focus-visible:ring-2 focus-visible:outline-none",
                  "dark:border-zinc-800 dark:hover:bg-zinc-900",
                )}
              >
                <span className="text-accent-text text-[11px] font-medium">{example.label}</span>
                <span className="mt-1 block text-sm font-medium text-zinc-800 dark:text-zinc-100">
                  {example.title}
                </span>
                <span className="mt-1 line-clamp-2 block text-xs leading-relaxed text-zinc-500">
                  {example.question}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {recent.length > 0 && (
        <section className="mt-10" aria-labelledby="recent-sessions-heading">
          <div className="flex items-baseline justify-between gap-3">
            <h2
              id="recent-sessions-heading"
              className="text-[11px] font-medium tracking-[0.16em] text-zinc-500 uppercase"
            >
              最近研究
            </h2>
            <Link
              href={"/history" as Route}
              className="text-xs text-zinc-500 hover:text-zinc-800 hover:underline focus-visible:ring-accent rounded-sm focus-visible:ring-2 focus-visible:outline-none dark:hover:text-zinc-200"
            >
              全部
            </Link>
          </div>
          <ul className="mt-3 divide-y divide-zinc-200 dark:divide-zinc-800">
            {recent.map((session) => (
              <li key={session.id}>
                <Link
                  href={`/?session=${encodeURIComponent(session.id)}` as Route}
                  className="block rounded-md py-2.5 hover:bg-zinc-50 focus-visible:ring-accent focus-visible:ring-2 focus-visible:outline-none dark:hover:bg-zinc-900/40"
                >
                  <span className="line-clamp-2 text-sm font-medium text-zinc-800 dark:text-zinc-100">
                    {session.question}
                  </span>
                  <span className="mt-1 block text-xs text-zinc-500">
                    {SESSION_ROW_STATUS_LABELS[session.status]}
                    {session.questionType ? ` · ${QUESTION_TYPE_LABELS[session.questionType]}` : ""}
                    {` · ${formatSessionDay(session.createdAt)}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <p className="mt-10 text-center text-[11px] leading-relaxed text-zinc-400">
        {HOME_DISCLAIMER}
      </p>
    </div>
  );
}

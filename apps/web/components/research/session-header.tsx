"use client";

import type { ReactNode } from "react";

import { SESSION_STATUS_LABELS, STAGE_LABELS, taskCounts } from "@/lib/research/stages";
import type { ResearchViewState } from "@/lib/research/state";
import { cn, formatCost, formatDuration, formatTokens } from "@/lib/utils";

const STATUS_DOT: Record<ResearchViewState["status"], string> = {
  idle: "bg-zinc-300 dark:bg-zinc-600",
  running: "motion-safe:animate-pulse bg-accent",
  completed: "bg-emerald-500",
  failed: "bg-amber-500",
  cancelled: "bg-zinc-400",
};

export function SessionHeader({
  state,
  now,
  costLimitUsd,
}: {
  state: ResearchViewState;
  now: number;
  costLimitUsd: number;
}) {
  const look = SESSION_STATUS_LABELS[state.status];
  // 结束后用后端给的权威耗时；进行中按本地时钟算，每秒走一格
  const duration =
    state.durationMs ??
    (state.startedAtMs !== null && now > 0 ? Math.max(0, now - state.startedAtMs) : null);

  const stage = state.status === "running" && state.stage ? STAGE_LABELS[state.stage] : undefined;
  const counts = taskCounts(state);
  const overBudget =
    state.warnings.some((warning) => warning.code === "budget_exhausted") ||
    (state.costUsd !== null && state.costUsd >= costLimitUsd);
  const budgetWarning = state.warnings.find((warning) => warning.code === "budget_exhausted");
  const cachePct = cachePercent(state.usage.input, state.usage.cached);
  const spendRatio =
    state.costUsd === null || costLimitUsd <= 0 ? 0 : Math.min(1, state.costUsd / costLimitUsd);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={cn("inline-block size-2 rounded-full", STATUS_DOT[state.status])}
          />
          <span className="font-medium text-zinc-800 dark:text-zinc-200">
            {stage ? `${look} · ${stage}` : look}
          </span>
        </span>

        {overBudget && (
          <span className="text-amber-600 dark:text-amber-500">
            {budgetWarning?.message ?? "已达成本上限"}
          </span>
        )}

        {state.hasGap && (
          <span className="text-amber-600 dark:text-amber-500">事件有缺失，刷新可查看完整结果</span>
        )}
      </div>

      <div className="flex flex-wrap items-start gap-x-8 gap-y-3">
        <Stat label="耗时" value={formatDuration(duration)} />

        <Stat
          label="成本"
          value={formatCost(state.costUsd)}
          tone={overBudget ? "warn" : "default"}
          ariaLabel={`成本 ${formatCost(state.costUsd)} / ${formatCost(costLimitUsd)}`}
        >
          <div className="mt-1 flex items-center gap-2">
            <div
              aria-hidden
              className="h-1 w-16 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700"
            >
              <div
                className={cn("h-full rounded-full", overBudget ? "bg-amber-500" : "bg-accent")}
                style={{ width: `${Math.round(spendRatio * 100)}%` }}
              />
            </div>
            <span className="text-[11px] tabular-nums text-zinc-500">
              {formatCost(costLimitUsd)}
            </span>
          </div>
        </Stat>

        <Stat
          label="Token"
          value={`${formatTokens(state.usage.input)} in · ${formatTokens(state.usage.output)} out`}
          hint={cachePct === null ? undefined : `缓存 ${cachePct}%`}
        />

        {counts ? (
          <Stat
            label="任务"
            value={`${counts.settled}/${counts.total}`}
            hint={counts.current ?? undefined}
          />
        ) : null}
      </div>
    </div>
  );
}

function cachePercent(input: number, cached: number): number | null {
  if (cached <= 0 || input <= 0) return null;
  return Math.min(100, Math.round((cached / input) * 100));
}

function Stat({
  label,
  value,
  hint,
  tone = "default",
  ariaLabel,
  children,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "warn";
  ariaLabel?: string;
  children?: ReactNode;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel ?? (hint ? `${label} ${value} · ${hint}` : `${label} ${value}`)}
    >
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div
        className={cn(
          "text-sm font-medium tabular-nums",
          tone === "warn"
            ? "text-amber-600 dark:text-amber-500"
            : "text-zinc-900 dark:text-zinc-100",
        )}
      >
        {value}
      </div>
      {children}
      {hint ? <div className="mt-0.5 text-[11px] text-zinc-500">{hint}</div> : null}
    </div>
  );
}

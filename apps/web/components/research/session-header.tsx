"use client";

import { SESSION_STATUS_LABELS, STAGE_LABELS, taskProgress } from "@/lib/research/stages";
import type { ResearchViewState } from "@/lib/research/state";
import { cn, formatCost, formatDuration, formatTokens } from "@/lib/utils";

const STATUS_DOT: Record<ResearchViewState["status"], string> = {
  idle: "bg-zinc-300 dark:bg-zinc-600",
  running: "motion-safe:animate-pulse bg-blue-500",
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
  const progress = taskProgress(state);
  const overBudget =
    state.warnings.some((warning) => warning.code === "budget_exhausted") ||
    (state.costUsd !== null && state.costUsd >= costLimitUsd);
  const budgetWarning = state.warnings.find((warning) => warning.code === "budget_exhausted");

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
      <span className="flex items-center gap-1.5">
        <span
          aria-hidden
          className={cn("inline-block size-2 rounded-full", STATUS_DOT[state.status])}
        />
        <span className="font-medium text-zinc-700 dark:text-zinc-300">
          {stage ? `${look} · ${stage}` : look}
        </span>
      </span>

      <span>{formatDuration(duration)}</span>

      <span>
        {formatTokens(state.usage.input)} in / {formatTokens(state.usage.output)} out
        {state.usage.cached > 0 && ` · ${formatTokens(state.usage.cached)} 缓存`}
      </span>

      <span className={overBudget ? "font-medium text-amber-600 dark:text-amber-500" : undefined}>
        {formatCost(state.costUsd)} / {formatCost(costLimitUsd)}
      </span>

      {overBudget && (
        <span className="text-amber-600 dark:text-amber-500">
          {budgetWarning?.message ?? "已达成本上限"}
        </span>
      )}

      {state.hasGap && (
        <span className="text-amber-600 dark:text-amber-500">事件有缺失，刷新可查看完整结果</span>
      )}

      {progress ? <span>{progress}</span> : null}
    </div>
  );
}

"use client";

import { AGENT_LABELS } from "@/components/research/activity-panel";
import type { ResearchViewState } from "@/lib/research/state";
import { cn, formatCost, formatDuration, formatTokens } from "@/lib/utils";

const STAGE_LABELS: Record<string, string> = {
  planning: "制定计划",
  researching: "执行研究",
  checking: "事实核查",
  writing: "撰写报告",
};

const STATUS_LOOK: Record<ResearchViewState["status"], { label: string; dot: string }> = {
  idle: { label: "待提问", dot: "bg-zinc-300 dark:bg-zinc-600" },
  running: { label: "进行中", dot: "animate-pulse bg-blue-500" },
  completed: { label: "已完成", dot: "bg-emerald-500" },
  failed: { label: "失败", dot: "bg-amber-500" },
  cancelled: { label: "已取消", dot: "bg-zinc-400" },
};

export function SessionHeader({ state, now }: { state: ResearchViewState; now: number }) {
  const look = STATUS_LOOK[state.status];
  // 结束后用后端给的权威耗时；进行中按本地时钟算，每秒走一格
  const duration =
    state.durationMs ??
    (state.startedAtMs !== null && now > 0 ? Math.max(0, now - state.startedAtMs) : null);

  const stage = state.status === "running" ? STAGE_LABELS[state.stage ?? ""] : undefined;
  const progress = taskProgress(state);

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
      <span className="flex items-center gap-1.5">
        <span aria-hidden className={cn("inline-block size-2 rounded-full", look.dot)} />
        <span className="font-medium text-zinc-700 dark:text-zinc-300">
          {stage ? `${look.label} · ${stage}` : look.label}
        </span>
      </span>

      <span>{formatDuration(duration)}</span>

      <span>
        {formatTokens(state.usage.input)} in / {formatTokens(state.usage.output)} out
        {state.usage.cached > 0 && ` · ${formatTokens(state.usage.cached)} 缓存`}
      </span>

      <span>{formatCost(state.costUsd)}</span>

      {state.hasGap && (
        <span className="text-amber-600 dark:text-amber-500">事件有缺失，刷新可查看完整结果</span>
      )}

      {progress ? <span>{progress}</span> : null}
    </div>
  );
}

function taskProgress(state: ResearchViewState): string | null {
  const tasks = state.taskIds.map((id) => state.tasks[id]).filter((task) => task !== undefined);
  if (tasks.length === 0) return null;
  const settled = tasks.filter(
    (task) => task.status === "completed" || task.status === "failed" || task.status === "skipped",
  ).length;
  const running = tasks.find((task) => task.status === "running");
  const current = running ? (AGENT_LABELS[running.agent] ?? running.agent) : null;
  return current ? `${settled}/${tasks.length} · ${current}` : `${settled}/${tasks.length}`;
}

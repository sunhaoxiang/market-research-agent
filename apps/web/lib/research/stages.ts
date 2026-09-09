/**
 * 阶段瀑布（P6-2）：把 reducer 记下的起止时间收成可渲染的耗时。
 *
 * 时钟留在调用方（`now`），和任务行的实时耗时同一套规则——reducer 保持纯函数。
 */

import type { Stage } from "@mra/shared";

import type { ResearchViewState, StageSpan } from "@/lib/research/state";

export const STAGE_LABELS: Record<Stage, string> = {
  planning: "制定计划",
  researching: "执行研究",
  checking: "事实核查",
  writing: "撰写报告",
};

export const AGENT_LABELS: Record<string, string> = {
  research_manager: "Research Manager",
  crypto_research: "Crypto Research",
  stock_research: "Stock Research",
  web_research: "Web Research",
  fact_checker: "Fact Checker",
  report_writer: "Report Writer",
};

export const SESSION_STATUS_LABELS: Record<ResearchViewState["status"], string> = {
  idle: "待提问",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function taskCounts(state: ResearchViewState): {
  settled: number;
  total: number;
  current: string | null;
} | null {
  const tasks = state.taskIds.map((id) => state.tasks[id]).filter((task) => task !== undefined);
  if (tasks.length === 0) return null;
  const settled = tasks.filter(
    (task) => task.status === "completed" || task.status === "failed" || task.status === "skipped",
  ).length;
  const running = tasks.find((task) => task.status === "running");
  const current = running ? (AGENT_LABELS[running.agent] ?? running.agent) : null;
  return { settled, total: tasks.length, current };
}

export function taskProgress(state: ResearchViewState): string | null {
  const counts = taskCounts(state);
  if (!counts) return null;
  return counts.current
    ? `${counts.settled}/${counts.total} · ${counts.current}`
    : `${counts.settled}/${counts.total}`;
}

export type ResolvedStageSpan = StageSpan & {
  durationMs: number;
  active: boolean;
};

export function resolveStageSpans(state: ResearchViewState, now: number): ResolvedStageSpan[] {
  return state.stages.map((span, index) => {
    const last = index === state.stages.length - 1;
    const active = last && span.endedAtMs === null && state.status === "running";
    const end = spanEndMs(span, state, now);
    return {
      ...span,
      durationMs: Math.max(0, end - span.startedAtMs),
      active,
    };
  });
}

function spanEndMs(span: StageSpan, state: ResearchViewState, now: number): number {
  if (span.endedAtMs !== null) return span.endedAtMs;
  if (state.status !== "running") {
    return state.completedAtMs ?? span.startedAtMs;
  }
  if (now > 0) return Math.max(now, span.startedAtMs);
  return span.startedAtMs;
}

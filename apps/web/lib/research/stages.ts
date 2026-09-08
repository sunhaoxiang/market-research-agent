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

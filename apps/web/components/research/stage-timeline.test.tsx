/**
 * 阶段瀑布：归约后的片段要能看出各阶段耗时（P6-2）。
 */

import { render, screen } from "@testing-library/react";
import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { StageTimeline } from "@/components/research/stage-timeline";
import { resolveStageSpans } from "@/lib/research/stages";
import { reduceAll } from "@/lib/research/state";

let seq = 0;

function event(type: string, payload: unknown = null): ResearchEvent {
  seq += 1;
  return {
    seq,
    session_id: "sess-1",
    ts: new Date(1_800_000_000_000 + seq * 10_000).toISOString(),
    message: null,
    type,
    payload,
  } as unknown as ResearchEvent;
}

function completedRun() {
  seq = 0;
  return reduceAll([
    event("session_started", { question: "Q", model_id: "m" }),
    event("stage_changed", { stage: "planning", previous: null }),
    event("stage_changed", { stage: "researching", previous: "planning" }),
    event("stage_changed", { stage: "checking", previous: "researching" }),
    event("stage_changed", { stage: "writing", previous: "checking" }),
    event("session_completed", {
      duration_ms: 50_000,
      usage: { input: 0, output: 0, cached: 0 },
      cost_usd: null,
    }),
  ]);
}

describe("resolveStageSpans", () => {
  it("按相邻事件的间隔给出各阶段耗时", () => {
    const spans = resolveStageSpans(completedRun(), 0);
    expect(spans.map((span) => [span.stage, span.durationMs, span.active])).toEqual([
      ["planning", 10_000, false],
      ["researching", 10_000, false],
      ["checking", 10_000, false],
      ["writing", 10_000, false],
    ]);
  });

  it("进行中的阶段用 now 往前走", () => {
    seq = 0;
    const state = reduceAll([
      event("session_started", { question: "Q", model_id: "m" }),
      event("stage_changed", { stage: "researching", previous: "planning" }),
    ]);
    const started = state.stages[0]!.startedAtMs;
    const spans = resolveStageSpans(state, started + 25_000);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.durationMs).toBe(25_000);
    expect(spans[0]?.active).toBe(true);
  });
});

describe("StageTimeline", () => {
  it("列出每个阶段的名字和耗时，不靠颜色单独表达", () => {
    render(<StageTimeline state={completedRun()} now={0} />);
    const region = screen.getByRole("region", { name: "阶段耗时" });
    expect(region.textContent).toMatch(/制定计划 · 10\.0s/);
    expect(region.textContent).toMatch(/执行研究 · 10\.0s/);
    expect(region.textContent).toMatch(/事实核查 · 10\.0s/);
    expect(region.textContent).toMatch(/撰写报告 · 10\.0s/);
  });

  it("还没进入任何阶段时不渲染", () => {
    seq = 0;
    const state = reduceAll([event("session_started", { question: "Q", model_id: "m" })]);
    const { container } = render(<StageTimeline state={state} now={0} />);
    expect(container.firstChild).toBeNull();
  });
});

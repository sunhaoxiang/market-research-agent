/**
 * 状态播报：不依赖过程面板是否展开（P6-3）。
 */

import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { liveAnnouncement } from "@/lib/research/live-status";
import { reduceAll } from "@/lib/research/state";

let seq = 0;

function event(
  type: string,
  payload: unknown = null,
  message: string | null = null,
): ResearchEvent {
  seq += 1;
  return {
    seq,
    session_id: "sess-1",
    ts: new Date(1_800_000_000_000 + seq * 1000).toISOString(),
    message,
    type,
    payload,
  } as unknown as ResearchEvent;
}

const PLAN = {
  question_type: "crypto",
  interpretation: "用户想了解 Hyperliquid",
  entities: [],
  tasks: [
    {
      id: "t1",
      agent: "crypto_research",
      objective: "获取 TVL",
      entities: [],
      suggested_tools: [],
      depends_on: [],
      priority: 0,
    },
  ],
  report_sections: ["Overview"],
  assumptions: [],
};

describe("liveAnnouncement", () => {
  it("进行中播报阶段、进度和最近一句", () => {
    seq = 0;
    const state = reduceAll([
      event("session_started", { question: "Q", model_id: "m" }),
      event("stage_changed", { stage: "researching", previous: "planning" }, "正在执行研究任务"),
      event("plan_created", { plan: PLAN }),
      event("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "获取 TVL",
        model_id: "m",
      }),
    ]);

    expect(liveAnnouncement(state)).toBe(
      "进行中，执行研究。0/1 · Crypto Research。正在执行研究任务",
    );
  });

  it("没有文案的事件不会把播报清空", () => {
    seq = 0;
    const state = reduceAll([
      event("session_started", { question: "Q", model_id: "m" }),
      event("stage_changed", { stage: "planning", previous: null }, "正在制定研究计划"),
      event("heartbeat"),
    ]);

    expect(liveAnnouncement(state)).toBe("进行中，制定计划。正在制定研究计划");
  });

  it("失败时播报错误，不只说失败", () => {
    seq = 0;
    const state = reduceAll([
      event("session_started", { question: "Q", model_id: "m" }),
      event("session_failed", {
        error: { code: "plan_rejected", message: "计划为空" },
        stage: "planning",
      }),
    ]);

    expect(liveAnnouncement(state)).toBe("失败。plan_rejected：计划为空");
  });

  it("空闲时不播报", () => {
    expect(liveAnnouncement(reduceAll([]))).toBe("");
  });
});

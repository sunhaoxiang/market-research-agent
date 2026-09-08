/**
 * 研究过程整段折叠（§13.1 / P6-1）。
 */

import { fireEvent, render, screen } from "@testing-library/react";
import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { ProcessPanel } from "@/components/research/process-panel";
import { reduceAll } from "@/lib/research/state";

let seq = 0;

function event(type: string, payload: unknown = null): ResearchEvent {
  seq += 1;
  return {
    seq,
    session_id: "sess-1",
    ts: new Date(1_800_000_000_000 + seq * 1000).toISOString(),
    message: null,
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

function runningState() {
  seq = 0;
  return reduceAll([
    event("session_started", { question: "Q", model_id: "m" }),
    event("stage_changed", { stage: "researching", previous: "planning" }),
    event("plan_created", { plan: PLAN }),
    event("agent_started", {
      agent: "crypto_research",
      task_id: "t1",
      objective: "获取 TVL",
      model_id: "m",
    }),
  ]);
}

function completedState() {
  seq = 0;
  return reduceAll([
    event("session_started", { question: "Q", model_id: "m" }),
    event("plan_created", { plan: PLAN }),
    event("session_completed", {
      duration_ms: 1000,
      usage: { input: 0, output: 0, cached: 0 },
      cost_usd: null,
    }),
  ]);
}

describe("过程折叠", () => {
  it("进行中默认展开任务树", () => {
    render(<ProcessPanel state={runningState()} now={1_800_000_010_000} />);
    expect(screen.getByRole("button", { name: /研究过程/ }).getAttribute("aria-expanded")).toBe(
      "true",
    );
    expect(screen.getByText("Crypto Research")).toBeDefined();
  });

  it("完成后默认收起", () => {
    render(<ProcessPanel state={completedState()} now={0} />);
    expect(screen.getByRole("button", { name: /研究过程/ }).getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(screen.getByRole("button", { name: /研究过程/ }).getAttribute("aria-controls")).toBe(
      "research-process",
    );
    expect(document.getElementById("research-process")?.hidden).toBe(true);
  });

  it("点击后可以展开已完成的过程", () => {
    render(<ProcessPanel state={completedState()} now={0} />);
    fireEvent.click(screen.getByRole("button", { name: /研究过程/ }));
    expect(screen.getByText("Crypto Research")).toBeDefined();
  });
});

/**
 * 顶栏用量：过程折叠时仍要看得见成本和超限（P6-10）。
 */

import { render, screen } from "@testing-library/react";
import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { SessionHeader } from "@/components/research/session-header";
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

describe("SessionHeader", () => {
  it("usage_updated 立刻画出累计 token 与成本相对上限", () => {
    seq = 0;
    const state = reduceAll([
      event("session_started", { question: "Q", model_id: "m" }),
      event("usage_updated", {
        usage: { input: 1200, output: 80, cached: 96 },
        cost_usd: 0.0031,
      }),
    ]);

    render(<SessionHeader state={state} now={0} costLimitUsd={1} />);
    expect(screen.getByText("1.2k in · 80 out")).toBeTruthy();
    expect(screen.getByRole("group", { name: "成本 $0.0031 / $1.00" })).toBeTruthy();
    expect(screen.getByText("缓存 8%")).toBeTruthy();
    expect(screen.getByText("耗时")).toBeTruthy();
    expect(screen.getByText("Token")).toBeTruthy();
  });

  it("超限用文字标出，不只改颜色", () => {
    seq = 0;
    const state = reduceAll([
      event("session_started", { question: "Q", model_id: "m" }),
      event("usage_updated", {
        usage: { input: 10_000, output: 2000, cached: 0 },
        cost_usd: 1.2,
      }),
      event("warning", { code: "budget_exhausted", message: "会话成本已达上限 $1.00" }),
    ]);

    render(<SessionHeader state={state} now={0} costLimitUsd={1} />);
    expect(screen.getByText("会话成本已达上限 $1.00")).toBeTruthy();
    expect(screen.getByRole("group", { name: "成本 $1.20 / $1.00" })).toBeTruthy();
    expect(screen.getByText("$1.20")).toBeTruthy();
  });
});

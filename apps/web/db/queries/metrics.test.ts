/**
 * @vitest-environment node
 *
 * 结构化指标落库（§20.1，P1-13 验收）。
 *
 * ⚠️ 工具调用部分只能用合成事件测：Phase 1 的任务由占位 runner 驱动，
 * 一次真实研究产生 0 个 tool_call。真实数据要等 Phase 2 接入工具后才有，
 * 所以这里刻意把关联逻辑的边界情形（乱序、缺失、未闭合）测厚一些。
 */

import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { ToolCallCorrelator, toAgentRun } from "@/db/queries/metrics";
import type { NewToolCall } from "@/db/schema";

let seq = 0;

function event(type: string, payload: unknown, atMs = 1_800_000_000_000): ResearchEvent {
  seq += 1;
  return {
    seq,
    session_id: "sess-1",
    ts: new Date(atMs).toISOString(),
    message: null,
    type,
    payload,
  } as unknown as ResearchEvent;
}

const METRICS = {
  agent: "research_manager",
  task_id: null,
  model_id: "deepseek:deepseek-v4-pro",
  status: "completed",
  prompt: { hash: "a1b2c3d4e5f60718", chars: 4096 },
  usage: { input: 2419, output: 2395, cached: 2304 },
  cost_usd: 0.004868688,
  duration_ms: 27_549,
  error: null,
};

describe("agent_runs", () => {
  it("把埋点事件铺平成一行", () => {
    const row = toAgentRun("sess-1", event("agent_run_metrics", METRICS));

    expect(row).toMatchObject({
      sessionId: "sess-1",
      agent: "research_manager",
      modelId: "deepseek:deepseek-v4-pro",
      status: "completed",
      promptHash: "a1b2c3d4e5f60718",
      promptChars: 4096,
      tokensIn: 2419,
      tokensOut: 2395,
      tokensCached: 2304,
      durationMs: 27_549,
    });
    expect(row?.costUsd).toBeCloseTo(0.004868688);
  });

  it("规划阶段的 run 没有 task_id", () => {
    // 规划不属于计划里的任何任务，这也是它不能塞进 agent_completed 的原因
    expect(toAgentRun("sess-1", event("agent_run_metrics", METRICS))?.taskId).toBeNull();
  });

  it("缓存命中量单独成列", () => {
    // 命中价仅为未命中的 3%（§9.8），混在 tokensIn 里就无法验证缓存是否生效
    const row = toAgentRun("sess-1", event("agent_run_metrics", METRICS));

    expect(row?.tokensCached).toBe(2304);
    expect(row?.tokensIn).toBe(2419);
  });

  it("定价未知时成本记 null 而不是 0", () => {
    // 记 0 会让「便宜的模型」和「没配价格的模型」在 /debug 里长得一样
    const row = toAgentRun("sess-1", event("agent_run_metrics", { ...METRICS, cost_usd: null }));

    expect(row?.costUsd).toBeNull();
  });

  it("失败的 run 连错误一起记下", () => {
    const row = toAgentRun(
      "sess-1",
      event("agent_run_metrics", {
        ...METRICS,
        status: "failed",
        error: { code: "structured_output", message: "重试用尽" },
      }),
    );

    expect(row?.status).toBe("failed");
    expect(row?.error).toEqual({ code: "structured_output", message: "重试用尽" });
  });

  it("没有 prompt 指纹时留空而不是编一个", () => {
    const row = toAgentRun("sess-1", event("agent_run_metrics", { ...METRICS, prompt: null }));

    expect(row?.promptHash).toBeNull();
    expect(row?.promptChars).toBeNull();
  });

  it("其他事件类型不产生行", () => {
    expect(toAgentRun("sess-1", event("agent_completed", { task_id: "t1" }))).toBeNull();
  });
});

describe("tool_calls 关联", () => {
  function correlate(...events: ResearchEvent[]): NewToolCall[] {
    const correlator = new ToolCallCorrelator();
    return events
      .map((e) => correlator.accept("sess-1", e))
      .filter((row): row is NewToolCall => row !== null);
  }

  const startedAt = 1_800_000_000_000;

  function started(callId = "c1", taskId: string | null = "t1") {
    return event(
      "tool_started",
      {
        call_id: callId,
        tool: "get_tvl",
        agent: "crypto_research",
        task_id: taskId,
        input_summary: { protocol: "hyperliquid" },
      },
      startedAt,
    );
  }

  it("开始事件本身不落库", () => {
    // 一次调用只该有一行；开始时落一行再更新会引入两次写和一次 UPDATE
    expect(correlate(started())).toEqual([]);
  });

  it("完成时合成一行，带上只有开始事件才有的字段", () => {
    // tool_completed 不带 agent / task_id / input，而这几个维度
    // 恰恰是 /debug 里最有用的
    const [row] = correlate(
      started(),
      event(
        "tool_completed",
        {
          call_id: "c1",
          tool: "get_tvl",
          ok: true,
          provider: "defillama",
          cache_hit: true,
          duration_ms: 340,
          result_summary: { tvl: 1.2e9 },
        },
        startedAt + 340,
      ),
    );

    expect(row).toMatchObject({
      tool: "get_tvl",
      agent: "crypto_research",
      taskId: "t1",
      provider: "defillama",
      ok: true,
      cacheHit: true,
      durationMs: 340,
      input: { protocol: "hyperliquid" },
      outputSummary: { tvl: 1.2e9 },
    });
  });

  it("ok=false 的完成事件记为失败", () => {
    const [row] = correlate(
      started(),
      event("tool_completed", {
        call_id: "c1",
        tool: "get_tvl",
        ok: false,
        provider: null,
        cache_hit: false,
        duration_ms: 90,
        result_summary: null,
      }),
    );

    expect(row?.ok).toBe(false);
  });

  it("tool_failed 记下错误码", () => {
    // 「哪个 Tool 最容易失败」要按错误码分组才有意义
    const [row] = correlate(
      started(),
      event(
        "tool_failed",
        { call_id: "c1", tool: "get_tvl", error_code: "rate_limited", message: "429" },
        startedAt + 1200,
      ),
    );

    expect(row).toMatchObject({ ok: false, errorCode: "rate_limited", durationMs: 1200 });
  });

  it("并发的多个调用各自归位", () => {
    // 同一层的任务是并发跑的，工具调用会交错到达
    const rows = correlate(
      started("c1", "t1"),
      started("c2", "t2"),
      event("tool_completed", {
        call_id: "c2",
        tool: "web_search",
        ok: true,
        provider: "tavily",
        cache_hit: false,
        duration_ms: 500,
        result_summary: null,
      }),
      event("tool_completed", {
        call_id: "c1",
        tool: "get_tvl",
        ok: true,
        provider: "defillama",
        cache_hit: false,
        duration_ms: 200,
        result_summary: null,
      }),
    );

    expect(rows.map((row) => row.taskId)).toEqual(["t2", "t1"]);
    expect(rows.map((row) => row.provider)).toEqual(["tavily", "defillama"]);
  });

  it("缺失开始事件时仍落一行，只是维度为空", () => {
    // 断线重连后从中途接事件就会这样。丢掉整行的代价更大：
    // 耗时和失败率统计会凭空少掉一批样本
    const [row] = correlate(
      event("tool_completed", {
        call_id: "unknown",
        tool: "get_tvl",
        ok: true,
        provider: "defillama",
        cache_hit: false,
        duration_ms: 340,
        result_summary: null,
      }),
    );

    expect(row).toMatchObject({ tool: "get_tvl", agent: null, taskId: null, durationMs: 340 });
  });

  it("会话结束时未闭合的调用落成 abandoned", () => {
    // 会话被取消或崩在工具调用中途就会留下这些。不落库的话，
    // 「卡住的工具」在 /debug 里完全不可见——而它最该被发现
    const correlator = new ToolCallCorrelator();
    correlator.accept("sess-1", started("c1"));

    const [row] = correlator.drain("sess-1", startedAt + 30_000);

    expect(row).toMatchObject({ ok: false, errorCode: "abandoned", durationMs: 30_000 });
  });

  it("已闭合的调用不会被 drain 重复落库", () => {
    const correlator = new ToolCallCorrelator();
    correlator.accept("sess-1", started("c1"));
    correlator.accept(
      "sess-1",
      event("tool_completed", {
        call_id: "c1",
        tool: "get_tvl",
        ok: true,
        provider: null,
        cache_hit: false,
        duration_ms: 10,
        result_summary: null,
      }),
    );

    expect(correlator.drain("sess-1", startedAt + 1000)).toEqual([]);
  });
});

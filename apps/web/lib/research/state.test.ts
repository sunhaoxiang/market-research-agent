/**
 * 事件归约（P1-12 验收）。
 *
 * 这里是整个前端唯一有状态机的地方，也是最容易在事件顺序上出错的地方。
 * 测试全部走"逐条喂事件"的方式——和线上真实的输入形态一致。
 */

import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { initialState, reduce, reduceAll } from "@/lib/research/state";

let seq = 0;

function event<T extends ResearchEvent["type"]>(
  type: T,
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

/** 每个用例独立的 seq 计数，避免 hasGap 被上一个用例污染。 */
function script(): (
  type: ResearchEvent["type"],
  payload?: unknown,
  message?: string,
) => ResearchEvent {
  seq = 0;
  return (type, payload = null, message) => event(type, payload, message ?? null);
}

const PLAN = {
  question_type: "crypto",
  interpretation: "用户想了解 Hyperliquid 的协议收入",
  entities: [
    { type: "crypto", symbol: "HYPE", name: "Hyperliquid", chain: null, contract_address: null },
  ],
  tasks: [
    {
      id: "t1",
      agent: "crypto_research",
      objective: "获取手续费收入",
      entities: [],
      suggested_tools: [],
      depends_on: [],
      priority: 0,
    },
    {
      id: "t2",
      agent: "web_research",
      objective: "查找解锁时间表",
      entities: [],
      suggested_tools: [],
      depends_on: ["t1"],
      priority: 1,
    },
  ],
  report_sections: ["Overview"],
  assumptions: [],
};

describe("会话生命周期", () => {
  it("session_started 记录问题、模型与开始时刻", () => {
    const e = script();
    const state = reduce(
      initialState,
      e("session_started", { question: "HYPE 怎么样？", model_id: "deepseek:deepseek-v4-pro" }),
    );

    expect(state.status).toBe("running");
    expect(state.question).toBe("HYPE 怎么样？");
    expect(state.modelId).toBe("deepseek:deepseek-v4-pro");
    expect(state.startedAtMs).toBe(1_800_000_001_000);
  });

  it("session_completed 落下用量、成本与耗时", () => {
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("session_completed", {
        duration_ms: 30_500,
        usage: { input: 1200, output: 800, cached: 1000 },
        cost_usd: 0.0052,
      }),
    ]);

    expect(state.status).toBe("completed");
    expect(state.usage.cached).toBe(1000);
    expect(state.costUsd).toBeCloseTo(0.0052);
    expect(state.durationMs).toBe(30_500);
  });

  it("session_failed 保留失败发生的阶段", () => {
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("session_failed", {
        error: { code: "plan_rejected", message: "计划为空" },
        stage: "planning",
      }),
    ]);

    expect(state.status).toBe("failed");
    expect(state.error?.code).toBe("plan_rejected");
    expect(state.stage).toBe("planning");
  });
});

describe("计划先行（§13.2）", () => {
  it("plan_created 一到就画出完整任务树，全部 pending", () => {
    // 这是消除等待焦虑的关键：用户立刻知道要做什么、要多久
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("plan_created", { plan: PLAN }),
    ]);

    expect(state.taskIds).toEqual(["t1", "t2"]);
    expect(Object.values(state.tasks).map((t) => t.status)).toEqual(["pending", "pending"]);
  });

  it("保留计划里的任务顺序与依赖关系", () => {
    const e = script();
    const state = reduce(initialState, e("plan_created", { plan: PLAN }));

    expect(state.taskIds).toEqual(["t1", "t2"]);
    expect(state.tasks.t2!.dependsOn).toEqual(["t1"]);
    expect(state.tasks.t1!.agent).toBe("crypto_research");
  });

  it("plan_created 里的实体会补上 intent 没给的部分", () => {
    const e = script();
    const state = reduce(initialState, e("plan_created", { plan: PLAN }));

    expect(state.entities.map((entity) => entity.symbol)).toEqual(["HYPE"]);
    expect(state.questionType).toBe("crypto");
  });

  it("plan_updated 追加补充研究的任务而不清掉已有的", () => {
    const e = script();
    const state = reduceAll([
      e("plan_created", { plan: PLAN }),
      e("plan_updated", {
        added_tasks: [{ ...PLAN.tasks[0]!, id: "t3", objective: "补充：竞品对比" }],
        reason: "数据缺口",
      }),
    ]);

    expect(state.taskIds).toEqual(["t1", "t2", "t3"]);
    expect(state.tasks.t3!.status).toBe("pending");
  });
});

describe("任务节点点亮", () => {
  const run = () => {
    const e = script();
    return [
      e("session_started", { question: "Q", model_id: "m" }),
      e("plan_created", { plan: PLAN }),
    ];
  };

  it("agent_started 把节点转为 running 并记下开始时刻", () => {
    const e = script();
    const state = reduceAll([
      ...run(),
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "获取手续费收入",
        model_id: "deepseek:deepseek-v4-flash",
      }),
    ]);

    expect(state.tasks.t1!.status).toBe("running");
    expect(state.tasks.t1!.startedAtMs).not.toBeNull();
    expect(state.tasks.t1!.modelId).toBe("deepseek:deepseek-v4-flash");
    // 同层的另一个任务不受影响
    expect(state.tasks.t2!.status).toBe("pending");
  });

  it("agent_progress 只保留最新一条", () => {
    // §13.3 明确不做滚动日志：activity 面板里堆文本噪音太大
    const e = script();
    const state = reduceAll([
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      e("agent_progress", { agent: "crypto_research", task_id: "t1", message: "第一步" }),
      e("agent_progress", { agent: "crypto_research", task_id: "t1", message: "第二步" }),
    ]);

    expect(state.tasks.t1!.progress).toBe("第二步");
  });

  it("agent_completed 落下摘要与计数", () => {
    const e = script();
    const state = reduceAll([
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      e("agent_completed", {
        agent: "crypto_research",
        task_id: "t1",
        summary: "过去 90 天手续费 3.2 亿",
        claim_count: 5,
        source_count: 3,
        duration_ms: 4200,
      }),
    ]);

    const task = state.tasks.t1!;
    expect(task.status).toBe("completed");
    expect(task.summary).toContain("3.2 亿");
    expect(task.claimCount).toBe(5);
    expect(task.durationMs).toBe(4200);
  });

  it("单个任务失败不影响其他任务", () => {
    const e = script();
    const state = reduceAll([
      ...run(),
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      e("agent_started", { agent: "web_research", task_id: "t2", objective: "o", model_id: "m" }),
      e("agent_failed", {
        agent: "crypto_research",
        task_id: "t1",
        error: { code: "TimeoutError", message: "超时" },
      }),
      e("agent_completed", {
        agent: "web_research",
        task_id: "t2",
        summary: "ok",
        claim_count: 1,
        source_count: 1,
        duration_ms: 100,
      }),
    ]);

    expect(state.tasks.t1!.status).toBe("failed");
    expect(state.tasks.t1!.error?.code).toBe("TimeoutError");
    expect(state.tasks.t2!.status).toBe("completed");
  });

  it("断线重连时先到 agent_started 也能建出节点", () => {
    // P6-6 的既定场景：从中途开始接事件，plan_created 已经错过了
    const e = script();
    const state = reduce(
      initialState,
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t9",
        objective: "o",
        model_id: "m",
      }),
    );

    expect(state.taskIds).toEqual(["t9"]);
    expect(state.tasks.t9!.status).toBe("running");
  });
});

describe("终态时的任务收尾", () => {
  it("从未开始的任务在会话结束后标为 skipped", () => {
    // 执行器对预算/时间耗尽只发一条聚合 warning，不逐个任务通知，
    // 所以"哪些被跳过"只能由前端推断
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("plan_created", { plan: PLAN }),
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      e("agent_completed", {
        agent: "crypto_research",
        task_id: "t1",
        summary: "s",
        claim_count: 0,
        source_count: 0,
        duration_ms: 1,
      }),
      e("session_completed", {
        duration_ms: 1,
        usage: { input: 0, output: 0, cached: 0 },
        cost_usd: null,
      }),
    ]);

    expect(state.tasks.t1!.status).toBe("completed");
    expect(state.tasks.t2!.status).toBe("skipped");
  });

  it("会话被取消时仍在 running 的任务标为 failed 而不是 pending", () => {
    // 显示成 pending 会让用户以为它还在排队
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("plan_created", { plan: PLAN }),
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      e("session_cancelled"),
    ]);

    expect(state.status).toBe("cancelled");
    expect(state.tasks.t1!.status).toBe("failed");
    expect(state.tasks.t2!.status).toBe("skipped");
  });

  it("会话进行中不会提前把 pending 标成 skipped", () => {
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("plan_created", { plan: PLAN }),
    ]);

    expect(state.tasks.t2!.status).toBe("pending");
  });
});

describe("工具调用", () => {
  const started = () => {
    const e = script();
    return [
      e("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
    ];
  };

  it("tool_started 挂到对应任务下", () => {
    const e = script();
    const state = reduceAll([
      ...started(),
      e("tool_started", {
        call_id: "c1",
        tool: "get_tvl",
        agent: "crypto_research",
        task_id: "t1",
        input_summary: {},
      }),
    ]);

    expect(state.tasks.t1!.toolCalls).toHaveLength(1);
    expect(state.tasks.t1!.toolCalls[0]!.status).toBe("running");
  });

  it("tool_completed 按 call_id 找到调用并补上 provider / 缓存命中 / 耗时", () => {
    // tool_completed 不带 task_id（call_id 已经唯一），必须能跨任务定位
    const e = script();
    const state = reduceAll([
      ...started(),
      e("tool_started", {
        call_id: "c1",
        tool: "get_tvl",
        agent: "crypto_research",
        task_id: "t1",
        input_summary: {},
      }),
      e("tool_completed", {
        call_id: "c1",
        tool: "get_tvl",
        ok: true,
        provider: "defillama",
        cache_hit: true,
        duration_ms: 340,
        result_summary: {},
      }),
    ]);

    const call = state.tasks.t1!.toolCalls[0]!;
    expect(call.status).toBe("completed");
    expect(call.provider).toBe("defillama");
    expect(call.cacheHit).toBe(true);
    expect(call.durationMs).toBe(340);
  });

  it("ok=false 的 tool_completed 记为失败", () => {
    const e = script();
    const state = reduceAll([
      ...started(),
      e("tool_started", {
        call_id: "c1",
        tool: "web_fetch",
        agent: "crypto_research",
        task_id: "t1",
        input_summary: {},
      }),
      e("tool_completed", {
        call_id: "c1",
        tool: "web_fetch",
        ok: false,
        provider: null,
        cache_hit: false,
        duration_ms: 90,
        result_summary: {},
      }),
    ]);

    expect(state.tasks.t1!.toolCalls[0]!.status).toBe("failed");
  });

  it("没有对应 tool_started 的完成事件被丢弃而不是造出空节点", () => {
    // 工具名和所属任务都不知道，造一个节点只会在 UI 上显示成一行空白
    const e = script();
    const state = reduceAll([
      ...started(),
      e("tool_completed", {
        call_id: "unknown",
        tool: "x",
        ok: true,
        provider: null,
        cache_hit: false,
        duration_ms: 1,
        result_summary: {},
      }),
    ]);

    expect(state.tasks.t1!.toolCalls).toEqual([]);
  });
});

describe("来源", () => {
  const sample = {
    id: "src-1",
    ref: "s1",
    url: "https://www.theblock.co/post/1?utm_source=x",
    url_canonical: "https://theblock.co/post/1",
    title: "Fee share",
    domain: "theblock.co",
    source_type: "news" as const,
    provider: "tavily",
    reliability: "secondary" as const,
    published_at: null,
    retrieved_at: "2026-09-08T12:00:00.000Z",
    excerpt: "holders",
    citation_index: null,
    http_status: null,
  };

  it("source_found 把来源追加到列表", () => {
    const e = script();
    const state = reduce(
      initialState,
      e("source_found", { source: sample }, "发现来源：Fee share"),
    );

    expect(state.sources).toEqual([sample]);
    expect(state.lastMessage).toBe("发现来源：Fee share");
  });

  it("同一 canonical URL 不重复追加", () => {
    const e = script();
    const state = reduceAll([
      e("source_found", { source: sample }),
      e("source_found", { source: { ...sample, id: "src-2", url: "https://theblock.co/post/1" } }),
    ]);

    expect(state.sources).toHaveLength(1);
    expect(state.sources[0]!.id).toBe("src-1");
  });
});

describe("报告", () => {
  it("report_completed 存下报告并为来源补上 [n]", () => {
    const e = script();
    const numbered = {
      id: "src-1",
      ref: "s1",
      url: "https://theblock.co/post/1",
      url_canonical: "https://theblock.co/post/1",
      title: "Fee share",
      domain: "theblock.co",
      source_type: "news" as const,
      provider: "tavily",
      reliability: "secondary" as const,
      published_at: null,
      retrieved_at: "2026-09-08T12:00:00.000Z",
      excerpt: "holders",
      citation_index: 1,
      http_status: null,
    };
    const report = {
      title: "HYPE 近况",
      executive_summary: "正在讨论手续费分享。[1]",
      sections: [
        {
          id: "Overview",
          title: "概述",
          markdown: "Hyperliquid 正在讨论手续费分享。[1]",
          claim_ids: [],
        },
      ],
      data_gaps: [],
    };
    const state = reduceAll([
      e("source_found", {
        source: { ...numbered, citation_index: null },
      }),
      e("report_completed", { report, sources: [numbered], citation_count: 1 }, "报告已生成"),
    ]);

    expect(state.report?.title).toBe("HYPE 近况");
    expect(state.report?.executive_summary).toContain("[1]");
    expect(state.sources[0]!.citation_index).toBe(1);
  });
});

describe("信封字段", () => {
  it("message 直接可显示，未覆盖的事件类型也能靠它降级", () => {
    // §12.3 的冗余设计：前端不必为每种类型都写文案
    const e = script();
    const state = reduce(
      initialState,
      e("fact_check_started", { claim_count: 3 }, "开始核查 3 条陈述"),
    );

    expect(state.lastMessage).toBe("开始核查 3 条陈述");
  });

  it("心跳不覆盖上一条业务文案", () => {
    // 否则规划阶段 45 秒里，"正在制定研究计划"会被空白心跳顶掉
    const e = script();
    const state = reduceAll([
      e("stage_changed", { stage: "planning", previous: null }, "正在制定研究计划"),
      e("heartbeat"),
    ]);

    expect(state.lastMessage).toBe("正在制定研究计划");
  });

  it("心跳不产生新状态对象，避免无谓重渲染", () => {
    const e = script();
    const before = reduce(initialState, e("session_started", { question: "Q", model_id: "m" }));
    const after = reduce(before, e("heartbeat"));

    expect(after).not.toBe(before); // seq 变了
    expect(after.lastMessage).toBe(before.lastMessage);
  });

  it("seq 出现空洞时置起 hasGap", () => {
    // 漏收事件意味着状态树不完整，UI 应提示刷新而不是装作没事
    const state = reduceAll([
      { ...event("session_started", { question: "Q", model_id: "m" }), seq: 1 },
      { ...event("stage_changed", { stage: "planning", previous: null }), seq: 5 },
    ] as ResearchEvent[]);

    expect(state.hasGap).toBe(true);
  });

  it("连续 seq 不置 hasGap", () => {
    const e = script();
    const state = reduceAll([
      e("session_started", { question: "Q", model_id: "m" }),
      e("stage_changed", { stage: "planning", previous: null }),
      e("plan_created", { plan: PLAN }),
    ]);

    expect(state.hasGap).toBe(false);
  });
});

describe("warning 与用量", () => {
  it("warning 累积而不覆盖", () => {
    const e = script();
    const state = reduceAll([
      e("warning", { code: "plan_repaired", message: "去掉了 1 个悬空依赖" }),
      e("warning", { code: "budget_exhausted", message: "预算耗尽，跳过 2 个任务" }),
    ]);

    expect(state.warnings.map((w) => w.code)).toEqual(["plan_repaired", "budget_exhausted"]);
  });

  it("usage_updated 覆盖式更新（后端给的是累计值）", () => {
    const e = script();
    const state = reduceAll([
      e("usage_updated", { usage: { input: 100, output: 50, cached: 0 }, cost_usd: 0.001 }),
      e("usage_updated", { usage: { input: 300, output: 120, cached: 96 }, cost_usd: 0.003 }),
    ]);

    expect(state.usage).toEqual({ input: 300, output: 120, cached: 96 });
    expect(state.costUsd).toBeCloseTo(0.003);
  });
});

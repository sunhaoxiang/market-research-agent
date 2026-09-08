/**
 * Activity Panel 渲染（P1-12 验收）。
 *
 * reducer 的测试保证状态对，这里保证状态**被画出来**——两者都错过的话，
 * 验收标准「提问后能看到计划与逐节点点亮」就没有任何测试兜着。
 *
 * 断言尽量走可访问名（`aria-label`）而不是 class：§13.2 要求状态不只靠颜色区分，
 * 用可访问名断言等于顺带验证了这条无障碍要求。
 */

import { render, screen } from "@testing-library/react";
import type { ResearchEvent } from "@mra/shared";
import { describe, expect, it } from "vitest";

import { ActivityPanel } from "@/components/research/activity-panel";
import { type ResearchViewState, reduceAll } from "@/lib/research/state";

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
  interpretation: "用户想了解 Hyperliquid 的协议收入",
  entities: [
    { type: "crypto", symbol: "HYPE", name: "Hyperliquid", chain: null, contract_address: null },
  ],
  tasks: [
    {
      id: "t1",
      agent: "crypto_research",
      objective: "获取 Hyperliquid 过去 90 天的手续费收入",
      entities: [],
      suggested_tools: [],
      depends_on: [],
      priority: 0,
    },
    {
      id: "t2",
      agent: "web_research",
      objective: "查找 HYPE 的代币解锁时间表",
      entities: [],
      suggested_tools: [],
      depends_on: ["t1"],
      priority: 1,
    },
  ],
  report_sections: ["Overview"],
  assumptions: [],
};

function stateFrom(...events: ResearchEvent[]): ResearchViewState {
  return reduceAll(events);
}

/** 规划阶段的事件序列。重置 seq，保证每个用例的 hasGap 判断独立。 */
function planningEvents(): ResearchEvent[] {
  seq = 0;
  return [
    event("session_started", { question: "Q", model_id: "deepseek:deepseek-v4-pro" }),
    event("stage_changed", { stage: "planning", previous: null }),
    event("intent_classified", { question_type: "crypto", entities: PLAN.entities }),
    event("plan_created", { plan: PLAN }, "已生成 2 个研究任务"),
    event("stage_changed", { stage: "researching", previous: "planning" }, "正在执行研究任务"),
  ];
}

function panel(state: ResearchViewState, now = 1_800_000_010_000) {
  return render(<ActivityPanel state={state} now={now} />);
}

describe("计划先行", () => {
  it("计划一到就画出全部任务，状态是待执行", () => {
    panel(stateFrom(...planningEvents()));

    expect(screen.getByText("Crypto Research")).toBeDefined();
    expect(screen.getByText("Web Research")).toBeDefined();
    expect(screen.getAllByLabelText("待执行")).toHaveLength(2);
  });

  it("显示识别出的问题类型与实体", () => {
    panel(stateFrom(...planningEvents()));

    // 一行里同时给出类型与实体，用整句断言避免 /crypto/ 也匹配到
    // "Crypto Research" 这个 agent 名
    expect(screen.getByText("识别为 crypto · HYPE")).toBeDefined();
  });

  it("显示任务目标", () => {
    panel(stateFrom(...planningEvents()));

    expect(screen.getByText(/过去 90 天的手续费收入/)).toBeDefined();
  });
});

describe("逐节点点亮", () => {
  it("running 的节点标为进行中，同层其他节点仍待执行", () => {
    const state = stateFrom(
      ...planningEvents(),
      event("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: PLAN.tasks[0]!.objective,
        model_id: "deepseek:deepseek-v4-pro",
      }),
    );

    panel(state);

    expect(screen.getAllByLabelText("进行中")).toHaveLength(1);
    expect(screen.getAllByLabelText("待执行")).toHaveLength(1);
  });

  it("completed 的节点显示陈述与来源数", () => {
    const state = stateFrom(
      ...planningEvents(),
      event("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      event("agent_completed", {
        agent: "crypto_research",
        task_id: "t1",
        summary: "手续费 3.2 亿",
        claim_count: 5,
        source_count: 3,
        duration_ms: 4200,
      }),
    );

    panel(state);

    expect(screen.getByText(/5 条陈述 · 3 来源/)).toBeDefined();
    expect(screen.getByText("4.2s")).toBeDefined();
  });

  it("失败节点展开并显示错误，不被折叠", () => {
    // §13.2 的折叠策略：失败详情正是用户此刻最需要看的
    const state = stateFrom(
      ...planningEvents(),
      event("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      event("agent_failed", {
        agent: "crypto_research",
        task_id: "t1",
        error: { code: "TimeoutError", message: "任务超时" },
      }),
    );

    panel(state);

    expect(screen.getByLabelText("失败")).toBeDefined();
    expect(screen.getByText(/TimeoutError: 任务超时/)).toBeDefined();
  });

  it("running 节点显示实时耗时", () => {
    const state = stateFrom(
      ...planningEvents(),
      event("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
    );
    // 任务在 t+6s 开始，时钟推到 t+14s
    panel(state, 1_800_000_014_000);

    expect(screen.getByText("8.0s")).toBeDefined();
  });

  it("会话结束后未执行的任务显示为已跳过并说明原因", () => {
    const state = stateFrom(
      ...planningEvents(),
      event("session_completed", {
        duration_ms: 1000,
        usage: { input: 0, output: 0, cached: 0 },
        cost_usd: null,
      }),
    );

    panel(state);

    expect(screen.getAllByLabelText("已跳过")).toHaveLength(2);
    expect(screen.getAllByText(/预算或时间耗尽/)).toHaveLength(2);
  });
});

describe("工具调用", () => {
  it("缓存命中的调用带闪电标记与 provider / 耗时", () => {
    const state = stateFrom(
      ...planningEvents(),
      event("agent_started", {
        agent: "crypto_research",
        task_id: "t1",
        objective: "o",
        model_id: "m",
      }),
      event("tool_started", {
        call_id: "c1",
        tool: "get_tvl",
        agent: "crypto_research",
        task_id: "t1",
        input_summary: {},
      }),
      event("tool_completed", {
        call_id: "c1",
        tool: "get_tvl",
        ok: true,
        provider: "defillama",
        cache_hit: true,
        duration_ms: 340,
        result_summary: {},
      }),
    );

    panel(state);

    expect(screen.getByText("get_tvl")).toBeDefined();
    expect(screen.getByLabelText("缓存命中")).toBeDefined();
    expect(screen.getByText(/defillama · 340ms/)).toBeDefined();
  });
});

describe("无障碍与告警", () => {
  it("警告逐条显示而不覆盖", () => {
    const state = stateFrom(
      ...planningEvents(),
      event("warning", { code: "plan_repaired", message: "去掉了 1 个悬空依赖" }),
      event("warning", { code: "budget_exhausted", message: "预算耗尽，跳过 1 个任务" }),
    );

    panel(state);

    expect(screen.getByText("去掉了 1 个悬空依赖")).toBeDefined();
    expect(screen.getByText("预算耗尽，跳过 1 个任务")).toBeDefined();
  });
});

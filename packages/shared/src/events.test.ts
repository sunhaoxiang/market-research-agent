/**
 * 类型层测试：验证判别联合真的能收窄。
 *
 * 这些断言主要在 `tsc` 阶段生效——如果生成的类型退化成 `payload: unknown`
 * 或联合失去判别性，这个文件会编译失败。运行时断言只是让 vitest 有东西可跑。
 */

import { describe, expect, it } from "vitest";

import type { ResearchEvent } from "./generated/types.js";
import { type EventOfType, type PayloadOfType, isTerminalEvent } from "./events.js";

describe("事件判别联合", () => {
  it("switch 分支内 payload 类型被正确收窄", () => {
    const event: ResearchEvent = {
      seq: 1,
      session_id: "s-1",
      ts: new Date().toISOString(),
      message: null,
      type: "tool_started",
      payload: {
        call_id: "c-1",
        tool: "get_tvl",
        agent: "crypto_research",
        task_id: "t1",
        input_summary: "protocol=hyperliquid",
      },
    };

    switch (event.type) {
      case "tool_started": {
        // 若联合失去判别性，下面这行会编译失败
        const tool: string = event.payload.tool;
        expect(tool).toBe("get_tvl");
        break;
      }
      default:
        throw new Error("判别失败：未进入 tool_started 分支");
    }
  });

  it("EventOfType / PayloadOfType 能取出精确类型", () => {
    const payload: PayloadOfType<"agent_completed"> = {
      agent: "stock_research",
      task_id: "t2",
      summary: "已获取 NVDA 最近四个季度财报",
      claim_count: 7,
      source_count: 3,
      duration_ms: 4200,
    };

    const event: EventOfType<"agent_completed"> = {
      seq: 9,
      session_id: "s-1",
      ts: new Date().toISOString(),
      message: null,
      type: "agent_completed",
      payload,
    };

    expect(event.payload.claim_count).toBe(7);
  });

  it("终止事件能被识别", () => {
    const completed: ResearchEvent = {
      seq: 99,
      session_id: "s-1",
      ts: new Date().toISOString(),
      message: null,
      type: "session_completed",
      payload: { duration_ms: 1000, usage: { input: 10, output: 5, cached: 0 }, cost_usd: 0.01 },
    };
    const heartbeat: ResearchEvent = {
      seq: 50,
      session_id: "s-1",
      ts: new Date().toISOString(),
      message: null,
      type: "heartbeat",
      payload: null,
    };

    expect(isTerminalEvent(completed)).toBe(true);
    expect(isTerminalEvent(heartbeat)).toBe(false);
  });
});

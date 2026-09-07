/**
 * 结构化指标落库（§20.1 第二条数据线，P1-13）。
 *
 * 与 `research_events` 的分工：事件流回答"这次研究发生了什么"，
 * 这两张表回答"多慢、多贵、哪个工具在失败、缓存命中率多少"。后者是 `/debug`
 * 页面与跨模型 eval 的唯一数据源——SDK tracing 只覆盖 LLM 交互细节，
 * 给不了按 provider 聚合的缓存命中率，也算不出国内模型的分时计价。
 *
 * 数据全部来自事件流：决策 C 规定 Python 不碰业务数据库（§4），
 * 所以这些行只能由 Next 侧在消费 SSE 时写出来。
 */

import "server-only";

import type { ResearchEvent } from "@mra/shared";

import type { NewAgentRun, NewToolCall } from "@/db/schema";
import { newId } from "@/lib/ids";

/** `agent_run_metrics` 事件 → `agent_runs` 一行。 */
export function toAgentRun(sessionId: string, event: ResearchEvent): NewAgentRun | null {
  if (event.type !== "agent_run_metrics") return null;
  const { payload } = event;

  return {
    id: newId(),
    sessionId,
    taskId: payload.task_id,
    agent: payload.agent,
    modelId: payload.model_id,
    status: payload.status,
    // prompt 只存 hash 与长度，绝不存全文（§20.3）
    promptHash: payload.prompt?.hash ?? null,
    promptChars: payload.prompt?.chars ?? null,
    tokensIn: payload.usage.input,
    tokensOut: payload.usage.output,
    tokensCached: payload.usage.cached,
    costUsd: payload.cost_usd,
    durationMs: payload.duration_ms,
    error: payload.error,
    createdAt: Date.parse(event.ts),
  };
}

/**
 * 把 `tool_started` 与 `tool_completed` / `tool_failed` 合成 `tool_calls` 一行。
 *
 * 必须关联而不能只看完成事件：`tool_completed` 不带 `agent` / `task_id`
 * （`call_id` 已经唯一），而"哪个 Agent 的哪个任务在调这个工具"恰恰是
 * `/debug` 里最有用的维度。输入摘要同理只在 `tool_started` 里。
 */
export class ToolCallCorrelator {
  private readonly open = new Map<
    string,
    { tool: string; agent: string | null; taskId: string | null; input: unknown; at: number }
  >();

  /** 喂一条事件。返回可落库的行；尚未闭合时返回 null。 */
  accept(sessionId: string, event: ResearchEvent): NewToolCall | null {
    if (event.type === "tool_started") {
      this.open.set(event.payload.call_id, {
        tool: event.payload.tool,
        agent: event.payload.agent,
        taskId: event.payload.task_id,
        input: event.payload.input_summary,
        at: Date.parse(event.ts),
      });
      return null;
    }

    if (event.type === "tool_completed") {
      const started = this.open.get(event.payload.call_id);
      this.open.delete(event.payload.call_id);
      return {
        id: newId(),
        sessionId,
        taskId: started?.taskId ?? null,
        agent: started?.agent ?? null,
        tool: event.payload.tool,
        provider: event.payload.provider,
        input: started?.input ?? null,
        outputSummary: event.payload.result_summary,
        ok: event.payload.ok,
        errorCode: null,
        cacheHit: event.payload.cache_hit,
        durationMs: event.payload.duration_ms,
        createdAt: started?.at ?? Date.parse(event.ts),
      };
    }

    if (event.type === "tool_failed") {
      const started = this.open.get(event.payload.call_id);
      this.open.delete(event.payload.call_id);
      const at = Date.parse(event.ts);
      return {
        id: newId(),
        sessionId,
        taskId: started?.taskId ?? null,
        agent: started?.agent ?? null,
        tool: event.payload.tool,
        provider: null,
        input: started?.input ?? null,
        outputSummary: null,
        ok: false,
        errorCode: event.payload.error_code,
        cacheHit: false,
        // 没有 tool_started 时算不出耗时。记 0 而不是猜一个值：
        // 0 在 p95 排行里显眼且不会污染统计，编出来的数字则会
        durationMs: started ? at - started.at : 0,
        createdAt: started?.at ?? at,
      };
    }

    return null;
  }

  /**
   * 会话结束时仍未闭合的调用。
   *
   * 会话被取消或崩在工具调用中途就会留下这些。落成 `ok=false` 的行，
   * 否则"卡住的工具"在 `/debug` 里完全不可见——而它正是最该被发现的一类问题。
   */
  drain(sessionId: string, endedAt: number): NewToolCall[] {
    const rows = [...this.open].map(([, started]) => ({
      id: newId(),
      sessionId,
      taskId: started.taskId,
      agent: started.agent,
      tool: started.tool,
      provider: null,
      input: started.input,
      outputSummary: null,
      ok: false,
      errorCode: "abandoned",
      cacheHit: false,
      durationMs: endedAt - started.at,
      createdAt: started.at,
    }));
    this.open.clear();
    return rows;
  }
}

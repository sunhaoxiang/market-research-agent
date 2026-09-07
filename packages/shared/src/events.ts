/**
 * 事件协议的辅助类型。
 *
 * 这些是从生成类型派生的工具类型，不是新定义——所以不会与 Pydantic 真源漂移。
 * 主要服务于前端的事件 reducer：`switch (event.type)` 分支里能拿到精确的 payload 类型。
 */

import type { ResearchEvent } from "./generated/types.js";

/** 所有事件类型的字符串联合，等价于 Python 侧的 `EventType` 枚举。 */
export type EventType = ResearchEvent["type"];

/**
 * 按 type 取出对应的事件类型。
 *
 * @example
 * function onToolStarted(event: EventOfType<"tool_started">) {
 *   event.payload.tool; // 类型安全，无需断言
 * }
 */
export type EventOfType<T extends EventType> = Extract<ResearchEvent, { type: T }>;

/** 按 type 取出对应的 payload 类型。 */
export type PayloadOfType<T extends EventType> = EventOfType<T>["payload"];

/** 收到这些事件后事件流结束，前端可关闭连接。与 Python 侧 `TERMINAL_EVENT_TYPES` 对应。 */
export const TERMINAL_EVENT_TYPES = [
  "session_completed",
  "session_failed",
  "session_cancelled",
] as const satisfies readonly EventType[];

export function isTerminalEvent(event: ResearchEvent): boolean {
  return (TERMINAL_EVENT_TYPES as readonly string[]).includes(event.type);
}

/**
 * 事件落库缓冲（§10.3，P1-11）。
 *
 * 事件流是高频的（每次研究几十到几百条），逐条一个事务意味着逐条 fsync——
 * SQLite 在 WAL 下单次提交约 0.1-1ms，几百条就是几百毫秒的纯 IO，
 * 而且会和转发给浏览器的路径抢同一个事件循环。
 *
 * 攒批的代价是「崩溃时丢掉最后一批」。可以接受：事件是过程记录，
 * 真正要紧的终态与报告走各自的单独事务（§11.2 步骤 5）。
 */

import "server-only";

import type { ResearchEvent } from "@mra/shared";

import type { Db } from "@/db/client";
import { appendEvents } from "@/db/queries/sessions";
import type { NewResearchEvent } from "@/db/schema";
import { newId } from "@/lib/ids";

export const FLUSH_EVERY_EVENTS = 20;
export const FLUSH_EVERY_MS = 200;

/** 从事件 payload 里抽出用于筛选的列。 */
function denormalize(event: ResearchEvent): Pick<NewResearchEvent, "agent" | "tool" | "taskId"> {
  // payload 是判别联合，各类型字段不同；这里只取存在的那几个，
  // 目的是让「某个 agent 的全部事件」这类查询不必解析 JSON
  const payload: Record<string, unknown> = (event.payload ?? {}) as Record<string, unknown>;
  const pick = (key: string): string | null =>
    typeof payload[key] === "string" ? (payload[key] as string) : null;

  return { agent: pick("agent"), tool: pick("tool"), taskId: pick("task_id") };
}

export function toRow(sessionId: string, event: ResearchEvent): NewResearchEvent {
  return {
    id: newId(),
    sessionId,
    seq: event.seq,
    type: event.type,
    message: event.message ?? null,
    // 存完整 payload：事件协议会演进，而历史会话要能用新代码重新渲染（§12.1）
    payload: event.payload ?? null,
    createdAt: Date.parse(event.ts),
    ...denormalize(event),
  };
}

/**
 * 按条数或时间攒批写入。
 *
 * 时间阈值和条数阈值都要有：只按条数的话，一个 45 秒的规划阶段结束前
 * 什么都不会落库，此时崩溃就丢掉整个规划过程。
 */
export class EventBuffer {
  private pending: NewResearchEvent[] = [];
  private lastFlushAt = Date.now();

  constructor(
    private readonly db: Db,
    private readonly sessionId: string,
  ) {}

  add(event: ResearchEvent): void {
    this.pending.push(toRow(this.sessionId, event));

    const full = this.pending.length >= FLUSH_EVERY_EVENTS;
    const stale = Date.now() - this.lastFlushAt >= FLUSH_EVERY_MS;
    if (full || stale) this.flush();
  }

  flush(): void {
    if (this.pending.length === 0) return;
    const batch = this.pending;
    // 先清空再写：写失败时不能把这批留在缓冲里，否则下一次 flush 会重试同一批
    // 并再次撞上 (session_id, seq) 唯一索引，一条坏数据能卡死整条流
    this.pending = [];
    this.lastFlushAt = Date.now();
    appendEvents(this.db, batch);
  }
}

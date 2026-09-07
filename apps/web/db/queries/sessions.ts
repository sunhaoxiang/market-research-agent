/**
 * research_session 相关查询。
 *
 * 所有 DB 访问都收敛到 db/queries/ 下，便于将来迁移 PostgreSQL 时集中修改（§25.3）。
 */

import { desc, eq, sql } from "drizzle-orm";

import type { Db } from "@/db/client";
import {
  type NewResearchEvent,
  type NewResearchSession,
  type ResearchSession,
  type SessionStatus,
  researchEvents,
  researchSessions,
} from "@/db/schema";

export const LOCAL_USER_ID = "local";

export function createSession(db: Db, input: NewResearchSession): ResearchSession {
  const [row] = db.insert(researchSessions).values(input).returning().all();
  if (!row) {
    throw new Error("创建 research_session 失败");
  }
  return row;
}

export function getSession(db: Db, id: string): ResearchSession | undefined {
  return db.select().from(researchSessions).where(eq(researchSessions.id, id)).get();
}

export function listSessions(
  db: Db,
  { userId = LOCAL_USER_ID, limit = 50, offset = 0 }: ListSessionsOptions = {},
): ResearchSession[] {
  return db
    .select()
    .from(researchSessions)
    .where(eq(researchSessions.userId, userId))
    .orderBy(desc(researchSessions.createdAt))
    .limit(limit)
    .offset(offset)
    .all();
}

export type ListSessionsOptions = {
  userId?: string;
  limit?: number;
  offset?: number;
};

export function updateSessionStatus(
  db: Db,
  id: string,
  status: SessionStatus,
  patch: Partial<
    Pick<ResearchSession, "completedAt" | "durationMs" | "error" | "costUsd" | "tokenUsage">
  > = {},
): void {
  db.update(researchSessions)
    .set({ status, ...patch })
    .where(eq(researchSessions.id, id))
    .run();
}

/**
 * 更新研究过程中才知道的会话级字段。
 *
 * 与 `updateSessionStatus` 分开：状态机的推进和"补充元数据"是两件事，
 * 混在一个函数里会让调用方为了写 plan 而必须传一个状态，进而出现
 * 用当前状态覆盖当前状态这种无意义的写。
 */
export function patchSession(
  db: Db,
  id: string,
  patch: Partial<Pick<ResearchSession, "questionType" | "plan" | "modelId" | "tokenUsage">>,
): void {
  db.update(researchSessions).set(patch).where(eq(researchSessions.id, id)).run();
}

/**
 * 批量写入事件。
 *
 * 单事务插入，避免逐条事务的 fsync 开销（§10.3）。事件流按 200ms 或 20 条触发。
 */
export function appendEvents(db: Db, events: NewResearchEvent[]): void {
  if (events.length === 0) return;
  db.transaction((tx) => {
    tx.insert(researchEvents).values(events).run();
  });
}

export function listEvents(db: Db, sessionId: string, afterSeq = -1) {
  return db
    .select()
    .from(researchEvents)
    .where(sql`${researchEvents.sessionId} = ${sessionId} AND ${researchEvents.seq} > ${afterSeq}`)
    .orderBy(researchEvents.seq)
    .all();
}

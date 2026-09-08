/**
 * 历史事件回放（刷新页面后重建 Source Panel / 报告）。
 *
 * 浏览器只持有内存里的 reducer 状态；关掉标签页就没了。事件已经按序
 * 落在 `research_events`，从这里读出来再 `reduceAll` 就能还原现场。
 */

import type { ResearchEvent } from "@mra/shared";

import { getDb } from "@/db/client";
import { toClientEvent } from "@/db/queries/events";
import { getSession, listEvents } from "@/db/queries/sessions";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!id) {
    return Response.json({ error: { code: "NOT_FOUND", message: "会话不存在" } }, { status: 404 });
  }

  const db = getDb();
  const session = getSession(db, id);
  if (!session) {
    return Response.json({ error: { code: "NOT_FOUND", message: "会话不存在" } }, { status: 404 });
  }

  const afterRaw = new URL(request.url).searchParams.get("after");
  const afterSeq = afterRaw === null || afterRaw === "" ? -1 : Number(afterRaw);
  const after = Number.isFinite(afterSeq) ? afterSeq : -1;

  const events: ResearchEvent[] = listEvents(db, id, after).map(toClientEvent);
  return Response.json({ events });
}

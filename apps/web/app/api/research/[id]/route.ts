/**
 * 单次会话：取消进行中的研究（P6-7 / §16.1）。
 *
 * 已完成的会话不在这里删除——历史列表要能翻到。取消只作用于
 * `pending|planning|researching|checking|writing`。
 */

import { getDb } from "@/db/client";
import { getSession } from "@/db/queries/sessions";
import { isActiveSessionStatus } from "@/db/schema";
import { cancelResearch } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

export async function DELETE(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!id) {
    return Response.json({ error: { code: "NOT_FOUND", message: "会话不存在" } }, { status: 404 });
  }

  const session = getSession(getDb(), id);
  if (!session) {
    return Response.json({ error: { code: "NOT_FOUND", message: "会话不存在" } }, { status: 404 });
  }
  if (!isActiveSessionStatus(session.status)) {
    return Response.json(
      { error: { code: "NOT_RUNNING", message: "研究已经结束，无法取消" } },
      { status: 409 },
    );
  }

  const result = await cancelResearch(id);
  if (!result.ok) {
    const status = result.error.code === "NOT_RUNNING" ? 409 : 503;
    return Response.json({ error: result.error }, { status });
  }

  return Response.json({ cancelled: true });
}

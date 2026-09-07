/**
 * 模型目录（P1-5 / §9.3）。
 *
 * 纯转发 Agent 服务的 `/v1/models`——模型目录的真源在 Python 侧，
 * 这里只负责让浏览器不直连 Agent 服务（§11.1 职责边界）。
 */

import { fetchAgentModels } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

export async function GET() {
  const result = await fetchAgentModels();

  if (!result.ok) {
    return Response.json({ error: result.error }, { status: 503 });
  }

  return Response.json(result.data);
}

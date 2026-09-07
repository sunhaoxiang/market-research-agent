import { sql } from "drizzle-orm";

import { getDb } from "@/db/client";
import { fetchAgentHealth } from "@/lib/agent-client";

export const dynamic = "force-dynamic";

type ComponentHealth = {
  ok: boolean;
  detail?: string;
};

function checkDatabase(): ComponentHealth {
  try {
    // 同时验证：连接可用 + migration 已执行
    const db = getDb();
    db.get(sql`SELECT 1`);
    const table = db.get<{ name: string }>(
      sql`SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'research_sessions'`,
    );
    return table ? { ok: true } : { ok: false, detail: "migration 未执行，请运行 pnpm db:migrate" };
  } catch (error) {
    return { ok: false, detail: error instanceof Error ? error.message : "未知数据库错误" };
  }
}

export async function GET() {
  const [database, agent] = await Promise.all([
    Promise.resolve(checkDatabase()),
    fetchAgentHealth(),
  ]);

  const agentHealth: ComponentHealth = agent.ok
    ? { ok: agent.data.status === "ok", detail: agent.data.status }
    : { ok: false, detail: agent.error.message };

  const allOk = database.ok && agentHealth.ok;

  return Response.json(
    {
      status: allOk ? "ok" : "degraded",
      components: {
        database,
        agentService: agentHealth,
      },
      agent: agent.ok ? agent.data : null,
    },
    { status: allOk ? 200 : 503 },
  );
}

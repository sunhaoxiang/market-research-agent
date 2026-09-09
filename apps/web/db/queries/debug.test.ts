// @vitest-environment node

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { beforeEach, describe, expect, it } from "vitest";

import { loadDebugSnapshot } from "@/db/queries/debug";
import { createSession } from "@/db/queries/sessions";
import * as schema from "@/db/schema";

type Db = ReturnType<typeof drizzle<typeof schema>>;
let db: Db;
const T0 = 1_800_000_000_000;

beforeEach(() => {
  const raw = new Database(":memory:");
  raw.pragma("foreign_keys = ON");
  db = drizzle(raw, { schema });
  migrate(db, { migrationsFolder: "./db/migrations" });
});

function session(id: string, createdAt = T0) {
  return createSession(db, {
    id,
    question: `问题 ${id}`,
    modelId: "deepseek:deepseek-v4-pro",
    status: "completed",
    createdAt,
    startedAt: createdAt,
    completedAt: createdAt + 30_000,
    durationMs: 30_000,
    costUsd: 0.02,
  });
}

describe("loadDebugSnapshot", () => {
  it("空库时各项为空，不编造 0% 失败率", () => {
    const snap = loadDebugSnapshot(db);
    expect(snap.totals.sessions).toBe(0);
    expect(snap.recentSessions).toEqual([]);
    expect(snap.agents).toEqual([]);
    expect(snap.tools).toEqual([]);
    expect(snap.models).toEqual([]);
  });

  it("从 stage_changed 还原各阶段耗时", () => {
    session("s1");
    db.insert(schema.researchEvents)
      .values([
        event("s1", 1, "session_started", { question: "Q", model_id: "m" }, T0),
        event("s1", 2, "stage_changed", { stage: "planning", previous: null }, T0),
        event(
          "s1",
          3,
          "stage_changed",
          { stage: "researching", previous: "planning" },
          T0 + 10_000,
        ),
        event("s1", 4, "session_completed", { duration_ms: 30_000 }, T0 + 30_000),
      ])
      .run();

    const [row] = loadDebugSnapshot(db).recentSessions;
    expect(row?.stages.map((span) => [span.stage, span.durationMs])).toEqual([
      ["planning", 10_000],
      ["researching", 20_000],
    ]);
    expect(loadDebugSnapshot(db).stageAverages[0]?.stage).toBe("researching");
  });

  it("Agent 按总耗时排行，失败单独计数", () => {
    session("s1");
    db.insert(schema.agentRuns)
      .values([
        run("s1", "report_writer", 1_000, 0.01, "completed"),
        run("s1", "research_manager", 20_000, 0.05, "completed"),
        run("s1", "research_manager", 5_000, 0.01, "failed"),
      ])
      .run();

    const agents = loadDebugSnapshot(db).agents;
    expect(agents.map((row) => row.agent)).toEqual(["research_manager", "report_writer"]);
    expect(agents[0]).toMatchObject({ runs: 2, failed: 1, durationMs: 25_000 });
  });

  it("Tool 给出失败率与 p95", () => {
    session("s1");
    db.insert(schema.toolCalls)
      .values([
        call("s1", "get_tvl", true, 100),
        call("s1", "get_tvl", false, 900, "timeout"),
        call("s1", "web_search", true, 200),
        call("s1", "web_search", true, 220),
      ])
      .run();

    const tools = loadDebugSnapshot(db).tools;
    expect(tools[0]).toMatchObject({
      tool: "get_tvl",
      calls: 2,
      failures: 1,
      failureRate: 0.5,
      p95Ms: 900,
      errorCodes: { timeout: 1 },
    });
    expect(tools[1]?.tool).toBe("web_search");
    expect(tools[1]?.failureRate).toBe(0);
  });

  it("模型按成本聚合", () => {
    session("s1");
    db.insert(schema.agentRuns)
      .values([
        run("s1", "research_manager", 1000, 0.08, "completed", "deepseek:deepseek-v4-pro"),
        run("s1", "report_writer", 1000, 0.01, "completed", "deepseek:deepseek-v4-flash"),
      ])
      .run();

    expect(loadDebugSnapshot(db).models.map((row) => row.modelId)).toEqual([
      "deepseek:deepseek-v4-pro",
      "deepseek:deepseek-v4-flash",
    ]);
  });
});

function event(
  sessionId: string,
  seq: number,
  type: string,
  payload: unknown,
  at: number,
): schema.NewResearchEvent {
  return {
    id: crypto.randomUUID(),
    sessionId,
    seq,
    type,
    payload,
    createdAt: at,
  };
}

function run(
  sessionId: string,
  agent: schema.AgentName,
  durationMs: number,
  costUsd: number,
  status: string,
  modelId = "deepseek:deepseek-v4-pro",
): schema.NewAgentRun {
  return {
    id: crypto.randomUUID(),
    sessionId,
    agent,
    modelId,
    status,
    durationMs,
    costUsd,
    tokensIn: 100,
    tokensOut: 50,
    tokensCached: 0,
    createdAt: T0,
  };
}

function call(
  sessionId: string,
  tool: string,
  ok: boolean,
  durationMs: number,
  errorCode: string | null = null,
): schema.NewToolCall {
  return {
    id: crypto.randomUUID(),
    sessionId,
    tool,
    ok,
    cacheHit: false,
    durationMs,
    errorCode,
    createdAt: T0,
  };
}

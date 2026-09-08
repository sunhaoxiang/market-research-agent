/**
 * Schema 与 migration 测试。
 *
 * 用内存 SQLite + 真实 migration SQL，验证：
 *   - migration 能从零建库
 *   - CHECK 约束、外键、唯一约束真的生效（不只是 TS 类型层面）
 *   - JSON / boolean 列往返正确
 *   - 事件批量写入是事务性的
 */

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { beforeEach, describe, expect, it } from "vitest";

import * as schema from "./schema";
import { appendEvents, createSession, listEvents, listSessions } from "./queries/sessions";

type TestDb = ReturnType<typeof drizzle<typeof schema>>;

let raw: Database.Database;
let db: TestDb;

function newSession(overrides: Partial<schema.NewResearchSession> = {}): schema.NewResearchSession {
  return {
    id: crypto.randomUUID(),
    question: "介绍一下 HYPE 这个项目",
    modelId: "openai:gpt-5.6-terra",
    createdAt: Date.now(),
    ...overrides,
  };
}

beforeEach(() => {
  raw = new Database(":memory:");
  raw.pragma("foreign_keys = ON");
  db = drizzle(raw, { schema });
  migrate(db, { migrationsFolder: "./db/migrations" });
});

describe("migration", () => {
  it("从零建出全部 10 张表", () => {
    const tables = raw
      .prepare<[], { name: string }>(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '__drizzle%'",
      )
      .all()
      .map((r) => r.name)
      .sort();

    expect(tables).toEqual([
      "agent_runs",
      "claim_sources",
      "claims",
      "research_events",
      "research_reports",
      "research_sessions",
      "settings",
      "sources",
      "tool_calls",
      "watchlist",
    ]);
  });
});

describe("research_sessions", () => {
  it("user_id 默认为 local，支持未来多用户", () => {
    const session = createSession(db, newSession());
    expect(session.userId).toBe("local");
    expect(session.status).toBe("pending");
  });

  it("JSON 列往返保持结构", () => {
    const snapshot: schema.ModelSnapshot = {
      modelId: "openai:gpt-5.6-terra",
      provider: "openai",
      upstreamModel: "gpt-5.6-terra",
      adapter: "openai_responses",
      temperature: 0.2,
      capabilities: { structured_output: "native_schema", context_window: 400_000 },
    };
    const created = createSession(db, newSession({ modelSnapshot: snapshot }));
    const loaded = db.select().from(schema.researchSessions).all()[0];

    expect(loaded?.modelSnapshot).toEqual(snapshot);
    expect(created.id).toBe(loaded?.id);
  });

  it("拒绝非法 status（CHECK 约束在 DB 层生效）", () => {
    const id = crypto.randomUUID();
    expect(() =>
      raw
        .prepare(
          "INSERT INTO research_sessions (id, question, status, model_id, created_at) VALUES (?, ?, ?, ?, ?)",
        )
        .run(id, "q", "not_a_real_status", "m", Date.now()),
    ).toThrow(/CHECK constraint failed/);
  });

  it("按 created_at 倒序返回列表", () => {
    const base = Date.now();
    createSession(db, newSession({ question: "旧", createdAt: base - 1000 }));
    createSession(db, newSession({ question: "新", createdAt: base }));

    expect(listSessions(db).map((s) => s.question)).toEqual(["新", "旧"]);
  });

  it("按状态过滤", () => {
    createSession(db, newSession({ question: "完成", status: "completed" }));
    createSession(db, newSession({ question: "失败", status: "failed" }));
    expect(listSessions(db, { status: "failed" }).map((s) => s.question)).toEqual(["失败"]);
  });
});

describe("research_events", () => {
  it("批量写入后可按 seq 顺序读取", () => {
    const session = createSession(db, newSession());
    appendEvents(
      db,
      [2, 0, 1].map((seq) => ({
        id: crypto.randomUUID(),
        sessionId: session.id,
        seq,
        type: "agent_started",
        createdAt: Date.now(),
        payload: { agent: "crypto_research" },
      })),
    );

    const events = listEvents(db, session.id);
    expect(events.map((e) => e.seq)).toEqual([0, 1, 2]);
    expect(events[0]?.payload).toEqual({ agent: "crypto_research" });
  });

  it("同一 session 内 seq 唯一", () => {
    const session = createSession(db, newSession());
    const event = { sessionId: session.id, seq: 0, type: "x", createdAt: Date.now() };

    appendEvents(db, [{ ...event, id: crypto.randomUUID() }]);
    expect(() => appendEvents(db, [{ ...event, id: crypto.randomUUID() }])).toThrow(/UNIQUE/);
  });

  it("批量写入是事务性的：一条失败则整批回滚", () => {
    const session = createSession(db, newSession());
    appendEvents(db, [
      { id: crypto.randomUUID(), sessionId: session.id, seq: 0, type: "a", createdAt: 1 },
    ]);

    // 第二条与已有 seq 冲突，整批（含 seq=1 那条）都不应写入
    expect(() =>
      appendEvents(db, [
        { id: crypto.randomUUID(), sessionId: session.id, seq: 1, type: "b", createdAt: 2 },
        { id: crypto.randomUUID(), sessionId: session.id, seq: 0, type: "c", createdAt: 3 },
      ]),
    ).toThrow();

    expect(listEvents(db, session.id)).toHaveLength(1);
  });

  it("外键级联：删除 session 一并清理事件", () => {
    const session = createSession(db, newSession());
    appendEvents(db, [
      { id: crypto.randomUUID(), sessionId: session.id, seq: 0, type: "a", createdAt: 1 },
    ]);

    raw.prepare("DELETE FROM research_sessions WHERE id = ?").run(session.id);
    expect(listEvents(db, session.id)).toHaveLength(0);
  });

  it("拒绝引用不存在的 session", () => {
    expect(() =>
      appendEvents(db, [
        { id: crypto.randomUUID(), sessionId: "does-not-exist", seq: 0, type: "a", createdAt: 1 },
      ]),
    ).toThrow(/FOREIGN KEY/);
  });
});

describe("claims 与 sources", () => {
  it("epistemic_type 受 CHECK 约束限制", () => {
    const session = createSession(db, newSession());
    expect(() =>
      raw
        .prepare(
          "INSERT INTO claims (id, session_id, text, epistemic_type, confidence, created_at) VALUES (?,?,?,?,?,?)",
        )
        .run(crypto.randomUUID(), session.id, "t", "wild_guess", "high", Date.now()),
    ).toThrow(/CHECK constraint failed/);
  });

  it("同一 session 内相同 canonical URL 只能存一条（去重保证）", () => {
    const session = createSession(db, newSession());
    const source = {
      sessionId: session.id,
      url: "https://defillama.com/protocol/hyperliquid",
      urlCanonical: "https://defillama.com/protocol/hyperliquid",
      sourceType: "api" as const,
      retrievedAt: Date.now(),
    };

    db.insert(schema.sources)
      .values({ ...source, id: crypto.randomUUID() })
      .run();
    expect(() =>
      db
        .insert(schema.sources)
        .values({ ...source, id: crypto.randomUUID() })
        .run(),
    ).toThrow(/UNIQUE/);
  });
});

describe("tool_calls", () => {
  it("boolean 列以 integer 存储但读取为 boolean", () => {
    const session = createSession(db, newSession());
    db.insert(schema.toolCalls)
      .values({
        id: crypto.randomUUID(),
        sessionId: session.id,
        tool: "get_tvl",
        provider: "defillama",
        ok: true,
        cacheHit: false,
        durationMs: 340,
        createdAt: Date.now(),
      })
      .run();

    const row = db.select().from(schema.toolCalls).all()[0];
    expect(row?.ok).toBe(true);
    expect(row?.cacheHit).toBe(false);

    const stored = raw.prepare<[], { ok: number }>("SELECT ok FROM tool_calls").get();
    expect(stored?.ok).toBe(1);
  });
});

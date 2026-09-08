/**
 * @vitest-environment node
 *
 * SOURCE_FOUND / REPORT_COMPLETED 投影到 sources / claims / reports。
 * 刷新后的 Source Panel 靠事件回放；这两张表是同一条数据的物化，必须对得上。
 */

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { beforeEach, describe, expect, it } from "vitest";

import type { ResearchEvent } from "@mra/shared";

import { projectArtifacts } from "@/db/queries/artifacts";
import { toClientEvent, toRow } from "@/db/queries/events";
import { createSession, listEvents } from "@/db/queries/sessions";
import * as schema from "@/db/schema";
import { reduceAll } from "@/lib/research/state";

type TestDb = ReturnType<typeof drizzle<typeof schema>>;

let db: TestDb;
let sessionId: string;

const SOURCE = {
  id: "src-1",
  ref: "s1",
  url: "https://theblock.co/post/1",
  url_canonical: "https://theblock.co/post/1",
  title: "Fee share",
  domain: "theblock.co",
  source_type: "news" as const,
  provider: "tavily",
  reliability: "secondary" as const,
  published_at: null,
  retrieved_at: "2026-09-08T12:00:00.000Z",
  excerpt: "holders",
  citation_index: null as number | null,
  http_status: 200,
};

function event(type: string, payload: unknown, seq = 1): ResearchEvent {
  return {
    seq,
    session_id: sessionId,
    ts: new Date(1_800_000_000_000 + seq).toISOString(),
    message: null,
    type,
    payload,
  } as unknown as ResearchEvent;
}

beforeEach(() => {
  const raw = new Database(":memory:");
  raw.pragma("foreign_keys = ON");
  db = drizzle(raw, { schema });
  migrate(db, { migrationsFolder: "./db/migrations" });
  const session = createSession(db, {
    id: crypto.randomUUID(),
    question: "HYPE 近况",
    modelId: "deepseek:deepseek-v4-pro",
    createdAt: Date.now(),
  });
  sessionId = session.id;
});

describe("projectArtifacts", () => {
  it("source_found 写入 sources，report_completed 补 [n] 并落 claims", () => {
    projectArtifacts(db, sessionId, event("source_found", { source: SOURCE }, 1));
    projectArtifacts(
      db,
      sessionId,
      event(
        "report_completed",
        {
          report: {
            title: "HYPE 近况",
            executive_summary: "正在讨论手续费分享。[1]",
            sections: [
              {
                id: "Overview",
                title: "概述",
                markdown: "Hyperliquid 正在讨论手续费分享。[1]",
                claim_ids: ["c1"],
              },
            ],
            data_gaps: ["未找到官方解锁时间表"],
          },
          sources: [{ ...SOURCE, citation_index: 1 }],
          claims: [
            {
              id: "c1",
              text: "正在讨论手续费分享。",
              epistemic_type: "source_backed_fact",
              confidence: "medium",
              source_ids: ["src-1"],
              as_of: null,
              task_id: "t1",
              agent: "web_research",
              verification: "unverified",
              verification_note: null,
              citation_index: null,
            },
          ],
          citation_count: 1,
        },
        2,
      ),
    );

    const storedSources = db.select().from(schema.sources).all();
    expect(storedSources).toHaveLength(1);
    expect(storedSources[0]).toMatchObject({
      id: "src-1",
      urlCanonical: "https://theblock.co/post/1",
      citationIndex: 1,
      sourceType: "news",
    });

    const storedClaims = db.select().from(schema.claims).all();
    expect(storedClaims).toHaveLength(1);
    expect(storedClaims[0]).toMatchObject({
      id: "c1",
      epistemicType: "source_backed_fact",
      agent: "web_research",
    });

    const links = db.select().from(schema.claimSources).all();
    expect(links).toEqual([{ claimId: "c1", sourceId: "src-1" }]);

    const reports = db.select().from(schema.researchReports).all();
    expect(reports).toHaveLength(1);
    expect(reports[0]!.title).toBe("HYPE 近况");
    expect(reports[0]!.markdown).toContain("[1]");
    expect(reports[0]!.sections?.[0]).toMatchObject({ id: "Overview", claimIds: ["c1"] });
  });

  it("同一 canonical URL 再来只更新，不插第二行", () => {
    projectArtifacts(db, sessionId, event("source_found", { source: SOURCE }, 1));
    projectArtifacts(
      db,
      sessionId,
      event(
        "source_found",
        { source: { ...SOURCE, id: "src-dup", title: "Fee share (updated)", citation_index: 1 } },
        2,
      ),
    );

    const rows = db.select().from(schema.sources).all();
    expect(rows).toHaveLength(1);
    expect(rows[0]!.id).toBe("src-1");
    expect(rows[0]!.title).toBe("Fee share (updated)");
    expect(rows[0]!.citationIndex).toBe(1);
  });
});

describe("事件回放", () => {
  it("落库行还原后 reduceAll 能重建 Source Panel", () => {
    const events = [
      event("session_started", { question: "HYPE 近况", model_id: "m" }, 1),
      event("source_found", { source: { ...SOURCE, citation_index: null } }, 2),
      event(
        "report_completed",
        {
          report: {
            title: "HYPE 近况",
            executive_summary: "正在讨论手续费分享。[1]",
            sections: [
              {
                id: "Overview",
                title: "概述",
                markdown: "Hyperliquid 正在讨论手续费分享。[1]",
                claim_ids: ["c1"],
              },
            ],
            data_gaps: [],
          },
          sources: [{ ...SOURCE, citation_index: 1 }],
          claims: [
            {
              id: "c1",
              text: "正在讨论手续费分享。",
              epistemic_type: "analysis",
              confidence: "medium",
              source_ids: ["src-1"],
              as_of: null,
              task_id: "t1",
              agent: "web_research",
              verification: "unverified",
              verification_note: null,
              citation_index: null,
            },
          ],
          citation_count: 1,
        },
        3,
      ),
    ];

    db.insert(schema.researchEvents)
      .values(events.map((item) => toRow(sessionId, item)))
      .run();

    const restored = reduceAll(listEvents(db, sessionId).map(toClientEvent));
    expect(restored.sources).toHaveLength(1);
    expect(restored.sources[0]!.citation_index).toBe(1);
    expect(restored.claims).toHaveLength(1);
    expect(restored.claims[0]!.epistemic_type).toBe("analysis");
    expect(restored.report?.title).toBe("HYPE 近况");
  });
});

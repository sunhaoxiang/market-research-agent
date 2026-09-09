// @vitest-environment node

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as schema from "@/db/schema";
import { createSession } from "@/db/queries/sessions";

const getDb = vi.hoisted(() => vi.fn());
const fetchProviderDebug = vi.hoisted(() => vi.fn());

vi.mock("@/db/client", () => ({ getDb }));
vi.mock("@/lib/agent-client", () => ({ fetchProviderDebug }));

const { GET } = await import("@/app/api/debug/route");

beforeEach(() => {
  const raw = new Database(":memory:");
  raw.pragma("foreign_keys = ON");
  const db = drizzle(raw, { schema });
  migrate(db, { migrationsFolder: "./db/migrations" });
  createSession(db, {
    id: "s1",
    question: "HYPE",
    modelId: "deepseek:deepseek-v4-pro",
    createdAt: Date.now(),
  });
  getDb.mockReturnValue(db);
  fetchProviderDebug.mockResolvedValue({
    ok: true,
    data: {
      providers: [{ provider: "defillama", configured: true, requests: 0, cache_hits: 0 }],
    },
  });
});

describe("GET /api/debug", () => {
  it("带上 SQLite 聚合和 Python 的 provider 快照", async () => {
    const body = (await (await GET()).json()) as {
      totals: { sessions: number };
      providers: { provider: string }[];
      providersError: null;
    };
    expect(body.totals.sessions).toBe(1);
    expect(body.providers[0]?.provider).toBe("defillama");
    expect(body.providersError).toBeNull();
  });

  it("Agent 服务挂了仍然返回库里的排行", async () => {
    fetchProviderDebug.mockResolvedValue({
      ok: false,
      error: { code: "AGENT_SERVICE_UNREACHABLE", message: "down" },
    });
    const body = (await (await GET()).json()) as {
      totals: { sessions: number };
      providers: unknown[];
      providersError: { code: string };
    };
    expect(body.totals.sessions).toBe(1);
    expect(body.providers).toEqual([]);
    expect(body.providersError.code).toBe("AGENT_SERVICE_UNREACHABLE");
  });
});

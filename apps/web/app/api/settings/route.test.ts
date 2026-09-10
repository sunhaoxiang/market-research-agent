// @vitest-environment node

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as schema from "@/db/schema";

const getDb = vi.hoisted(() => vi.fn());
const fetchAgentModels = vi.hoisted(() => vi.fn());

vi.mock("@/db/client", () => ({ getDb }));
vi.mock("@/lib/agent-client", () => ({ fetchAgentModels }));

const { GET, PATCH } = await import("@/app/api/settings/route");

type Db = ReturnType<typeof drizzle<typeof schema>>;
let db: Db;

beforeEach(() => {
  const raw = new Database(":memory:");
  raw.pragma("foreign_keys = ON");
  db = drizzle(raw, { schema });
  migrate(db, { migrationsFolder: "./db/migrations" });
  getDb.mockReturnValue(db);
  fetchAgentModels.mockResolvedValue({
    ok: true,
    data: {
      models: [],
      role_defaults: { planner: "deepseek:deepseek-flash" },
      default_model_id: "deepseek:deepseek-flash",
      limits: {
        max_tasks_per_plan: 6,
        max_parallel_tasks: 2,
        max_tool_calls_per_agent: 12,
        max_supplement_rounds: 1,
        task_timeout_s: 180,
        total_timeout_s: 600,
        max_session_cost_usd: 1,
      },
    },
  });
});

describe("GET/PATCH /api/settings", () => {
  it("未保存过时返回空偏好和环境默认", async () => {
    const body = (await (await GET()).json()) as {
      preferences: { defaultModelId: string | null };
      defaults: { defaultModelId: string };
    };
    expect(body.preferences.defaultModelId).toBeNull();
    expect(body.defaults.defaultModelId).toBe("deepseek:deepseek-flash");
  });

  it("PATCH 之后能读回来", async () => {
    const patched = await PATCH(
      new Request("http://localhost/api/settings", {
        method: "PATCH",
        body: JSON.stringify({
          defaultModelId: "deepseek:deepseek-v4-flash",
          report: { language: "en" },
          limits: { maxTasksPerPlan: 4 },
        }),
      }),
    );
    expect(patched.status).toBe(200);

    const body = (await (await GET()).json()) as {
      preferences: {
        defaultModelId: string | null;
        report: { language: string };
        limits: { maxTasksPerPlan: number } | null;
      };
    };
    expect(body.preferences.defaultModelId).toBe("deepseek:deepseek-v4-flash");
    expect(body.preferences.report.language).toBe("en");
    expect(body.preferences.limits?.maxTasksPerPlan).toBe(4);
  });
});

// @vitest-environment node
// 这条链路上的模块标了 `server-only`，在 jsdom 环境下会主动抛错。
// 默认环境是 jsdom（组件测试要用），因此在这一个文件里覆盖成 node。

/**
 * 研究流 BFF（P1-11 验收）。
 *
 * 核心验的是 §11.2 那条约束：**浏览器断开后仍要读完上游并落库**。
 * 这条最容易在重构中被破坏——只要有人顺手把 request 的 signal 传给上游 fetch，
 * 或者在 `cancel()` 里加一句 abort，行为就悄悄退化成"关掉标签页就丢一半数据"，
 * 而所有其他测试仍然是绿的。
 */

import Database from "better-sqlite3";
import { drizzle } from "drizzle-orm/better-sqlite3";
import { migrate } from "drizzle-orm/better-sqlite3/migrator";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as schema from "@/db/schema";

const startResearch = vi.hoisted(() => vi.fn());
const cancelResearch = vi.hoisted(() => vi.fn());
const getDb = vi.hoisted(() => vi.fn());

vi.mock("@/lib/agent-client", () => ({ startResearch, cancelResearch }));
vi.mock("@/db/client", () => ({ getDb }));

const { POST, GET: listResearch } = await import("@/app/api/research/route");
const { GET } = await import("@/app/api/research/[id]/events/route");
const { DELETE } = await import("@/app/api/research/[id]/route");

type Db = ReturnType<typeof drizzle<typeof schema>>;

let db: Db;

beforeEach(() => {
  const raw = new Database(":memory:");
  raw.pragma("foreign_keys = ON");
  db = drizzle(raw, { schema });
  migrate(db, { migrationsFolder: "./db/migrations" });

  getDb.mockReturnValue(db);
  startResearch.mockReset();
  cancelResearch.mockReset();
});

function ask(body: unknown): Promise<Response> {
  return POST(
    new Request("http://localhost/api/research", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  );
}

/** 构造一条上游 SSE 响应。`gate` 用于把流悬停在中途。 */
function upstreamOf(frames: string[], gate?: Promise<void>): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const [index, frame] of frames.entries()) {
        if (index === 1 && gate) await gate;
        controller.enqueue(encoder.encode(frame));
      }
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/**
 * 交出若干帧后连接断裂的上游。
 *
 * 分两次 pull：先把帧真正交出去，再报错。写成 `enqueue(); error();` 是不行的
 * ——规范要求 `error()` 清空队列，那些帧根本到不了消费端，也就模拟不出
 * "已收到的数据不能丢"这个场景。
 */
function truncatedUpstream(...frames: string[]): Response {
  const encoder = new TextEncoder();
  let pulls = 0;
  return new Response(
    new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1;
        if (pulls <= frames.length) {
          controller.enqueue(encoder.encode(frames[pulls - 1]!));
          return;
        }
        controller.error(new Error("连接被重置"));
      },
    }),
    { status: 200 },
  );
}

function event(seq: number, type: string, payload: unknown = null, message?: string): string {
  const body = JSON.stringify({
    seq,
    session_id: "ignored",
    type,
    ts: new Date(1_800_000_000_000 + seq).toISOString(),
    message: message ?? null,
    payload,
  });
  return `id: ${seq}\nevent: research\ndata: ${body}\n\n`;
}

const PLAN = { question_type: "crypto", tasks: [{ id: "t1" }] };

const HAPPY_PATH = [
  event(1, "session_started", { question: "Q", model_id: "deepseek:deepseek-v4-pro" }),
  event(2, "stage_changed", { stage: "planning", previous: null }),
  event(3, "intent_classified", { question_type: "crypto", entities: [] }),
  event(4, "plan_created", { plan: PLAN }),
  event(5, "agent_started", { agent: "crypto_research", task_id: "t1", objective: "o" }),
  event(6, "agent_run_metrics", {
    agent: "research_manager",
    task_id: null,
    model_id: "deepseek:deepseek-v4-pro",
    status: "completed",
    prompt: { hash: "a1b2c3d4e5f60718", chars: 4096 },
    usage: { input: 2419, output: 2395, cached: 2304 },
    cost_usd: 0.0048,
    duration_ms: 27549,
    error: null,
  }),
  event(7, "session_completed", {
    duration_ms: 1234,
    cost_usd: 0.02,
    usage: { input: 2419, output: 2395, cached: 2304 },
  }),
];

function sessions() {
  return db.select().from(schema.researchSessions).all();
}

function events() {
  return db.select().from(schema.researchEvents).orderBy(schema.researchEvents.seq).all();
}

async function drain(response: Response): Promise<string> {
  return await new Response(response.body).text();
}

describe("请求校验", () => {
  it("拒绝空问题", async () => {
    const response = await ask({ question: "   " });

    expect(response.status).toBe(400);
    expect(startResearch).not.toHaveBeenCalled();
    expect(sessions()).toHaveLength(0);
  });

  it("拒绝超长问题", async () => {
    expect((await ask({ question: "x".repeat(2001) })).status).toBe(400);
  });
});

describe("正常路径", () => {
  it("先建 session 行再调上游", async () => {
    // 反过来的话，上游已经在烧 token 而本地没有任何记录
    startResearch.mockImplementation(() => {
      expect(sessions()).toHaveLength(1);
      return Promise.resolve(upstreamOf(HAPPY_PATH));
    });

    await drain(await ask({ question: "Hyperliquid 怎么样？" }));

    expect(startResearch).toHaveBeenCalledOnce();
  });

  it("把已保存的默认模型和上限传给上游", async () => {
    const { putPreferences } = await import("@/db/queries/settings");
    putPreferences(db, {
      defaultModelId: "deepseek:deepseek-v4-flash",
      roleModels: {
        planner: null,
        balanced: null,
        fast: null,
        writing: "deepseek:deepseek-v4-pro",
      },
      limits: {
        maxTasksPerPlan: 3,
        maxParallelTasks: 2,
        maxToolCallsPerAgent: 12,
        maxSupplementRounds: 1,
        taskTimeoutS: 180,
        totalTimeoutS: 600,
        maxSessionCostUsd: 1,
      },
      report: { language: "en" },
    });
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const [input] = startResearch.mock.calls[0] as [
      {
        modelId?: string;
        options?: {
          report_language?: string;
          role_models?: { writing?: string };
          limits?: { max_tasks_per_plan: number };
        };
      },
    ];
    expect(input.modelId).toBe("deepseek:deepseek-v4-flash");
    expect(input.options?.report_language).toBe("en");
    expect(input.options?.role_models?.writing).toBe("deepseek:deepseek-v4-pro");
    expect(input.options?.limits?.max_tasks_per_plan).toBe(3);
  });

  it("这次提问选的模型覆盖已保存的默认模型", async () => {
    const { putPreferences } = await import("@/db/queries/settings");
    const { emptyPreferences } = await import("@/lib/settings");
    putPreferences(db, {
      ...emptyPreferences(),
      defaultModelId: "deepseek:deepseek-v4-flash",
    });
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q", modelId: "deepseek:deepseek-v4-pro" }));

    const [input] = startResearch.mock.calls[0] as [{ modelId?: string }];
    expect(input.modelId).toBe("deepseek:deepseek-v4-pro");
  });

  it("把 session id 放在响应头里", async () => {
    // 浏览器发请求时还不知道 id，而它需要它来跳转详情页；响应体是流，只能走头
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    const response = await ask({ question: "Q" });

    expect(response.headers.get("X-Session-Id")).toBe(sessions()[0]!.id);
    await drain(response);
  });

  it("声明 event-stream 并关掉反代缓冲", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    const response = await ask({ question: "Q" });

    expect(response.headers.get("Content-Type")).toContain("text/event-stream");
    expect(response.headers.get("X-Accel-Buffering")).toBe("no");
    await drain(response);
  });

  it("事件原样转发给浏览器", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    const text = await drain(await ask({ question: "Q" }));

    expect(text.match(/^data: /gm)).toHaveLength(HAPPY_PATH.length);
    expect(text).toContain('"type":"session_completed"');
  });

  it("全部事件落库，seq 与类型都对得上", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    expect(events().map((e) => e.type)).toEqual([
      "session_started",
      "stage_changed",
      "intent_classified",
      "plan_created",
      "agent_started",
      "agent_run_metrics",
      "session_completed",
    ]);
    expect(events().map((e) => e.seq)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("会话级元数据从事件里补齐", async () => {
    // 刷新页面后要能直接重画任务树，而不是从几十条事件里重建
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const session = sessions()[0]!;
    expect(session.questionType).toBe("crypto");
    expect(session.plan).toEqual(PLAN);
    // 请求里没指定模型，真实用的是 Python 按角色解析出来的那个
    expect(session.modelId).toBe("deepseek:deepseek-v4-pro");
  });

  it("埋点行与事件同批落库", async () => {
    // §20.1 的第二条数据线。Python 不碰业务库（决策 C），所以这些行只能由
    // BFF 在消费 SSE 时派生出来——事件流是它们唯一的来源
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const runs = db.select().from(schema.agentRuns).all();
    expect(runs).toHaveLength(1);
    expect(runs[0]).toMatchObject({
      agent: "research_manager",
      modelId: "deepseek:deepseek-v4-pro",
      status: "completed",
      promptHash: "a1b2c3d4e5f60718",
      tokensIn: 2419,
      tokensCached: 2304,
    });
  });

  it("工具调用由开始与完成两条事件合成一行", async () => {
    startResearch.mockResolvedValue(
      upstreamOf([
        event(1, "session_started", { question: "Q", model_id: "m" }),
        event(2, "tool_started", {
          call_id: "c1",
          tool: "get_tvl",
          agent: "crypto_research",
          task_id: "t1",
          input_summary: { protocol: "hyperliquid" },
        }),
        event(3, "tool_completed", {
          call_id: "c1",
          tool: "get_tvl",
          ok: true,
          provider: "defillama",
          cache_hit: true,
          duration_ms: 340,
          result_summary: { tvl: 1.2e9 },
        }),
        event(4, "session_completed", { duration_ms: 1, cost_usd: 0, usage: {} }),
      ]),
    );

    await drain(await ask({ question: "Q" }));

    const calls = db.select().from(schema.toolCalls).all();
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      tool: "get_tvl",
      agent: "crypto_research",
      taskId: "t1",
      provider: "defillama",
      cacheHit: true,
      ok: true,
      durationMs: 340,
    });
  });

  it("流中断时未闭合的工具调用落成 abandoned", async () => {
    // 「卡住的工具」是最该被 /debug 发现的一类问题，不能因为流断了就丢掉
    startResearch.mockResolvedValue(
      truncatedUpstream(
        event(1, "tool_started", {
          call_id: "c1",
          tool: "web_fetch",
          agent: "web_research",
          task_id: "t1",
          input_summary: {},
        }),
      ),
    );

    await drain(await ask({ question: "Q" }));

    const calls = db.select().from(schema.toolCalls).all();
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ tool: "web_fetch", ok: false, errorCode: "abandoned" });
  });

  it("终态事件里的用量与成本都落库", async () => {
    // 终态是权威账本；过程中的 usage_updated 也会先写一笔，两者应对齐
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const session = sessions()[0]!;
    expect(session.tokenUsage).toEqual({ input: 2419, output: 2395, cached: 2304 });
    expect(session.costUsd).toBeCloseTo(0.02);
    expect(session.durationMs).toBe(1234);
  });

  it("usage_updated 就把累计成本和 token 写入会话，不等终态", async () => {
    startResearch.mockResolvedValue(
      truncatedUpstream(
        event(1, "session_started", { question: "Q", model_id: "m" }),
        event(2, "usage_updated", {
          usage: { input: 400, output: 80, cached: 96 },
          cost_usd: 0.0031,
        }),
      ),
    );

    await drain(await ask({ question: "Q" }));

    const session = sessions()[0]!;
    expect(session.tokenUsage).toEqual({ input: 400, output: 80, cached: 96 });
    expect(session.costUsd).toBeCloseTo(0.0031);
    expect(session.status).not.toBe("completed");
  });

  it("把 payload 里的 agent / task_id 提到列上", async () => {
    // 让「某个 agent 的全部事件」这类查询不必解析 JSON
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const started = events().find((e) => e.type === "agent_started")!;
    expect(started.agent).toBe("crypto_research");
    expect(started.taskId).toBe("t1");
  });

  it("事件驱动 session 状态机", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const session = sessions()[0]!;
    expect(session.status).toBe("completed");
    expect(session.durationMs).toBe(1234);
    expect(session.costUsd).toBe(0.02);
    expect(session.completedAt).not.toBeNull();
  });

  it("失败事件落成 failed 与错误详情", async () => {
    startResearch.mockResolvedValue(
      upstreamOf([
        HAPPY_PATH[0]!,
        event(2, "session_failed", {
          error: { code: "plan_rejected", message: "计划为空" },
          stage: "planning",
        }),
      ]),
    );

    await drain(await ask({ question: "Q" }));

    const session = sessions()[0]!;
    expect(session.status).toBe("failed");
    expect(session.error).toEqual({ code: "plan_rejected", message: "计划为空" });
  });
});

describe("浏览器断开（§11.2）", () => {
  it("断开后仍读完上游并落全部事件", async () => {
    let openGate = () => {};
    const gate = new Promise<void>((resolve) => {
      openGate = resolve;
    });
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH, gate));

    const response = await ask({ question: "Q" });
    const reader = response.body!.getReader();

    // 收到第一帧就断开，此时上游还剩三帧没发
    await reader.read();
    await reader.cancel();

    openGate();
    await vi.waitFor(() => expect(sessions()[0]!.status).toBe("completed"));

    expect(events()).toHaveLength(HAPPY_PATH.length);
    // 断开之后才到的 plan_created 也要落库，否则刷新页面看不到任务树
    expect(sessions()[0]!.plan).toEqual(PLAN);
  });

  it("不把上游 fetch 绑到客户端的 signal 上", async () => {
    // 传了 signal 的话，一断连上游就被中止，研究白跑且数据只剩一半
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));

    await drain(await ask({ question: "Q" }));

    const [input] = startResearch.mock.calls[0] as [Record<string, unknown>];
    expect(input).not.toHaveProperty("signal");
  });
});

describe("上游故障", () => {
  it("连不上 Agent 服务时返回 503 并把 session 标记为失败", async () => {
    startResearch.mockRejectedValue(new Error("ECONNREFUSED"));

    const response = await ask({ question: "Q" });

    expect(response.status).toBe(503);
    expect(sessions()[0]!.status).toBe("failed");
    expect(sessions()[0]!.error?.code).toBe("AGENT_SERVICE_UNREACHABLE");
  });

  it("原样转出上游的结构化错误，不压成泛化的 502", async () => {
    startResearch.mockResolvedValue(
      Response.json(
        { detail: { error: { code: "MODEL_NOT_AVAILABLE", message: "未配置 OPENAI_API_KEY" } } },
        { status: 503 },
      ),
    );

    const response = await ask({ question: "Q", modelId: "openai:gpt-5.6-terra" });
    const body = (await response.json()) as { error: { code: string; message: string } };

    expect(body.error.code).toBe("MODEL_NOT_AVAILABLE");
    expect(body.error.message).toContain("OPENAI_API_KEY");
    expect(sessions()[0]!.status).toBe("failed");
  });

  it("流中途断裂时 session 落成 failed，而不是假装完成", async () => {
    startResearch.mockResolvedValue(truncatedUpstream(HAPPY_PATH[0]!));

    await drain(await ask({ question: "Q" })).catch(() => {});
    await vi.waitFor(() => expect(sessions()[0]!.status).toBe("failed"));

    expect(sessions()[0]!.error?.code).toBe("UPSTREAM_STREAM_BROKEN");
    // 断裂前收到的事件不能丢
    expect(events()).toHaveLength(1);
  });
});

describe("来源与陈述投影", () => {
  it("SOURCE_FOUND / REPORT_COMPLETED 写入 sources 与 claims", async () => {
    const source = {
      id: "src-1",
      ref: "s1",
      url: "https://theblock.co/a",
      url_canonical: "https://theblock.co/a",
      title: "Fee share",
      domain: "theblock.co",
      source_type: "news",
      provider: "tavily",
      reliability: "secondary",
      published_at: null,
      retrieved_at: "2026-09-08T12:00:00.000Z",
      excerpt: "holders",
      citation_index: null,
      http_status: 200,
    };
    startResearch.mockResolvedValue(
      upstreamOf([
        event(1, "session_started", { question: "Q", model_id: "m" }),
        event(2, "source_found", { source }),
        event(3, "report_completed", {
          report: {
            title: "近况",
            executive_summary: "讨论。[1]",
            sections: [{ id: "Overview", title: "概述", markdown: "讨论。[1]", claim_ids: ["c1"] }],
            data_gaps: [],
          },
          sources: [{ ...source, citation_index: 1 }],
          claims: [
            {
              id: "c1",
              text: "讨论。",
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
        }),
        event(4, "session_completed", { duration_ms: 1, cost_usd: 0, usage: {} }),
      ]),
    );

    await drain(await ask({ question: "Q" }));

    expect(db.select().from(schema.sources).all()).toMatchObject([
      { id: "src-1", citationIndex: 1, urlCanonical: "https://theblock.co/a" },
    ]);
    expect(db.select().from(schema.claims).all()).toMatchObject([{ id: "c1" }]);
    expect(db.select().from(schema.claimSources).all()).toEqual([
      { claimId: "c1", sourceId: "src-1" },
    ]);
  });
});

describe("事件回放 GET /api/research/{id}/events", () => {
  it("把已落库事件还原成前端信封", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));
    const posted = await ask({ question: "Q" });
    const sessionId = posted.headers.get("X-Session-Id")!;
    await drain(posted);

    const response = await GET(new Request(`http://localhost/api/research/${sessionId}/events`), {
      params: Promise.resolve({ id: sessionId }),
    });
    const body = (await response.json()) as { events: Array<{ type: string; seq: number }> };

    expect(response.status).toBe(200);
    expect(body.events.map((item) => item.type)).toEqual([
      "session_started",
      "stage_changed",
      "intent_classified",
      "plan_created",
      "agent_started",
      "agent_run_metrics",
      "session_completed",
    ]);
  });

  it("after=seq 只返回后续事件（P6-6 增量入口）", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));
    const posted = await ask({ question: "Q" });
    const sessionId = posted.headers.get("X-Session-Id")!;
    await drain(posted);

    const response = await GET(
      new Request(`http://localhost/api/research/${sessionId}/events?after=3`),
      { params: Promise.resolve({ id: sessionId }) },
    );
    const body = (await response.json()) as { events: Array<{ seq: number }> };

    expect(body.events.map((item) => item.seq)).toEqual([4, 5, 6, 7]);
  });

  it("带上 session 状态，方便前端决定要不要续订", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));
    const posted = await ask({ question: "Q" });
    const sessionId = posted.headers.get("X-Session-Id")!;
    await drain(posted);

    const response = await GET(new Request(`http://localhost/api/research/${sessionId}/events`), {
      params: Promise.resolve({ id: sessionId }),
    });
    const body = (await response.json()) as { status: string };
    expect(body.status).toBe("completed");
  });

  it("未知 session 返回 404", async () => {
    const response = await GET(new Request("http://localhost/api/research/missing/events"), {
      params: Promise.resolve({ id: "missing" }),
    });
    expect(response.status).toBe(404);
  });
});

describe("会话列表 GET /api/research", () => {
  it("返回分页列表和来源数", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));
    const posted = await ask({ question: "HYPE 怎么样" });
    const sessionId = posted.headers.get("X-Session-Id")!;
    await drain(posted);

    const response = await listResearch(new Request("http://localhost/api/research"));
    const body = (await response.json()) as {
      total: number;
      sessions: Array<{ id: string; question: string; sourceCount: number; status: string }>;
    };
    expect(response.status).toBe(200);
    expect(body.total).toBe(1);
    expect(body.sessions[0]).toMatchObject({
      id: sessionId,
      question: "HYPE 怎么样",
      status: "completed",
    });
  });
});

describe("取消 DELETE /api/research/{id}", () => {
  it("进行中的会话转给 Python cancel", async () => {
    const { createSession } = await import("@/db/queries/sessions");
    const session = createSession(db, {
      id: "sess-run",
      question: "Q",
      modelId: "deepseek:deepseek-v4-pro",
      status: "researching",
      createdAt: Date.now(),
    });
    cancelResearch.mockResolvedValue({ ok: true, data: { cancelled: true } });

    const response = await DELETE(new Request(`http://localhost/api/research/${session.id}`), {
      params: Promise.resolve({ id: session.id }),
    });
    expect(response.status).toBe(200);
    expect(cancelResearch).toHaveBeenCalledWith(session.id);
  });

  it("已结束的会话返回 409", async () => {
    startResearch.mockResolvedValue(upstreamOf(HAPPY_PATH));
    const posted = await ask({ question: "Q" });
    const sessionId = posted.headers.get("X-Session-Id")!;
    await drain(posted);

    const response = await DELETE(new Request(`http://localhost/api/research/${sessionId}`), {
      params: Promise.resolve({ id: sessionId }),
    });
    expect(response.status).toBe(409);
  });
});

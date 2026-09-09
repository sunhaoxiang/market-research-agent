/**
 * Playwright 用的 Agent 服务打桩。
 *
 * 浏览器只打 Next BFF（§11.1），Next 再把研究流转到这里。
 * 不调 LLM、不碰真实 Tavily / CoinGecko。端口默认 18000，避免和本机 :8000 撞车。
 *
 * 取消必须先发 `session_cancelled` 再关连接——只掐 TCP 会被 BFF 当成流中断。
 */
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";

const HOST = "127.0.0.1";
const PORT = Number(process.env.STUB_AGENT_PORT ?? 18000);

const MODEL_ID = "deepseek:deepseek-v4-pro";

const MODELS = {
  models: [
    {
      id: MODEL_ID,
      provider: "deepseek",
      display_name: "DeepSeek V4 Pro",
      available: true,
      unavailable_reason: null,
      capabilities: {
        tool_calling: true,
        parallel_tool_calls: true,
        structured_output: "json_mode",
        streaming: true,
        reasoning: true,
        vision: false,
        context_window: 128000,
        max_output_tokens: 8192,
        pricing: null,
      },
      verified_at: "2026-09-07",
      verified: true,
      notes: "e2e stub",
    },
  ],
  role_defaults: {
    planner: MODEL_ID,
    balanced: MODEL_ID,
    fast: MODEL_ID,
    writing: MODEL_ID,
  },
  default_model_id: MODEL_ID,
  limits: {
    max_tasks_per_plan: 6,
    max_parallel_tasks: 4,
    max_tool_calls_per_agent: 8,
    max_supplement_rounds: 1,
    task_timeout_s: 180,
    total_timeout_s: 900,
    max_session_cost_usd: 1,
  },
};

const HEALTH = {
  status: "ok",
  version: "e2e-stub",
  tracing_enabled: false,
  llm_providers: [
    { provider: "openai", configured: false },
    { provider: "moonshot", configured: false },
    { provider: "deepseek", configured: true },
    { provider: "zhipu", configured: false },
    { provider: "anthropic", configured: false },
    { provider: "google", configured: false },
  ],
  data_sources: [
    { provider: "tavily", configured: true },
    { provider: "coingecko", configured: true },
    { provider: "defillama", configured: true },
    { provider: "hyperliquid", configured: true },
    { provider: "fmp", configured: false },
    { provider: "sec_edgar", configured: true },
  ],
};

const PROVIDERS = {
  providers: [
    {
      provider: "tavily",
      configured: true,
      requests: 4,
      cache_hits: 1,
      cache_misses: 3,
      cache_hit_rate: 0.25,
      http_attempts: 3,
      errors: 0,
      daily_used: 4,
      daily_limit: 1000,
      daily_remaining: 996,
      monthly_used: 4,
      monthly_limit: 10000,
      monthly_remaining: 9996,
    },
  ],
};

/** @type {Map<string, { cancel: () => void }>} */
const runs = new Map();

const SOURCE = {
  id: "src-defillama-hl",
  ref: "s1",
  url: "https://defillama.com/protocol/hyperliquid",
  url_canonical: "https://defillama.com/protocol/hyperliquid",
  title: "Hyperliquid TVL",
  domain: "defillama.com",
  source_type: "api",
  provider: "defillama",
  reliability: "primary",
  published_at: null,
  retrieved_at: "2026-09-09T00:00:00.000Z",
  excerpt: "Hyperliquid protocol fees",
  citation_index: null,
  http_status: 200,
};

const PLAN = {
  question_type: "crypto",
  interpretation: "用户想了解 HYPE 的协议收入",
  entities: [
    {
      type: "crypto",
      symbol: "HYPE",
      name: "Hyperliquid",
      chain: null,
      contract_address: null,
    },
  ],
  tasks: [
    {
      id: "t1",
      agent: "crypto_research",
      objective: "获取 HYPE 协议手续费收入",
      entities: [],
      suggested_tools: ["get_tvl"],
      depends_on: [],
      priority: 0,
    },
  ],
  report_sections: ["Overview"],
  assumptions: [],
};

const REPORT = {
  title: "HYPE 协议收入简报",
  executive_summary: "Hyperliquid 协议手续费仍在增长。[1]",
  sections: [
    {
      id: "Overview",
      title: "概述",
      markdown: "近期协议收入保持上升。[1]",
      claim_ids: [],
    },
  ],
  data_gaps: [],
};

function script(question, modelId) {
  const model = typeof modelId === "string" && modelId ? modelId : MODEL_ID;
  return [
    { type: "session_started", payload: { question, model_id: model } },
    { type: "stage_changed", payload: { stage: "planning", previous: null } },
    {
      type: "intent_classified",
      payload: {
        question_type: "crypto",
        entities: PLAN.entities,
      },
    },
    { type: "plan_created", payload: { plan: PLAN }, message: "已生成 1 个研究任务" },
    metrics("research_manager", null, model, 1100, 400, 0.01, 80),
    {
      type: "usage_updated",
      payload: { usage: { input: 1100, output: 400, cached: 0 }, cost_usd: 0.01 },
    },
    { type: "stage_changed", payload: { stage: "researching", previous: "planning" } },
    {
      type: "agent_started",
      payload: {
        agent: "crypto_research",
        task_id: "t1",
        objective: "获取 HYPE 协议手续费收入",
        model_id: model,
      },
    },
    {
      type: "tool_started",
      payload: {
        call_id: "c1",
        tool: "get_tvl",
        agent: "crypto_research",
        task_id: "t1",
        input_summary: { protocol: "hyperliquid" },
      },
    },
    {
      type: "tool_completed",
      payload: {
        call_id: "c1",
        tool: "get_tvl",
        ok: true,
        provider: "defillama",
        cache_hit: true,
        duration_ms: 12,
        result_summary: { tvl: 1.4e9 },
      },
    },
    { type: "source_found", payload: { source: SOURCE } },
    {
      type: "agent_completed",
      payload: {
        agent: "crypto_research",
        task_id: "t1",
        summary: "手续费收入仍在增长",
        claim_count: 1,
        source_count: 1,
        duration_ms: 40,
      },
    },
    metrics("crypto_research", "t1", model, 1300, 400, 0.01, 40),
    {
      type: "usage_updated",
      payload: { usage: { input: 2400, output: 800, cached: 0 }, cost_usd: 0.02 },
    },
    { type: "stage_changed", payload: { stage: "writing", previous: "researching" } },
    { type: "report_started", payload: null },
    {
      type: "report_completed",
      payload: {
        report: REPORT,
        sources: [{ ...SOURCE, citation_index: 1 }],
        claims: [],
        citation_count: 1,
      },
      message: "报告已生成",
    },
    {
      type: "session_completed",
      payload: {
        duration_ms: 1200,
        cost_usd: 0.02,
        usage: { input: 2400, output: 800, cached: 0 },
      },
    },
  ];
}

function metrics(agent, taskId, modelId, input, output, cost, durationMs) {
  return {
    type: "agent_run_metrics",
    payload: {
      agent,
      task_id: taskId,
      model_id: modelId,
      status: "completed",
      prompt: { hash: "a1b2c3d4e5f60718", chars: 128 },
      usage: { input, output, cached: 0 },
      cost_usd: cost,
      duration_ms: durationMs,
      error: null,
    },
  };
}

function json(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (chunks.length === 0) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

function sseHeaders(res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
}

function wait(ms, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    const onAbort = () => {
      clearTimeout(timer);
      resolve();
    };
    if (signal.aborted) {
      onAbort();
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

async function streamResearch(req, res, body) {
  const sessionId =
    typeof body.session_id === "string" && body.session_id ? body.session_id : randomUUID();
  const question = typeof body.question === "string" ? body.question : "";
  const slow = /\[e2e-slow\]|慢慢/.test(question);
  const ac = new AbortController();
  let seq = 0;
  let closed = false;

  const finish = () => {
    if (closed) return;
    closed = true;
    runs.delete(sessionId);
    if (!res.writableEnded) res.end();
  };

  const writeEvent = (type, payload, message = null) => {
    if (closed || res.writableEnded) return;
    seq += 1;
    const event = {
      seq,
      session_id: sessionId,
      ts: new Date().toISOString(),
      message,
      type,
      payload,
    };
    res.write(`id: ${seq}\nevent: research\ndata: ${JSON.stringify(event)}\n\n`);
  };

  runs.set(sessionId, {
    cancel() {
      writeEvent("session_cancelled", null, "研究已取消");
      ac.abort();
      finish();
    },
  });

  req.on("close", () => {
    if (!closed) ac.abort();
  });

  sseHeaders(res);
  res.write(": stream open\n\n");

  try {
    for (const step of script(question, body.model_id)) {
      if (closed || ac.signal.aborted) return;
      await wait(slow ? 40 : 15, ac.signal);
      if (closed || ac.signal.aborted) return;
      writeEvent(step.type, step.payload, step.message ?? null);
      if (slow && step.type === "stage_changed" && step.payload?.stage === "planning") {
        while (!closed && !ac.signal.aborted) {
          await wait(200, ac.signal);
        }
        return;
      }
    }
  } finally {
    finish();
  }
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? "/", `http://${HOST}:${PORT}`);
  const { pathname } = url;

  if (req.method === "GET" && pathname === "/v1/health") {
    json(res, 200, HEALTH);
    return;
  }
  if (req.method === "GET" && pathname === "/v1/models") {
    json(res, 200, MODELS);
    return;
  }
  if (req.method === "GET" && pathname === "/v1/debug/providers") {
    json(res, 200, PROVIDERS);
    return;
  }
  if (req.method === "POST" && pathname === "/v1/research/stream") {
    readBody(req)
      .then((body) => streamResearch(req, res, body))
      .catch(() => json(res, 400, { error: { code: "INVALID_JSON", message: "请求体不是 JSON" } }));
    return;
  }

  const cancel = pathname.match(/^\/v1\/research\/([^/]+)\/cancel$/);
  if (req.method === "POST" && cancel) {
    const run = runs.get(decodeURIComponent(cancel[1] ?? ""));
    if (!run) {
      json(res, 404, { error: { code: "NOT_RUNNING", message: "没有进行中的研究" } });
      return;
    }
    run.cancel();
    json(res, 200, { cancelled: true });
    return;
  }

  json(res, 404, { error: { code: "NOT_FOUND", message: pathname } });
});

server.listen(PORT, HOST, () => {
  process.stdout.write(`e2e stub listening on http://${HOST}:${PORT}\n`);
});

/**
 * Python Agent Service 客户端。
 *
 * 所有对 Agent 服务的调用都经过这里，统一附带内网校验 token（§11.3）。
 */

import "server-only";

import { serverEnv } from "@/lib/env";

export type AgentProviderStatus = {
  provider: string;
  configured: boolean;
};

export type AgentHealth = {
  status: "ok" | "degraded";
  version: string;
  tracing_enabled: boolean;
  llm_providers: AgentProviderStatus[];
  data_sources: AgentProviderStatus[];
};

function internalHeaders(): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (serverEnv.internalApiToken) {
    headers["X-Internal-Token"] = serverEnv.internalApiToken;
  }
  return headers;
}

export type AgentServiceResult<T> =
  { ok: true; data: T } | { ok: false; error: { code: string; message: string } };

/** 模型能力元数据。字段与 Python 侧 `ModelCapabilities` 对应（§9.3）。 */
export type ModelCapabilities = {
  tool_calling: boolean;
  parallel_tool_calls: boolean;
  structured_output: "native_schema" | "json_mode" | "prompt_only";
  streaming: boolean;
  reasoning: boolean;
  vision: boolean;
  context_window: number;
  max_output_tokens: number;
  pricing: unknown;
};

export type AgentModelInfo = {
  id: string;
  provider: string;
  display_name: string;
  available: boolean;
  /** 不可用原因，例如「未配置 DEEPSEEK_API_KEY」。UI 应把选项置灰并显示这句话。 */
  unavailable_reason: string | null;
  capabilities: ModelCapabilities;
  /** 参数最后一次对照官方文档核实的日期；null 表示未核实，UI 应提示。 */
  verified_at: string | null;
  /** 是否完成过一次完整研究冒烟。与 verified_at 不是同一件事。 */
  verified: boolean;
  notes: string | null;
};

export type AgentModels = {
  models: AgentModelInfo[];
  role_defaults: Record<string, string>;
  default_model_id: string;
};

async function callAgent<T>(path: string, timeoutMs: number): Promise<AgentServiceResult<T>> {
  try {
    const response = await fetch(`${serverEnv.agentServiceUrl}${path}`, {
      headers: internalHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (!response.ok) {
      return {
        ok: false,
        error: {
          code: "AGENT_SERVICE_ERROR",
          message: `Agent 服务返回 ${response.status}`,
        },
      };
    }

    return { ok: true, data: (await response.json()) as T };
  } catch (error) {
    const isTimeout = error instanceof Error && error.name === "TimeoutError";
    return {
      ok: false,
      error: {
        code: isTimeout ? "AGENT_SERVICE_TIMEOUT" : "AGENT_SERVICE_UNREACHABLE",
        message: isTimeout
          ? `Agent 服务 ${timeoutMs}ms 内未响应`
          : `无法连接 Agent 服务（${serverEnv.agentServiceUrl}）。是否已启动？`,
      },
    };
  }
}

export function fetchAgentHealth(timeoutMs = 3000): Promise<AgentServiceResult<AgentHealth>> {
  return callAgent<AgentHealth>("/v1/health", timeoutMs);
}

export function fetchAgentModels(timeoutMs = 3000): Promise<AgentServiceResult<AgentModels>> {
  return callAgent<AgentModels>("/v1/models", timeoutMs);
}

export type StartResearchInput = {
  sessionId: string;
  question: string;
  modelId?: string;
};

/**
 * 启动一次研究，返回未消费的 SSE 响应（§16.2）。
 *
 * 刻意**不**接受 `AbortSignal`：调用方（Route Handler）在浏览器断开后仍要读完
 * 这条流以完成落库（§11.2）。把客户端的 signal 传到这里就会一断连就中止上游，
 * 用户刷新页面后只能看到半截数据。
 *
 * 也**不**设总超时：研究本身可能跑几分钟，超时由 Python 侧的
 * `TOTAL_TIMEOUT_S` 统一管（§7.2），两边各设一套迟早不一致。
 */
export async function startResearch(input: StartResearchInput): Promise<Response> {
  return fetch(`${serverEnv.agentServiceUrl}/v1/research/stream`, {
    method: "POST",
    headers: {
      ...internalHeaders(),
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session_id: input.sessionId,
      question: input.question,
      model_id: input.modelId ?? null,
    }),
    cache: "no-store",
  });
}

export async function cancelResearch(
  sessionId: string,
): Promise<AgentServiceResult<{ cancelled: boolean }>> {
  try {
    const response = await fetch(
      `${serverEnv.agentServiceUrl}/v1/research/${encodeURIComponent(sessionId)}/cancel`,
      {
        method: "POST",
        headers: internalHeaders(),
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      },
    );
    const detail: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      return { ok: false, error: extractAgentError(detail, response.status) };
    }
    return { ok: true, data: detail as { cancelled: boolean } };
  } catch (error) {
    const isTimeout = error instanceof Error && error.name === "TimeoutError";
    return {
      ok: false,
      error: {
        code: isTimeout ? "AGENT_SERVICE_TIMEOUT" : "AGENT_SERVICE_UNAVAILABLE",
        message: isTimeout
          ? "取消请求超时"
          : `无法连接 Agent 服务（${serverEnv.agentServiceUrl}）。是否已启动？`,
      },
    };
  }
}

function extractAgentError(detail: unknown, status: number): { code: string; message: string } {
  const outer = detail as { error?: unknown; detail?: { error?: unknown } } | null;
  const candidate = outer?.error ?? outer?.detail?.error;
  const error = candidate as { code?: unknown; message?: unknown } | undefined;
  return {
    code: typeof error?.code === "string" ? error.code : "AGENT_SERVICE_ERROR",
    message: typeof error?.message === "string" ? error.message : `Agent 服务返回 ${status}`,
  };
}

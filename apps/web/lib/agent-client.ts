/**
 * Python Agent Service 客户端。
 *
 * 所有对 Agent 服务的调用都经过这里，统一附带内网校验 token（§11.3）。
 * Phase 1 起会在此增加 SSE 研究流的调用。
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

function internalHeaders(): HeadersInit {
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

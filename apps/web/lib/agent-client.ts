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

export async function fetchAgentHealth(timeoutMs = 3000): Promise<AgentServiceResult<AgentHealth>> {
  try {
    const response = await fetch(`${serverEnv.agentServiceUrl}/v1/health`, {
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

    return { ok: true, data: (await response.json()) as AgentHealth };
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

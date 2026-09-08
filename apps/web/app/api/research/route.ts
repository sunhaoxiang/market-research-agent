/**
 * 研究流 BFF（§11.2，P1-11）。
 *
 * 三件事同时做：把事件转发给浏览器、把事件落库、维护 session 的状态机。
 *
 * **落库不绑定浏览器的生命周期，这是本文件最重要的约束。** 用户关掉标签页
 * 只该停止转发；上游的研究已经在花钱了，读完它并落库是刷新页面后还能看到
 * 完整结果的唯一前提。所以：
 *   - `startResearch()` 不接收 request 的 `AbortSignal`；
 *   - `ReadableStream` 的 `cancel()` 只置一个标志位，不中止上游；
 *   - 消费循环从头到尾只有一个，转发是它的副作用而非驱动。
 */

import type { ResearchEvent } from "@mra/shared";

import { getDb } from "@/db/client";
import { projectArtifacts } from "@/db/queries/artifacts";
import { EventBuffer } from "@/db/queries/events";
import {
  countSessions,
  countSourcesBySession,
  createSession,
  listSessions,
  patchSession,
  updateSessionStatus,
} from "@/db/queries/sessions";
import {
  QUESTION_TYPES,
  SESSION_STATUSES,
  type QuestionType,
  type SessionStatus,
  type TokenUsage,
} from "@/db/schema";
import { startResearch } from "@/lib/agent-client";
import { newId } from "@/lib/ids";
import { parseFrames, toResearchEvents } from "@/lib/sse";

export const dynamic = "force-dynamic";

const MAX_QUESTION_CHARS = 2000;

type RequestBody = {
  question?: unknown;
  modelId?: unknown;
};

export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as RequestBody;
  const question = typeof body.question === "string" ? body.question.trim() : "";
  const modelId = typeof body.modelId === "string" ? body.modelId : undefined;

  if (!question || question.length > MAX_QUESTION_CHARS) {
    return errorResponse(400, "INVALID_QUESTION", `问题需在 1-${MAX_QUESTION_CHARS} 字之间`);
  }

  const db = getDb();
  const sessionId = newId();

  // session 行先落地再调上游：反过来的话，上游已经开始烧 token 而本地没有任何
  // 记录，失败后连"这次研究存在过"都查不到
  createSession(db, {
    id: sessionId,
    question,
    modelId: modelId ?? "auto",
    status: "pending",
    createdAt: Date.now(),
    startedAt: Date.now(),
  });

  const upstream = await startResearch({ sessionId, question, modelId }).catch(
    (error: unknown) => error as Error,
  );

  if (upstream instanceof Error) {
    const message = `无法连接 Agent 服务。是否已启动？（${upstream.message}）`;
    failSession(sessionId, "AGENT_SERVICE_UNREACHABLE", message);
    return errorResponse(503, "AGENT_SERVICE_UNREACHABLE", message);
  }

  if (!upstream.ok || !upstream.body) {
    // 上游在流打开之前就失败了（模型不可用、token 不匹配）。它按 §16.3
    // 返回结构化错误，原样转出去——BFF 不该把它压成一句泛化的 502
    const detail: unknown = await upstream.json().catch(() => null);
    const error = extractError(detail);
    failSession(sessionId, error.code, error.message);
    return Response.json({ error }, { status: upstream.status });
  }

  return new Response(forward(upstream.body, sessionId), {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      // 少了它，反代会把事件攒到缓冲区满才下发（Next 文档 Streaming §反向代理）
      "X-Accel-Buffering": "no",
      // 浏览器发起请求时还不知道 session id，而它需要它来跳转到详情页。
      // 响应体是流，只能走响应头
      "X-Session-Id": sessionId,
    },
  });
}

/**
 * 消费上游、落库、转发。
 *
 * 转发写成"尽力而为"：`enqueue` 在浏览器已断开时会抛异常，那不是错误，
 * 只是没人听了。落库必须继续。
 */
function forward(
  upstream: ReadableStream<Uint8Array>,
  sessionId: string,
): ReadableStream<Uint8Array> {
  const db = getDb();
  const buffer = new EventBuffer(db, sessionId);
  const encoder = new TextEncoder();
  let clientGone = false;

  return new ReadableStream<Uint8Array>({
    start(controller) {
      // 不 await：`start` 一返回，响应头就发给浏览器了。消费循环在后台继续，
      // 它的生命周期由上游决定，与这个 controller 无关
      void (async () => {
        try {
          for await (const event of toResearchEvents(parseFrames(upstream))) {
            buffer.add(event);
            applyStatus(db, sessionId, event);

            if (clientGone) continue;
            try {
              controller.enqueue(encoder.encode(frame(event)));
            } catch {
              // 浏览器断了。继续读上游，只是不再往外写
              clientGone = true;
            }
          }
        } catch (error) {
          // 上游中途断裂。session 会停在最后一个已知状态上，而不是假装完成
          console.error("[research] 上游流中断", { sessionId, error });
          failSession(sessionId, "UPSTREAM_STREAM_BROKEN", "与 Agent 服务的连接中断");
        } finally {
          // close 而非 flush：还要把中途断裂时没闭合的工具调用落成
          // ok=false 的行，否则"卡住的工具"在 /debug 里完全不可见
          buffer.close();
          if (!clientGone) {
            try {
              controller.close();
            } catch {
              // 已经关了，无所谓
            }
          }
        }
      })();
    },

    cancel() {
      // 浏览器断开。**不**取消上游：研究还在跑，读完它才能落全（§11.2）
      clientGone = true;
    },
  });
}

/** 重新编码成 SSE 帧。不直接透传上游字节：那样就没法在中间插入自己的事件。 */
function frame(event: ResearchEvent): string {
  return `id: ${event.seq}\nevent: research\ndata: ${JSON.stringify(event)}\n\n`;
}

/** 事件驱动 session 的状态机与元数据（§11.1：Session 生命周期归 Next 管）。 */
function applyStatus(db: ReturnType<typeof getDb>, sessionId: string, event: ResearchEvent): void {
  try {
    projectArtifacts(db, sessionId, event);
  } catch (error) {
    console.error("[research] 投影来源/陈述失败", { sessionId, type: event.type, error });
  }

  const payload = (event.payload ?? {}) as Record<string, unknown>;

  switch (event.type) {
    case "session_started": {
      // 模型可能是 Python 侧按角色解析出来的，请求里的 modelId 是空的
      const modelId = payload.model_id;
      if (typeof modelId === "string") patchSession(db, sessionId, { modelId });
      break;
    }
    case "intent_classified": {
      // 历史列表要按问题类型分组，从事件里捞比事后解析 plan JSON 省事
      const questionType = payload.question_type;
      if (typeof questionType === "string") {
        patchSession(db, sessionId, { questionType: questionType as QuestionType });
      }
      break;
    }
    case "plan_created":
      // 存整个计划：刷新页面后要能重画任务树，而不是从几十条事件里重建
      patchSession(db, sessionId, { plan: payload.plan ?? null });
      break;
    case "usage_updated":
      // 中途的增量用量。目前 pipeline 只在收尾时给一次总账，但历史会话列表
      // 不该依赖"会话必须跑完"才有用量——中途崩掉的会话也要能看到烧了多少
      patchSession(db, sessionId, { tokenUsage: toTokenUsage(payload.usage) });
      break;
    case "stage_changed": {
      const stage = payload.stage;
      if (typeof stage === "string") {
        updateSessionStatus(db, sessionId, stage as SessionStatus);
      }
      break;
    }
    case "session_completed":
      updateSessionStatus(db, sessionId, "completed", {
        completedAt: Date.now(),
        durationMs: numberOrNull(payload.duration_ms),
        costUsd: numberOrNull(payload.cost_usd),
        // 权威用量在这里，不在 usage_updated——pipeline 目前根本不发那个事件，
        // 只依赖它的话历史会话的 token_usage 会一直是空的
        tokenUsage: toTokenUsage(payload.usage),
      });
      break;
    case "session_failed":
      updateSessionStatus(db, sessionId, "failed", {
        completedAt: Date.now(),
        error: extractError({ error: payload.error }),
      });
      break;
    case "session_cancelled":
      updateSessionStatus(db, sessionId, "cancelled", { completedAt: Date.now() });
      break;
    default:
      break;
  }
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/** Python 侧的 snake_case usage 转成表里的 camelCase 形状。 */
function toTokenUsage(value: unknown): TokenUsage | null {
  const usage = value as Record<string, unknown> | null | undefined;
  if (!usage) return null;
  return {
    input: numberOrNull(usage.input) ?? 0,
    output: numberOrNull(usage.output) ?? 0,
    cached: numberOrNull(usage.cached) ?? 0,
  };
}

function failSession(sessionId: string, code: string, message: string): void {
  updateSessionStatus(getDb(), sessionId, "failed", {
    completedAt: Date.now(),
    error: { code, message },
  });
}

function extractError(detail: unknown): { code: string; message: string } {
  const outer = detail as { error?: unknown; detail?: { error?: unknown } } | null;
  // FastAPI 把 HTTPException 的 detail 包一层，所以两个位置都要看
  const candidate = outer?.error ?? outer?.detail?.error;
  const error = candidate as { code?: unknown; message?: unknown } | undefined;

  return {
    code: typeof error?.code === "string" ? error.code : "AGENT_SERVICE_ERROR",
    message: typeof error?.message === "string" ? error.message : "Agent 服务返回了未知错误",
  };
}

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json({ error: { code, message } }, { status });
}

const LIST_LIMIT_MAX = 50;

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = parseBoundInt(url.searchParams.get("limit"), 20, 1, LIST_LIMIT_MAX);
  const offset = parseBoundInt(url.searchParams.get("offset"), 0, 0, 10_000);
  const status = parseEnum(url.searchParams.get("status"), SESSION_STATUSES);
  const questionType = parseEnum(url.searchParams.get("questionType"), QUESTION_TYPES);
  const modelId = url.searchParams.get("modelId")?.trim() || undefined;

  const db = getDb();
  const filter = { status, questionType, modelId, limit, offset };
  const rows = listSessions(db, filter);
  const total = countSessions(db, filter);
  const sourceCounts = countSourcesBySession(
    db,
    rows.map((row) => row.id),
  );

  return Response.json({
    total,
    limit,
    offset,
    sessions: rows.map((row) => ({
      id: row.id,
      question: row.question,
      questionType: row.questionType,
      status: row.status,
      modelId: row.modelId,
      durationMs: row.durationMs,
      costUsd: row.costUsd,
      createdAt: row.createdAt,
      sourceCount: sourceCounts[row.id] ?? 0,
    })),
  });
}

function parseBoundInt(raw: string | null, fallback: number, min: number, max: number): number {
  if (raw === null || raw === "") return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

function parseEnum<T extends string>(raw: string | null, allowed: readonly T[]): T | undefined {
  if (raw === null || raw === "") return undefined;
  return allowed.includes(raw as T) ? (raw as T) : undefined;
}

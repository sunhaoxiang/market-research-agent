/**
 * SSE 解析与研究流消费（§12.4，P1-11）。
 *
 * 手写解析器而不用 `EventSource`：后者只支持 GET，不能带请求体，
 * 也没法设置 `X-Internal-Token` 这类头。研究请求必须是 POST（问题正文可能很长）。
 *
 * 服务端（Route Handler 落库）与浏览器共用 `parseFrames`——两侧对分帧的理解
 * 必须一致，否则会出现「落库了但前端没显示」这种最难查的不一致。
 */

import type { ResearchEvent } from "@mra/shared";

export type SseFrame = {
  id: string | null;
  event: string | null;
  data: string;
};

/**
 * 把字节流解析成 SSE 帧。
 *
 * 用 `getReader()` 而不是 `for await (const chunk of body)`：
 * `ReadableStream` 的异步迭代协议在浏览器里支持并不完整（Chrome 至今没实现），
 * 而这个函数要同时跑在 Node 和浏览器里。
 */
export async function* parseFrames(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();

      if (done) {
        // 流结束时把残留内容当作最后一帧。正常情况下服务端会以空行收尾，
        // 走不到这里；连接被中途截断时它能保住已经完整的那一帧
        buffer += decoder.decode();
        const tail = toFrame(normalizeNewlines(buffer));
        if (tail) yield tail;
        return;
      }

      buffer += decoder.decode(value, { stream: true });

      // 规范允许 CR / LF / CRLF 三种行终止符，统一成 LF 再分帧。
      // 末尾孤立的 CR 要留着——它可能是下一个 chunk 里 CRLF 的前半截，
      // 现在就规范化会凭空多出一个空行，把一帧劈成两半
      const danglingCr = buffer.endsWith("\r");
      const head = danglingCr ? buffer.slice(0, -1) : buffer;
      buffer = normalizeNewlines(head) + (danglingCr ? "\r" : "");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const frame = toFrame(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (frame) yield frame;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function normalizeNewlines(text: string): string {
  return text.replace(/\r\n?/g, "\n");
}

/** 解析单个帧块。没有 data 字段的块（心跳注释、纯 id）返回 null。 */
function toFrame(block: string): SseFrame | null {
  let id: string | null = null;
  let event: string | null = null;
  const data: string[] = [];

  for (const line of block.split("\n")) {
    // 以 `:` 开头的是注释（服务端用它撑开响应头），整行忽略
    if (line === "" || line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // 规范要求只去掉冒号后的**一个**空格；用 trim() 会吃掉数据本身的缩进
    const rawValue = colon === -1 ? "" : line.slice(colon + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;

    switch (field) {
      case "id":
        id = value;
        break;
      case "event":
        event = value;
        break;
      case "data":
        // 多行 data 用 LF 拼接。我们的服务端不会产生多行（JSON 里换行是转义的），
        // 但规范允许，别的实现可能会用
        data.push(value);
        break;
      default:
        break;
    }
  }

  if (data.length === 0) return null;
  return { id, event, data: data.join("\n") };
}

/**
 * 把帧流解析成研究事件，跳过无法解析的帧。
 *
 * 单个坏帧不应该终止整条流：后面可能还有终态事件，而前端要靠它收尾。
 * 直接抛异常的话，一个字段的序列化问题就会让整次研究在 UI 上永远转圈。
 */
export async function* toResearchEvents(
  frames: AsyncIterable<SseFrame>,
  onMalformed?: (frame: SseFrame, error: unknown) => void,
): AsyncGenerator<ResearchEvent> {
  for await (const frame of frames) {
    try {
      yield JSON.parse(frame.data) as ResearchEvent;
    } catch (error) {
      onMalformed?.(frame, error);
    }
  }
}

export type StreamResearchInput = {
  question: string;
  modelId?: string;
};

export type StreamResearchOptions = {
  signal?: AbortSignal;
  onSessionId?: (sessionId: string) => void;
};

/**
 * 浏览器侧入口：发起研究并逐个产出事件。
 *
 * 请求打到 Next 的 `/api/research` 而不是 Python 服务：后者只监听 127.0.0.1，
 * 且落库要发生在 BFF 层（§11.1：Next 是 DB 的唯一 writer）。
 */
export async function* streamResearch(
  input: StreamResearchInput,
  options: StreamResearchOptions = {},
): AsyncGenerator<ResearchEvent> {
  const response = await fetch("/api/research", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(input),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    // 错误响应是 JSON（§16.3）而不是事件流——模型不可用、Agent 服务没起来
    // 这类问题在流打开之前就已经确定了
    const detail: unknown = await response.json().catch(() => null);
    throw new ResearchStreamError(response.status, detail);
  }

  const sessionId = response.headers.get("X-Session-Id");
  if (sessionId) options.onSessionId?.(sessionId);

  yield* toResearchEvents(parseFrames(response.body));
}

export class ResearchStreamError extends Error {
  constructor(
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(`研究流请求失败（HTTP ${status}）`);
    this.name = "ResearchStreamError";
  }
}

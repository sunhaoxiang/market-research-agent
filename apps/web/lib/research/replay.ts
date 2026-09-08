/**
 * 从 BFF 拉已落库事件，进行中的会话按 `after` 续订（P6-6）。
 *
 * 浏览器断线后 Next 仍在消费 Python 并写库，所以续订轮询 DB 即可，
 * 不必再接一条 Python SSE。
 */

import type { ResearchEvent } from "@mra/shared";

import type { ResearchStore } from "@/lib/research/store";

const POLL_MS = 800;

const ACTIVE_DB_STATUSES = new Set(["pending", "planning", "researching", "checking", "writing"]);

export type SessionEventsPage = {
  status: string;
  events: ResearchEvent[];
};

export async function fetchSessionEvents(
  sessionId: string,
  afterSeq = -1,
  signal?: AbortSignal,
): Promise<SessionEventsPage> {
  const response = await fetch(
    `/api/research/${encodeURIComponent(sessionId)}/events?after=${afterSeq}`,
    { signal, cache: "no-store" },
  );
  if (!response.ok) {
    throw new Error(`无法读取会话事件（HTTP ${response.status}）`);
  }
  return (await response.json()) as SessionEventsPage;
}

export function isActiveDbStatus(status: string): boolean {
  return ACTIVE_DB_STATUSES.has(status);
}

/** 把已落库事件喂进 store；若会话仍在跑则轮询后续 seq。 */
export async function replayAndFollow(
  sessionId: string,
  store: ResearchStore,
  signal: AbortSignal,
): Promise<void> {
  const first = await fetchSessionEvents(sessionId, -1, signal);
  store.applyAll(first.events);
  if (!isActiveDbStatus(first.status) || signal.aborted) return;

  let lastSeq = store.getSnapshot().lastSeq;
  while (!signal.aborted) {
    await sleep(POLL_MS, signal);
    if (signal.aborted) return;
    const page = await fetchSessionEvents(sessionId, lastSeq, signal);
    store.applyAll(page.events);
    lastSeq = store.getSnapshot().lastSeq;
    const view = store.getSnapshot();
    if (!isActiveDbStatus(page.status) || isTerminalView(view.status)) return;
  }
}

function isTerminalView(status: string): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

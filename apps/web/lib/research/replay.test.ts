import { afterEach, describe, expect, it, vi } from "vitest";

import { isActiveDbStatus, replayAndFollow } from "@/lib/research/replay";
import { createResearchStore } from "@/lib/research/store";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("isActiveDbStatus", () => {
  it("进行中的阶段要续订，终态不要", () => {
    expect(isActiveDbStatus("researching")).toBe(true);
    expect(isActiveDbStatus("completed")).toBe(false);
    expect(isActiveDbStatus("failed")).toBe(false);
  });
});

describe("replayAndFollow", () => {
  it("已完成会话只回放一次，不再轮询", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "completed",
        events: [
          {
            seq: 1,
            session_id: "sess-1",
            ts: "2026-09-08T00:00:00.000Z",
            type: "session_started",
            message: null,
            payload: { question: "Q", model_id: "m" },
          },
          {
            seq: 2,
            session_id: "sess-1",
            ts: "2026-09-08T00:00:01.000Z",
            type: "session_completed",
            message: null,
            payload: {
              duration_ms: 10,
              usage: { input: 0, output: 0, cached: 0 },
              cost_usd: null,
            },
          },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const store = createResearchStore();
    await replayAndFollow("sess-1", store, new AbortController().signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().status).toBe("completed");
    expect(store.getSnapshot().question).toBe("Q");
  });
});

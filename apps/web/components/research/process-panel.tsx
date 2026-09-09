"use client";

import { useState } from "react";

import { ActivityPanel } from "@/components/research/activity-panel";
import type { ResearchViewState } from "@/lib/research/state";

export function ProcessPanel({ state, now }: { state: ResearchViewState; now: number }) {
  const failed = state.taskIds.some((id) => state.tasks[id]?.status === "failed");
  const autoOpen = state.status === "running" || failed;
  const epoch = `${state.sessionId ?? "none"}:${autoOpen ? "open" : "shut"}`;
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const [seenEpoch, setSeenEpoch] = useState(epoch);
  if (seenEpoch !== epoch) {
    setSeenEpoch(epoch);
    setUserOpen(null);
  }
  const open = userOpen ?? autoOpen;

  return (
    <div className="rounded-md border border-zinc-200 dark:border-zinc-800">
      <button
        type="button"
        aria-expanded={open}
        aria-controls="research-process"
        onClick={() => setUserOpen(!open)}
        className="focus-visible:ring-accent flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm hover:bg-zinc-50 focus-visible:ring-2 focus-visible:outline-none dark:hover:bg-zinc-900"
      >
        <span className="font-medium">研究过程</span>
        <span className="text-xs text-zinc-400">{open ? "收起" : "展开"}</span>
      </button>
      <div
        id="research-process"
        hidden={!open}
        className="border-t border-zinc-200 px-3 py-3 dark:border-zinc-800"
      >
        <ActivityPanel state={state} now={now} hideHeading />
      </div>
    </div>
  );
}

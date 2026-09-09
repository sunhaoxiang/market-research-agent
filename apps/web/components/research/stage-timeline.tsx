"use client";

import { StageBar } from "@/components/research/stage-bar";
import { resolveStageSpans } from "@/lib/research/stages";
import type { ResearchViewState } from "@/lib/research/state";

export function StageTimeline({ state, now }: { state: ResearchViewState; now: number }) {
  const spans = resolveStageSpans(state, now);
  return <StageBar spans={spans} />;
}

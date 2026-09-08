"use client";

import { liveAnnouncement } from "@/lib/research/live-status";
import type { ResearchViewState } from "@/lib/research/state";

/** 始终挂在主区，不跟「研究过程」一起收起。 */
export function LiveStatus({ state }: { state: ResearchViewState }) {
  return (
    <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
      {liveAnnouncement(state)}
    </p>
  );
}

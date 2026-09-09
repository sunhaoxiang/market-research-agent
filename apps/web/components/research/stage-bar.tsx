/**
 * 阶段色条。服务端、客户端都能用——本身没有 hook。
 */

import type { Stage } from "@mra/shared";

import { STAGE_LABELS } from "@/lib/research/stages";
import { cn, formatDuration } from "@/lib/utils";

export const STAGE_TONE: Record<Stage, string> = {
  planning: "bg-zinc-400 dark:bg-zinc-500",
  researching: "bg-blue-500",
  checking: "bg-violet-500",
  writing: "bg-emerald-500",
};

/** 太短的阶段也留一点宽度，否则 2 秒的规划会被 10 分钟的研究挤没。 */
const MIN_SHARE = 0.06;

export type StageBarSpan = {
  stage: Stage;
  durationMs: number;
  active?: boolean;
};

export function StageBar({ spans, label }: { spans: StageBarSpan[]; label?: string }) {
  if (spans.length === 0) return null;

  const total = spans.reduce((sum, span) => sum + span.durationMs, 0);
  const weights = spans.map((span) => {
    if (total <= 0) return 1;
    return Math.max(span.durationMs / total, MIN_SHARE);
  });

  return (
    <section className="space-y-2" aria-label={label ?? "阶段耗时"}>
      <div
        aria-hidden
        className="flex h-2 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
      >
        {spans.map((span, index) => (
          <div
            key={`${span.stage}-${index}`}
            className={cn(STAGE_TONE[span.stage], span.active && "motion-safe:animate-pulse")}
            style={{ flexGrow: weights[index], flexBasis: 0 }}
          />
        ))}
      </div>

      <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
        {spans.map((span, index) => (
          <li key={`${span.stage}-${index}`} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className={cn("inline-block size-2 rounded-full", STAGE_TONE[span.stage])}
            />
            <span>
              {STAGE_LABELS[span.stage]}
              {span.active ? " · 进行中" : ""} · {formatDuration(span.durationMs)}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

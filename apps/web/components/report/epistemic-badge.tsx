import type { Claim, EpistemicType } from "@mra/shared";

import { cn } from "@/lib/utils";

/**
 * §13.2：事实无标记；分析蓝、推测黄、预测橙、观点灰。
 * `source_backed_fact` 仍是事实，不另打徽标。
 */
const MARKS: Partial<Record<EpistemicType, { label: string; className: string }>> = {
  analysis: {
    label: "分析",
    className: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  },
  inference: {
    label: "推测",
    className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  },
  prediction: {
    label: "预测",
    className: "bg-orange-100 text-orange-900 dark:bg-orange-950 dark:text-orange-200",
  },
  opinion: {
    label: "观点",
    className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  },
};

export type EpistemicMark = {
  type: EpistemicType;
  label: string;
  className: string;
};

export function markFor(type: EpistemicType): EpistemicMark | null {
  const mark = MARKS[type];
  return mark === undefined ? null : { type, ...mark };
}

export function marksForClaims(claims: readonly Claim[]): EpistemicMark[] {
  const seen = new Set<EpistemicType>();
  const marks: EpistemicMark[] = [];
  for (const claim of claims) {
    if (seen.has(claim.epistemic_type)) continue;
    const mark = markFor(claim.epistemic_type);
    if (mark === null) continue;
    seen.add(claim.epistemic_type);
    marks.push(mark);
  }
  return marks;
}

export function EpistemicBadge({ mark }: { mark: EpistemicMark }) {
  return (
    <span
      className={cn(
        "inline-flex rounded px-1.5 py-0.5 text-[10px] font-medium tracking-wide",
        mark.className,
      )}
    >
      {mark.label}
    </span>
  );
}

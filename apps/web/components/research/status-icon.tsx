import type { TaskNodeStatus } from "@/lib/research/state";
import { cn } from "@/lib/utils";

/**
 * §13.2 的五种节点状态。
 *
 * 图形 + `aria-label` 双重表达，不只靠颜色：§13.2 的无障碍要求。
 * running 用脉冲动画，是整个面板里唯一的动效——它承担"系统还在工作"的信号。
 */
const LOOK: Record<TaskNodeStatus, { glyph: string; label: string; className: string }> = {
  pending: { glyph: "○", label: "待执行", className: "text-zinc-300 dark:text-zinc-600" },
  running: { glyph: "●", label: "进行中", className: "animate-pulse text-blue-500" },
  completed: { glyph: "✓", label: "已完成", className: "text-emerald-500" },
  failed: { glyph: "⚠", label: "失败", className: "text-amber-500" },
  skipped: { glyph: "⊘", label: "已跳过", className: "text-zinc-400 dark:text-zinc-500" },
};

export function StatusIcon({ status, className }: { status: TaskNodeStatus; className?: string }) {
  const look = LOOK[status];
  return (
    <span
      role="img"
      aria-label={look.label}
      className={cn("inline-block w-4 shrink-0 text-center leading-5", look.className, className)}
    >
      {look.glyph}
    </span>
  );
}

export function statusLabel(status: TaskNodeStatus): string {
  return LOOK[status].label;
}

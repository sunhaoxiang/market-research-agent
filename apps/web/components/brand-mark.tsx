import { cn } from "@/lib/utils";

/** 品牌 mark：上升折线。favicon / og 用同一套几何，只是尺寸不同。 */
export function BrandMark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      aria-hidden
      className={cn("text-accent shrink-0", className)}
    >
      <rect width="32" height="32" rx="8" fill="currentColor" />
      <polyline
        points="6.5,21.5 12,15.5 16.5,19 25.5,9.5"
        fill="none"
        stroke="var(--accent-contrast)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

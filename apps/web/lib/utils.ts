import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 合并 className。shadcn/ui 的标准约定（§4 选的是复制式组件，不是依赖）。
 *
 * `twMerge` 不只是拼接：它会让后来的 Tailwind 类覆盖同组的前者，
 * 这样 `<Button className="px-8">` 才能真的盖掉组件内置的 `px-4`。
 * 单纯 `clsx` 拼出来的是 `px-4 px-8`，谁生效取决于 CSS 里的顺序。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** 毫秒转人类可读的耗时。 */
export function formatDuration(ms: number | null): string {
  if (ms === null) return "--";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** token 数缩写，避免表头被 1234567 撑开。 */
export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  return `${(count / 1000).toFixed(1)}k`;
}

/** 成本。研究成本常在 $0.005 量级，固定两位小数会全部显示成 $0.00。 */
export function formatCost(usd: number | null): string {
  if (usd === null) return "--";
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(4)}` : `$${usd.toFixed(2)}`;
}

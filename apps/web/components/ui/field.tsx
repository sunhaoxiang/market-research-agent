import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const CONTROL = [
  "w-full rounded-md border border-zinc-200 bg-transparent px-3 py-2 text-sm",
  "placeholder:text-zinc-400 focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none",
  "disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-800",
];

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(CONTROL, "resize-none", className)} {...props} />;
}

/**
 * 原生 `<select>` 而不是 Radix：模型选择器是个单选下拉，原生控件自带键盘导航、
 * 屏幕阅读器支持和移动端的系统选择器。等 P2 真的需要带图标和分组描述的
 * 复杂下拉时再换 shadcn 的 Select。
 */
export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(CONTROL, "cursor-pointer py-1.5", className)} {...props} />;
}

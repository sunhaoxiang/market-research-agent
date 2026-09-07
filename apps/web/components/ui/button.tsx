import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

type ButtonProps = ComponentProps<"button"> & {
  variant?: "primary" | "ghost";
};

export function Button({ className, variant = "primary", ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition",
        "focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:pointer-events-none disabled:opacity-50",
        variant === "primary" &&
          "bg-zinc-900 text-zinc-50 hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200",
        variant === "ghost" && "hover:bg-zinc-100 dark:hover:bg-zinc-800",
        className,
      )}
      {...props}
    />
  );
}

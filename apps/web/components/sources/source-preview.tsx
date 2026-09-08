import type { Source } from "@mra/shared";

import { formatTimestamp } from "@/lib/utils";

export function SourceHoverBody({ source }: { source: Source }) {
  return (
    <span className="block space-y-1">
      <span className="block font-medium">{source.title ?? source.url}</span>
      {source.domain && <span className="block text-zinc-500">{source.domain}</span>}
      <span className="block text-zinc-500">{formatTimestamp(source.retrieved_at)}</span>
      {source.excerpt && (
        <span className="mt-1 block line-clamp-4 text-zinc-600 dark:text-zinc-300">
          {source.excerpt}
        </span>
      )}
    </span>
  );
}

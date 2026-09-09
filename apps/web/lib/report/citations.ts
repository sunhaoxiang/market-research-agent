/**
 * 把正文里的 `[n]` 变成可点击的来源锚点。
 *
 * 与后端 guardrail 同一条规则：跳过 markdown 链接 `[n](url)`。
 * 紧挨着的 `[1][6][10]` 收成一条链接，前端画成一组 pill，避免正文里出现数字堆。
 */

const BARE_CITATION_RUN = /(?:\[(\d+)\](?!\())+/g;

export const SOURCE_ID_PREFIX = "source-";
export const CITE_HREF_PREFIX = `#${SOURCE_ID_PREFIX}`;

export function sourceElementId(index: number): string {
  return `${SOURCE_ID_PREFIX}${index}`;
}

export function citationHref(index: number): string {
  return citationGroupHref([index]);
}

export function citationGroupHref(indices: readonly number[]): string {
  return `${CITE_HREF_PREFIX}${indices.join(",")}`;
}

/** 把裸 `[n]` 写成指向 Source Panel 的 markdown 链接；相邻的收成一组。 */
export function linkCitations(markdown: string): string {
  return markdown.replace(BARE_CITATION_RUN, (run) => {
    const indices = [...run.matchAll(/\[(\d+)\]/g)].map((item) => Number(item[1]));
    return `[${indices.join(",")}](${citationGroupHref(indices)})`;
  });
}

export function parseCitationIndices(href: string | undefined | null): number[] | null {
  if (!href?.startsWith(CITE_HREF_PREFIX)) return null;
  const raw = href.slice(CITE_HREF_PREFIX.length);
  if (!raw) return null;
  const indices = raw.split(",").map((part) => Number(part));
  if (indices.length === 0 || indices.some((index) => !Number.isInteger(index) || index < 1)) {
    return null;
  }
  return indices;
}

export function parseCitationHref(href: string | undefined | null): number | null {
  const indices = parseCitationIndices(href);
  return indices?.length === 1 ? (indices[0] ?? null) : null;
}

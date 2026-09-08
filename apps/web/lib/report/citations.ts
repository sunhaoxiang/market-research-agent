/**
 * 把正文里的 `[n]` 变成可点击的来源锚点。
 *
 * 与后端 guardrail 同一条规则：跳过 markdown 链接 `[n](url)`。
 */

const BARE_CITATION = /\[(\d+)\](?!\()/g;

export const SOURCE_ID_PREFIX = "source-";
export const CITE_HREF_PREFIX = `#${SOURCE_ID_PREFIX}`;

export function sourceElementId(index: number): string {
  return `${SOURCE_ID_PREFIX}${index}`;
}

export function citationHref(index: number): string {
  return `${CITE_HREF_PREFIX}${index}`;
}

/** 把裸 `[n]` 写成指向 Source Panel 的 markdown 链接。 */
export function linkCitations(markdown: string): string {
  return markdown.replace(
    BARE_CITATION,
    (_all, value: string) => `[${value}](${citationHref(Number(value))})`,
  );
}

export function parseCitationHref(href: string | undefined | null): number | null {
  if (!href?.startsWith(CITE_HREF_PREFIX)) return null;
  const index = Number(href.slice(CITE_HREF_PREFIX.length));
  if (!Number.isInteger(index) || index < 1) return null;
  return index;
}

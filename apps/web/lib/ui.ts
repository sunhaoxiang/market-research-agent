/**
 * 页面区块的同一套外壳。
 *
 * 评审里散的原因是「研究过程」「数值冲突」有框，摘要另用 ring，
 * 其余靠间距和虚线。形状只留两种：普通 surface，警告 notice。
 */
const CHROME =
  "rounded-lg shadow-[0_1px_2px_rgba(9,9,11,0.04)] dark:shadow-[0_1px_2px_rgba(0,0,0,0.45)]";

export const surface = `${CHROME} border border-border bg-background`;

export const surfaceMuted = `${CHROME} border border-border bg-muted`;

export const notice = `${CHROME} border border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300`;

/**
 * 给屏幕阅读器的会话播报（P6-3 / §13.2）。
 *
 * 不把耗时、成本编进去：那些会随 ticker 每秒变，live region 会被刷成倒计时。
 * 过程面板可以收起，所以播报必须独立于「研究过程」是否展开。
 */

import { SESSION_STATUS_LABELS, STAGE_LABELS, taskProgress } from "@/lib/research/stages";
import type { ResearchViewState } from "@/lib/research/state";

export function liveAnnouncement(state: ResearchViewState): string {
  if (state.status === "idle") return "";

  const parts: string[] = [];
  if (state.status === "running" && state.stage) {
    parts.push(`${SESSION_STATUS_LABELS.running}，${STAGE_LABELS[state.stage]}`);
  } else {
    parts.push(SESSION_STATUS_LABELS[state.status]);
  }

  const progress = taskProgress(state);
  if (progress) parts.push(progress);
  if (state.lastMessage) parts.push(state.lastMessage);
  if (state.error) parts.push(`${state.error.code}：${state.error.message}`);
  const budget = state.warnings.find((warning) => warning.code === "budget_exhausted");
  if (budget) parts.push(budget.message);
  if (state.hasGap) parts.push("事件有缺失，刷新可查看完整结果");
  return parts.join("。");
}

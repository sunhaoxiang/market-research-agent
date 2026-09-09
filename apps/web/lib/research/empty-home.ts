import type { QuestionType, SessionStatus } from "@/db/schema";

export type ExamplePrompt = {
  type: QuestionType;
  label: string;
  title: string;
  question: string;
};

export type RecentSessionPreview = {
  id: string;
  question: string;
  status: SessionStatus;
  questionType: QuestionType | null;
  createdAt: number;
};

export const EXAMPLE_PROMPTS: readonly ExamplePrompt[] = [
  {
    type: "crypto",
    label: "加密",
    title: "HYPE 协议收入",
    question: "Hyperliquid 的协议收入最近怎么样？HYPE 值得关注吗？",
  },
  {
    type: "compare",
    label: "对比",
    title: "Solana 与 Sui",
    question: "比较 Solana 和 Sui 的生态活跃度",
  },
  {
    type: "stock",
    label: "美股",
    title: "英伟达财报",
    question: "英伟达最新一季财报的关键信号是什么？",
  },
];

export const QUESTION_TYPE_LABELS: Record<QuestionType, string> = {
  crypto: "加密",
  stock: "美股",
  macro: "宏观",
  compare: "对比",
  generic: "综合",
};

export const SESSION_ROW_STATUS_LABELS: Record<SessionStatus, string> = {
  pending: "等待",
  planning: "规划中",
  researching: "研究中",
  checking: "核查中",
  writing: "撰写中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const HOME_DISCLAIMER =
  "仅供参考，不构成投资建议。数据来自 Tavily、CoinGecko、DefiLlama、SEC EDGAR。";

export function formatSessionDay(ms: number): string {
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return "--";
  const yyyy = date.getUTCFullYear();
  const mm = String(date.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(date.getUTCDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

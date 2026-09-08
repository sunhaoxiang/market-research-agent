import Link from "next/link";
import type { Route } from "next";

import { AppHeader } from "@/components/research/app-header";
import { Select } from "@/components/ui/field";
import { getDb } from "@/db/client";
import { countSessions, countSourcesBySession, listSessions } from "@/db/queries/sessions";
import {
  QUESTION_TYPES,
  SESSION_STATUSES,
  type QuestionType,
  type SessionStatus,
} from "@/db/schema";
import { formatCost, formatDuration } from "@/lib/utils";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 20;

const QUESTION_LABELS: Record<QuestionType, string> = {
  crypto: "加密",
  stock: "美股",
  macro: "宏观",
  compare: "对比",
  generic: "综合",
};

const STATUS_LABELS: Record<SessionStatus, string> = {
  pending: "等待",
  planning: "规划中",
  researching: "研究中",
  checking: "核查中",
  writing: "撰写中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

type Search = {
  status?: string;
  questionType?: string;
  modelId?: string;
  page?: string;
};

export default async function HistoryPage({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
  const status = parseEnum(params.status, SESSION_STATUSES);
  const questionType = parseEnum(params.questionType, QUESTION_TYPES);
  const modelId = params.modelId?.trim() || undefined;
  const page = Math.max(1, Number(params.page) || 1);
  const offset = (page - 1) * PAGE_SIZE;

  const db = getDb();
  const filter = { status, questionType, modelId, limit: PAGE_SIZE, offset };
  const rows = listSessions(db, filter);
  const total = countSessions(db, { status, questionType, modelId });
  const sourceCounts = countSourcesBySession(
    db,
    rows.map((row) => row.id),
  );
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main id="main" className="mx-auto max-w-6xl px-6 py-12">
      <AppHeader current="history" />

      <form method="get" className="mb-6 flex flex-wrap items-end gap-3 text-sm">
        <label className="space-y-1">
          <span className="block text-xs text-zinc-500">状态</span>
          <Select name="status" defaultValue={status ?? ""} className="w-36">
            <option value="">全部</option>
            {SESSION_STATUSES.map((item) => (
              <option key={item} value={item}>
                {STATUS_LABELS[item]}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-zinc-500">类型</span>
          <Select name="questionType" defaultValue={questionType ?? ""} className="w-32">
            <option value="">全部</option>
            {QUESTION_TYPES.map((item) => (
              <option key={item} value={item}>
                {QUESTION_LABELS[item]}
              </option>
            ))}
          </Select>
        </label>
        <label className="space-y-1">
          <span className="block text-xs text-zinc-500">模型</span>
          <input
            name="modelId"
            defaultValue={modelId ?? ""}
            placeholder="全部"
            className="w-56 rounded-md border border-zinc-200 bg-transparent px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none dark:border-zinc-800"
          />
        </label>
        <button
          type="submit"
          className="rounded-md border border-zinc-200 px-3 py-1.5 hover:bg-zinc-50 focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none dark:border-zinc-800 dark:hover:bg-zinc-900"
        >
          筛选
        </button>
      </form>

      {rows.length === 0 ? (
        <p className="text-sm text-zinc-500">还没有研究记录。</p>
      ) : (
        <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {rows.map((row) => (
            <li key={row.id}>
              <Link
                href={`/?session=${encodeURIComponent(row.id)}` as Route}
                className="block py-3 hover:bg-zinc-50 focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none dark:hover:bg-zinc-900/40"
              >
                <p className="font-medium">{row.question}</p>
                <p className="mt-1 text-xs text-zinc-500">
                  {STATUS_LABELS[row.status]}
                  {row.questionType ? ` · ${QUESTION_LABELS[row.questionType]}` : ""}
                  {` · ${row.modelId}`}
                  {` · ${formatDuration(row.durationMs)}`}
                  {` · ${formatCost(row.costUsd)}`}
                  {` · ${sourceCounts[row.id] ?? 0} 来源`}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {pages > 1 && (
        <p className="mt-6 flex gap-3 text-sm">
          {page > 1 && (
            <Link
              href={historyHref({ ...params, page: String(page - 1) })}
              className="text-zinc-500 hover:underline focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none"
            >
              上一页
            </Link>
          )}
          <span className="text-zinc-400">
            {page} / {pages}
          </span>
          {page < pages && (
            <Link
              href={historyHref({ ...params, page: String(page + 1) })}
              className="text-zinc-500 hover:underline focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:outline-none"
            >
              下一页
            </Link>
          )}
        </p>
      )}
    </main>
  );
}

function parseEnum<T extends string>(
  raw: string | undefined,
  allowed: readonly T[],
): T | undefined {
  if (!raw) return undefined;
  return allowed.includes(raw as T) ? (raw as T) : undefined;
}

function historyHref(params: Search): Route {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.questionType) search.set("questionType", params.questionType);
  if (params.modelId) search.set("modelId", params.modelId);
  if (params.page && params.page !== "1") search.set("page", params.page);
  const query = search.toString();
  return (query ? `/history?${query}` : "/history") as Route;
}

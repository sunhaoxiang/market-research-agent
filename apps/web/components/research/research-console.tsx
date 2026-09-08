"use client";

import { useEffect, useRef, useState } from "react";

import type { ResearchEvent } from "@mra/shared";

import { ReportViewer } from "@/components/report/report-viewer";
import { SourcePanel } from "@/components/sources/source-panel";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/field";
import { useResearchState, useResearchStore, useTicker } from "@/lib/research/store";
import { ResearchStreamError, streamResearch } from "@/lib/sse";

import { ActivityPanel } from "./activity-panel";
import { type ModelOption, ModelSelector } from "./model-selector";
import { SessionHeader } from "./session-header";

const EXAMPLES = [
  "Hyperliquid 的协议收入最近怎么样？HYPE 值得关注吗？",
  "比较 Solana 和 Sui 的生态活跃度",
  "英伟达最新一季财报的关键信号是什么？",
];

export function ResearchConsole({ models }: { models: ModelOption[] }) {
  const store = useResearchStore();
  const state = useResearchState(store);
  const [question, setQuestion] = useState("");
  const [modelId, setModelId] = useState("");
  const [activeCitation, setActiveCitation] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const running = state.status === "running";

  useEffect(() => {
    const sessionId = new URLSearchParams(window.location.search).get("session");
    if (!sessionId) return;
    let cancelled = false;
    void (async () => {
      const response = await fetch(`/api/research/${encodeURIComponent(sessionId)}/events`);
      if (!response.ok || cancelled) return;
      const body = (await response.json()) as { events?: ResearchEvent[] };
      if (cancelled || !Array.isArray(body.events)) return;
      store.reset();
      for (const event of body.events) store.apply(event);
    })();
    return () => {
      cancelled = true;
    };
  }, [store]);

  async function submit() {
    const trimmed = question.trim();
    if (!trimmed || running) return;

    store.reset();
    setActiveCitation(null);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamResearch(
        { question: trimmed, modelId: modelId || undefined },
        { signal: controller.signal, onSessionId: rememberSession },
      )) {
        store.apply(event);
      }
    } catch (error) {
      // 中止是用户主动的，不是故障。研究本身仍在后端跑完并落库（§11.2），
      // 所以这里也不该显示成失败
      if (controller.signal.aborted) return;

      const detail =
        error instanceof ResearchStreamError
          ? ((error.detail as { error?: { code?: string; message?: string } } | null)?.error ??
            null)
          : null;
      store.failStream(
        detail?.code ?? "STREAM_ERROR",
        detail?.message ?? (error instanceof Error ? error.message : "研究流中断"),
      );
    }
  }

  // 只在运行中走时钟，避免空闲页面每秒重渲染
  const now = useTicker(running);

  return (
    <div className="space-y-6">
      <form
        onSubmit={(submitEvent) => {
          submitEvent.preventDefault();
          void submit();
        }}
        className="space-y-3"
      >
        <Textarea
          value={question}
          onChange={(changeEvent) => setQuestion(changeEvent.target.value)}
          onKeyDown={(keyEvent) => {
            // Cmd/Ctrl+Enter 提交，单独 Enter 保留换行——问题可能是多行的
            if (keyEvent.key === "Enter" && (keyEvent.metaKey || keyEvent.ctrlKey)) {
              keyEvent.preventDefault();
              void submit();
            }
          }}
          placeholder="问一个 Crypto / 美股 / 宏观的研究问题…"
          rows={3}
          maxLength={2000}
          disabled={running}
          aria-label="研究问题"
        />

        <div className="flex flex-wrap items-center justify-between gap-3">
          <ModelSelector models={models} value={modelId} onChange={setModelId} disabled={running} />

          <div className="flex items-center gap-2">
            {running && (
              <Button
                type="button"
                variant="ghost"
                onClick={() => abortRef.current?.abort()}
                title="停止接收事件。研究会在后端跑完并落库"
              >
                停止查看
              </Button>
            )}
            <Button type="submit" disabled={running || question.trim().length === 0}>
              {running ? "研究中…" : "开始研究"}
            </Button>
          </div>
        </div>
      </form>

      {state.status === "idle" ? (
        <div className="space-y-2">
          <p className="text-xs font-medium tracking-wide text-zinc-500 uppercase">试试这些</p>
          <ul className="space-y-1">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => setQuestion(example)}
                  className="text-left text-sm text-zinc-500 underline-offset-2 hover:text-zinc-900 hover:underline dark:hover:text-zinc-100"
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="space-y-5 border-t border-zinc-200 pt-5 dark:border-zinc-800">
          <SessionHeader state={state} now={now} />

          {state.lastMessage && (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">{state.lastMessage}</p>
          )}

          {state.error && (
            <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
              {state.error.code}: {state.error.message}
            </p>
          )}

          <div className="grid gap-8 lg:grid-cols-[minmax(0,22rem)_1fr]">
            <div className="space-y-4">
              <ActivityPanel state={state} now={now} />
              <SourcePanel
                sources={state.sources}
                activeIndex={activeCitation}
                onSelect={setActiveCitation}
              />
            </div>

            {state.report ? (
              <ReportViewer
                report={state.report}
                sources={state.sources}
                claims={state.claims}
                conflicts={state.conflicts}
                activeIndex={activeCitation}
                onCite={setActiveCitation}
              />
            ) : (
              <div className="space-y-3">
                <h2 className="text-xs font-medium tracking-wide text-zinc-500 uppercase">
                  Report
                </h2>
                {state.plan ? (
                  <div className="space-y-3">
                    <p className="text-sm leading-relaxed">{state.plan.interpretation}</p>
                    <ol className="list-decimal space-y-1 pl-5 text-sm text-zinc-600 dark:text-zinc-400">
                      {state.plan.tasks.map((task) => (
                        <li key={task.id}>{task.objective}</li>
                      ))}
                    </ol>
                    {state.plan.assumptions.length > 0 && (
                      <div>
                        <p className="text-xs font-medium text-zinc-500">研究假设</p>
                        <ul className="list-disc pl-5 text-xs text-zinc-500">
                          {state.plan.assumptions.map((assumption) => (
                            <li key={assumption}>{assumption}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <p className="border-t border-dashed border-zinc-200 pt-3 text-xs text-zinc-400 dark:border-zinc-800">
                      {state.stage === "writing" ? "正在撰写研究报告…" : "报告生成后显示。"}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-zinc-400">计划生成后显示。</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function rememberSession(sessionId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("session", sessionId);
  window.history.replaceState(null, "", url);
}

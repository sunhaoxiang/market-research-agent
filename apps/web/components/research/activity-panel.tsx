"use client";

import type { ResearchViewState, TaskNode, ToolCallNode } from "@/lib/research/state";
import { AGENT_LABELS } from "@/lib/research/stages";
import { cn, formatDuration } from "@/lib/utils";

import { StatusIcon, statusLabel } from "./status-icon";

export { AGENT_LABELS };

/** running 节点显示实时耗时，已完成的显示最终耗时（§13.2）。 */
function elapsed(node: { durationMs: number | null; startedAtMs: number | null }, now: number) {
  if (node.durationMs !== null) return formatDuration(node.durationMs);
  if (node.startedAtMs === null || now === 0) return "--";
  return formatDuration(Math.max(0, now - node.startedAtMs));
}

function ToolCallRow({ call, now }: { call: ToolCallNode; now: number }) {
  const status =
    call.status === "running" ? "running" : call.status === "failed" ? "failed" : "completed";

  return (
    <li className="flex items-baseline gap-2 py-0.5 text-xs">
      <StatusIcon status={status} />
      <span className="font-mono text-zinc-600 dark:text-zinc-400">{call.tool}</span>
      {call.cacheHit && (
        <span title="缓存命中" aria-label="缓存命中" className="text-amber-500">
          ⚡
        </span>
      )}
      <span className="text-zinc-400">
        {call.provider ? `${call.provider} · ` : ""}
        {elapsed(call, now)}
      </span>
      {call.errorMessage && <span className="text-amber-600">{call.errorMessage}</span>}
    </li>
  );
}

function TaskRow({ task, now }: { task: TaskNode; now: number }) {
  // §13.2 的折叠策略：完成的任务收成一行，失败的保持展开——
  // 失败详情正是用户此刻最需要看的东西
  const expanded = task.status === "running" || task.status === "failed";

  return (
    <li>
      <div className="flex items-baseline gap-2">
        <StatusIcon status={task.status} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-medium">{AGENT_LABELS[task.agent] ?? task.agent}</span>
            <span className="text-xs text-zinc-400">{elapsed(task, now)}</span>
            {task.status === "completed" && task.sourceCount !== null && (
              <span className="text-xs text-zinc-400">
                {task.claimCount} 条陈述 · {task.sourceCount} 来源
              </span>
            )}
          </div>

          <p
            className={cn(
              "text-xs leading-relaxed text-zinc-500",
              // 未展开时目标截断成一行，避免长 objective 把面板撑爆
              !expanded && "truncate",
            )}
          >
            {task.objective}
          </p>

          {expanded && task.progress && (
            <p className="text-accent-text mt-0.5 text-xs">{task.progress}</p>
          )}
          {task.status === "failed" && task.error && (
            <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-500">
              {task.error.code}: {task.error.message}
            </p>
          )}
          {task.status === "skipped" && (
            <p className="mt-0.5 text-xs text-zinc-400">未执行（预算或时间耗尽）</p>
          )}

          {task.toolCalls.length > 0 && (
            <ul className="mt-1 border-l border-zinc-200 pl-3 dark:border-zinc-800">
              {task.toolCalls.map((call) => (
                <ToolCallRow key={call.callId} call={call} now={now} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </li>
  );
}

export function ActivityPanel({
  state,
  now,
  hideHeading = false,
}: {
  state: ResearchViewState;
  now: number;
  hideHeading?: boolean;
}) {
  const tasks = state.taskIds.map((id) => state.tasks[id]).filter((task) => task !== undefined);

  return (
    <div className="space-y-4">
      {hideHeading ? null : (
        <h2 className="text-xs font-medium tracking-wide text-zinc-500 uppercase">研究过程</h2>
      )}

      <ol className="space-y-1">
        <li className="flex items-baseline gap-2">
          <StatusIcon
            status={state.questionType ? "completed" : state.stage ? "running" : "pending"}
          />
          <div>
            <span className="text-sm font-medium">理解问题</span>
            {state.questionType && (
              <p className="text-xs text-zinc-500">
                识别为 {state.questionType}
                {state.entities.length > 0 &&
                  ` · ${state.entities.map((entity) => entity.symbol).join(", ")}`}
              </p>
            )}
          </div>
        </li>

        <li className="flex items-baseline gap-2">
          <StatusIcon
            status={state.plan ? "completed" : state.stage === "planning" ? "running" : "pending"}
          />
          <div>
            <span className="text-sm font-medium">制定研究计划</span>
            {state.plan && <p className="text-xs text-zinc-500">{tasks.length} 个任务</p>}
          </div>
        </li>
      </ol>

      {tasks.length > 0 && (
        <ol className="space-y-3 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          {tasks.map((task) => (
            <TaskRow key={task.id} task={task} now={now} />
          ))}
        </ol>
      )}

      {state.warnings.length > 0 && (
        <ul className="space-y-1 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          {state.warnings.map((warning, index) => (
            <li
              key={`${warning.code}-${index}`}
              className="text-xs text-amber-600 dark:text-amber-500"
            >
              {warning.message}
            </li>
          ))}
        </ul>
      )}

      {tasks.length > 0 && (
        <p className="sr-only">
          {tasks
            .map((task) => `${AGENT_LABELS[task.agent] ?? task.agent}：${statusLabel(task.status)}`)
            .join("；")}
        </p>
      )}
    </div>
  );
}

import { ResearchConsole } from "@/components/research/research-console";
import type { ModelOption } from "@/components/research/model-selector";
import { getDb } from "@/db/client";
import { getPreferences } from "@/db/queries/settings";
import { listSessions } from "@/db/queries/sessions";
import { fetchAgentModels } from "@/lib/agent-client";
import type { RecentSessionPreview } from "@/lib/research/empty-home";
import { FALLBACK_LIMITS } from "@/lib/settings";
import { notice } from "@/lib/ui";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

/**
 * 模型目录在服务端取，作为 props 传给客户端组件。
 *
 * 避免客户端首屏再发一次 `/api/models`：那会让选择器有一段空白期，
 * 而目录是启动就确定的静态数据，没有理由等到浏览器端才去要。
 */
export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ session?: string }>;
}) {
  const catalog = await fetchAgentModels();
  const models: ModelOption[] = catalog.ok ? catalog.data.models : [];
  const { session: initialSessionId } = await searchParams;
  const db = getDb();
  const prefs = getPreferences(db);
  const defaultModelId = prefs.defaultModelId ?? undefined;
  const costLimitUsd = prefs.limits?.maxSessionCostUsd ?? FALLBACK_LIMITS.maxSessionCostUsd;
  const recent: RecentSessionPreview[] = listSessions(db, { limit: 3 }).map((row) => ({
    id: row.id,
    question: row.question,
    status: row.status,
    questionType: row.questionType,
    createdAt: row.createdAt,
  }));

  return (
    <main id="main" className="mx-auto max-w-6xl px-6 py-6">
      {!catalog.ok && (
        <p className={cn(notice, "mb-6 px-3 py-2 text-sm")}>{catalog.error.message}</p>
      )}

      <ResearchConsole
        models={models}
        initialSessionId={initialSessionId}
        defaultModelId={defaultModelId}
        costLimitUsd={costLimitUsd}
        recent={recent}
      />
    </main>
  );
}

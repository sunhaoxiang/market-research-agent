import { AppHeader } from "@/components/research/app-header";
import { ResearchConsole } from "@/components/research/research-console";
import type { ModelOption } from "@/components/research/model-selector";
import { fetchAgentModels } from "@/lib/agent-client";

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

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <AppHeader current="research" />

      {!catalog.ok && (
        <p className="mb-6 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300">
          {catalog.error.message}
        </p>
      )}

      <ResearchConsole models={models} initialSessionId={initialSessionId} />
    </main>
  );
}

# Development Plan — Crypto & US Stock Research Agent

> 本文档是**设计文档**（相对稳定），描述架构与技术决策。
> 阶段任务拆解与开发状态跟踪在 [`ROADMAP.md`](./ROADMAP.md)。
>
> 原则：**先设计，后编码。** 本文档确认后才开始 Phase 0。

- 文档版本：**v1.1（已确认，2026-09-07）**
- 项目代号：`market-research-agent`
- 最后更新：2026-09-07（据"仅有国内模型 key"的实际情况修订 §9.4 / §9.6 / §9.7 / §17 / §20.1 / §23）

---

## 1. 项目架构

### 1.1 一句话架构

一个 **pnpm monorepo**，包含两个可独立运行的进程：Next.js 全栈应用（UI + BFF + 数据库唯一 writer）和 Python FastAPI Agent 服务（无状态研究引擎），二者通过 **HTTP + SSE** 通信。

### 1.2 分层视图

```text
┌─────────────────────────────────────────────────────────────┐
│  Browser                                                    │
│  React Server/Client Components · Agent Activity Panel      │
│  Report Viewer · Source Panel · Model Selector              │
└───────────────────────────┬─────────────────────────────────┘
                            │ fetch (POST + ReadableStream)
┌───────────────────────────▼─────────────────────────────────┐
│  Next.js (apps/web)            [唯一 DB writer]             │
│  · App Router pages                                         │
│  · Route Handlers = BFF                                     │
│  · Drizzle ORM → SQLite                                     │
│  · Session 生命周期管理 / 事件持久化 / 事件转发              │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP + SSE (内网, 不暴露公网)
┌───────────────────────────▼─────────────────────────────────┐
│  Python Agent Service (services/agent)   [无状态]           │
│  FastAPI                                                    │
│   ├── Orchestrator（确定性 Python 编排）                     │
│   ├── Agents（OpenAI Agents SDK）                           │
│   ├── Tools（Pydantic in/out）                              │
│   ├── Model Registry（多 Provider 抽象）                    │
│   └── Data Providers（+ 缓存 + 限流 + 重试）                 │
└───────────────────────────┬─────────────────────────────────┘
                            │ httpx
┌───────────────────────────▼─────────────────────────────────┐
│  External APIs                                              │
│  LLM: OpenAI / Anthropic / Google / Moonshot / DeepSeek /   │
│       Zhipu                                                 │
│  Data: CoinGecko · DefiLlama · FMP · SEC EDGAR · Tavily     │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 三条核心架构决策

这三条决定了整个项目的形状，值得单独强调：

**决策 A：编排是确定性 Python 代码，不是 LLM 自主循环。**

LLM 只负责两件事：① 生成结构化 `ResearchPlan`；② 在每个子任务内自主调用 tools 并推理。**任务的 fan-out、并发、超时、重试、汇总、失败降级全部由 Python 代码控制**（`asyncio.gather`）。

理由：符合"能用确定性代码解决的问题，不要交给 LLM"。纯 LLM 自主循环（Manager 反复决定下一步）会带来不可预测的延迟、成本和无限循环风险，且执行图无法提前告知前端——而"提前画出执行计划再逐项点亮"正是本项目 UX 的核心（见 §13）。

**决策 B：子 Agent 用 Agents-as-Tools，不用 Handoff。**

Handoff 会**转移控制权且不返回**调用方，无法做"多路并行 + 汇总"。我们需要 Manager 收集所有子 Agent 的结构化输出交给 Fact Checker 和 Report Writer，因此使用 `agent.as_tool()` 模式（子 Agent 作为工具被调用，结果返回编排层）。Handoff 仅在未来做"纯对话式追问"时才可能引入。

**决策 C：Next.js 是数据库的唯一 writer，Python 服务完全无状态。**

避免两个进程同时写同一个 SQLite 文件（写锁竞争、无跨进程事务）。Python 只产生事件流，Next.js Route Handler 一边消费 SSE 落库、一边转发浏览器。客户端断开时，Next.js 继续消费直到 run 结束，保证 session 完整落库。

---

## 2. 技术栈最终选择

### 2.1 Python Agent 服务

| 用途          | 选择                                               | 版本策略                     |
| ------------- | -------------------------------------------------- | ---------------------------- |
| 解释器        | Python **3.13.15**（pyenv 管理）                   | 固定，写入 `.python-version` |
| 依赖/虚拟环境 | **uv**（本机已有 0.9.22）                          | `uv.lock` 提交               |
| Web 框架      | **FastAPI**                                        | latest                       |
| 数据校验      | **Pydantic v2**                                    | latest                       |
| Agent 框架    | **OpenAI Agents SDK**（`openai-agents`）           | 锁定 minor                   |
| 多模型        | `openai-agents[litellm]`                           | 随 SDK                       |
| HTTP 客户端   | **httpx**（AsyncClient，连接池复用）               | latest                       |
| 重试/退避     | **tenacity**                                       | latest                       |
| 日志          | **structlog**（JSON 输出，带 `session_id` 上下文） | latest                       |
| JSON          | **orjson**（FastAPI 自定义 response class）        | latest                       |
| 配置          | **pydantic-settings** + python-dotenv              | latest                       |
| 正文提取      | **trafilatura**                                    | latest                       |
| 测试          | **pytest** + pytest-asyncio + **respx**            | latest                       |
| Lint/Format   | **Ruff**（含 isort、pyupgrade 规则）               | latest                       |
| 类型检查      | **basedpyright**（pyright 系）                     | latest                       |

**不引入 SQLAlchemy**：决策 C 决定了 Python 不访问业务数据库。Provider 缓存用独立的轻量 SQLite 文件（`aiosqlite` 直连，schema 只有一张 KV 表），不需要 ORM。

### 2.2 前端 / BFF

| 用途          | 选择                                                                           |
| ------------- | ------------------------------------------------------------------------------ |
| 运行时        | **Node.js 24.20.0**（`.nvmrc`）                                                |
| 包管理        | **pnpm 12.3.4**（`packageManager` 字段锁定）                                   |
| 框架          | **Next.js**（App Router）+ **React** + **TypeScript** (strict)                 |
| 样式          | **Tailwind CSS**                                                               |
| 组件          | **shadcn/ui**（复制式，非依赖）                                                |
| ORM           | **Drizzle ORM** + drizzle-kit                                                  |
| 数据库        | **SQLite**（`better-sqlite3`，WAL 模式）                                       |
| Markdown 渲染 | react-markdown + remark-gfm + rehype 白名单 sanitize                           |
| 图表          | **Recharts**（Phase 3 起，价格/TVL 走势）                                      |
| 客户端状态    | React hooks + `useSyncExternalStore`（事件流 store），**不引入 Redux/Zustand** |
| 测试          | **Vitest** + Testing Library；Playwright（Phase 6）                            |
| Lint          | ESLint (next/core-web-vitals) + Prettier                                       |

### 2.3 不采用 Turborepo（第一阶段）

只有 1 个 app + 1 个 package + 1 个 Python 服务，pnpm workspace 的 `--filter` 和根 `package.json` scripts 足够。等到 app 数量 ≥ 3 或 CI 构建时间成为痛点再引入。

---

## 3. 为什么选择这些技术

### 3.1 OpenAI Agents SDK

- **完整覆盖第一阶段需求**：Agent、function tool（自动从 Python 类型签名生成 JSON Schema）、Agents-as-Tools、Handoff、Guardrails、Streaming、内置 Tracing。
- **模型抽象是 SDK 的一等公民**：`Model` / `ModelProvider` 协议 + `MultiProvider` + `LitellmModel`，多 Provider 支持不需要自己写 HTTP 层（见 §9）。
- **`Model` 协议可被 stub**：SDK 自带 `agents.testing.ScriptedModel`，可脚本化返回 tool call 序列，从而**零成本、确定性地测试整个 workflow**（见 §18）。这一点对本项目的可测试性至关重要。
- 依赖轻、心智负担小，不引入运行时 DAG/图状态概念。

### 3.2 uv 而非 pip / Poetry / PDM

依赖解析和安装快一个数量级；原生 `pyproject.toml` + lockfile；`uv run` 免手动激活 venv，CI 脚本更短。与 pyenv 分工清晰：**pyenv 管解释器版本，uv 管依赖与虚拟环境**。

### 3.3 Drizzle ORM 而非 Prisma

TypeScript 类型直接从 schema 推导（无代码生成步骤）；SQL 语义透明（本项目有较多带聚合/JSON 字段的查询）；SQLite→PostgreSQL 迁移只需换 driver 与 dialect 导入；migration 以纯 SQL 文件形式提交、可 review。Prisma 的 Rust query engine 二进制与生成步骤在 monorepo 中是额外负担。

### 3.4 SSE 而非 WebSocket

数据流是**单向**（服务端→客户端）；基于 HTTP，无需额外协议栈与鉴权路径；断线重连语义由 `id:` / `Last-Event-ID` 天然支持；可直接被 Next.js Route Handler 用 `ReadableStream` 转发。WebSocket 的双向能力在 Phase 8（human-in-the-loop 打断）才可能需要。

> 注：浏览器原生 `EventSource` 不支持 POST body 与自定义 header，因此前端用 `fetch` + `ReadableStream` 手动解析 SSE 帧（约 60 行代码，见 §12.4）。

### 3.5 Tavily 作为默认搜索 Provider

免费档 1000 credits/月、无需信用卡、返回 LLM-friendly 的清洗后正文（`include_raw_content`），省掉一层抓取+提取。但**接口层抽象为 `SearchProvider`**，可切换 Exa（语义/find-similar）或 Brave（独立索引），避免供应商锁定。

### 3.6 数据源选型

| 领域                  | Provider                     | 免费额度                | 备注                                      |
| --------------------- | ---------------------------- | ----------------------- | ----------------------------------------- |
| Crypto 市场数据       | **CoinGecko** (Demo API Key) | ~30 calls/min, 10k/mo   | 价格/市值/FDV/供应量/ATH/历史             |
| DeFi / TVL / 费用收入 | **DefiLlama**                | 免费无 key              | TVL、DEX volume、fees/revenue、链维度数据 |
| 美股行情+财务+估值    | **FMP** (Basic)              | **250 calls/day**       | 最紧的额度 → 强制缓存                     |
| SEC Filing            | **SEC EDGAR**                | 免费（需 `User-Agent`） | `submissions` + `companyfacts` (XBRL)     |
| Web 搜索/抓取         | **Tavily**                   | 1000 credits/mo         | + 自建 httpx/trafilatura fetch            |
| 链上明细数据          | Phase 3 评估                 | —                       | 见 §25 风险 R4                            |

**免费额度是硬约束**，因此 Provider 层的**缓存 + 限流 + 配额计数**在 Phase 2 就必须实现，属于必需品而非过早优化。

---

## 4. 为什么不选择其他方案

| 方案                                         | 不采用的理由                                                                                                                                                                          | 重新评估的触发条件                    |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| **LangGraph**                                | Agents SDK 已覆盖 routing / tool calling / streaming / 多 Agent；编排的复杂度由决策 A 收敛到普通 Python `asyncio` 代码，不需要图状态机运行时。引入会带来第二套 Agent 抽象与心智负担。 | 见 §4.1 决策清单                      |
| **LangChain**                                | 抽象层厚、版本不稳定，与 Agents SDK 职责重叠。                                                                                                                                        | 无                                    |
| **多 Agent 框架并存**（CrewAI / AutoGen 等） | 明确禁止：一个框架先做深。                                                                                                                                                            | 无                                    |
| **PostgreSQL（第一阶段）**                   | 单用户单机，SQLite 零运维且够快。Schema 已按可迁移原则设计（§10.1）。                                                                                                                 | 需要 pgvector / 多写者 / 远程部署     |
| **Redis**                                    | 无并发压力、无需分布式锁或队列。缓存用本地 SQLite。                                                                                                                                   | 需要跨进程任务队列或分布式限流        |
| **Vector DB / RAG**                          | MVP 无本地文档库；Web 检索结果实时使用即可。                                                                                                                                          | Phase 8 个人知识库                    |
| **MCP**                                      | 第一阶段所有 tool 都在同进程内，MCP 只增加一层进程间协议。                                                                                                                            | 需要复用外部 MCP server 生态          |
| **Celery / 任务队列**                        | 研究任务与 HTTP 请求生命周期绑定（SSE 长连接），无需后台队列。                                                                                                                        | Phase 8 定时研究 / Alerts             |
| **Docker（开发期强制）**                     | 本机 pyenv + pnpm 直接跑更快。提供 compose 文件但不作为开发默认路径。                                                                                                                 | 部署到远程                            |
| **Prisma**                                   | 见 §3.3                                                                                                                                                                               | 无                                    |
| **Vercel AI SDK 的 stream 协议**             | 我们的事件是结构化研究事件（agent/tool/plan 状态），不是 token 流，套用其 data stream 协议反而绕。                                                                                    | 若要做逐 token 打字机效果，可局部引入 |
| **自建 LLM HTTP 适配层**                     | 重复实现 Agents SDK 的 `Model` 协议与 LiteLLM 的 provider 适配，纯负债。                                                                                                              | 无                                    |

### 4.1 LangGraph 决策清单（明确的判断标准）

出现以下**任意两条**时，重新评估引入 LangGraph（并按需求 §3.1 要求补写"为什么需要 / 解决什么问题 / 为什么 Agents SDK 不行 / 架构变化"四段论）：

1. 单次研究耗时 > 5 分钟，需要**任务断点恢复**（进程重启后从中断处继续）。
2. 需要 **human-in-the-loop**：研究中途暂停等用户确认研究方向后继续。
3. 编排状态机分支数 > 10，且 `orchestrator.py` 超过 ~500 行难以维护。
4. 需要 workflow 持久化 + 时间旅行调试。

在此之前，编排层保持为纯 Python：**用类型化的 `ResearchState` dataclass + 显式 step 函数**，即使将来迁移 LangGraph 也是平移而非重写。

---

## 5. Monorepo 结构

**渐进创建**：以下是最终形态，每个 Phase 只创建当前需要的目录。带 `[Pn]` 标记的目录在对应 Phase 才出现。

```text
market-research-agent/
├── apps/
│   └── web/                          # Next.js
│       ├── app/
│       │   ├── (research)/
│       │   │   ├── page.tsx          # 新建研究（首页）
│       │   │   └── s/[sessionId]/page.tsx
│       │   ├── history/page.tsx      # [P6]
│       │   ├── settings/page.tsx
│       │   ├── watchlist/page.tsx    # [P8]
│       │   └── api/
│       │       ├── research/route.ts          # POST 启动研究（SSE）
│       │       ├── research/[id]/route.ts     # GET session 详情
│       │       ├── research/[id]/events/route.ts # [P6] 重连回放
│       │       ├── models/route.ts            # GET 可用模型
│       │       └── settings/route.ts
│       ├── components/
│       │   ├── ui/                   # shadcn/ui
│       │   ├── activity/             # Agent Activity Panel
│       │   ├── report/               # Report + Citation
│       │   └── sources/              # Source Panel
│       ├── lib/
│       │   ├── agent-client.ts       # 调用 Python 服务
│       │   ├── sse.ts                # SSE 解析
│       │   └── events/               # 事件 store / reducer
│       ├── db/
│       │   ├── schema.ts
│       │   ├── client.ts
│       │   ├── queries/
│       │   └── migrations/
│       └── ...
│
├── services/
│   └── agent/
│       ├── src/agent_service/
│       │   ├── main.py               # FastAPI app
│       │   ├── api/
│       │   │   ├── research.py       # SSE endpoint
│       │   │   └── models.py         # 模型目录端点
│       │   ├── orchestrator/
│       │   │   ├── state.py          # ResearchState
│       │   │   ├── planner.py        # LLM → ResearchPlan
│       │   │   ├── plan_validation.py # 计划语义校验（上限/依赖/无环）+ 分层
│       │   │   ├── executor.py       # fan-out 执行
│       │   │   └── pipeline.py       # 全流程串联
│       │   ├── agents/
│       │   │   ├── research_manager.py
│       │   │   ├── crypto_research.py
│       │   │   ├── stock_research.py
│       │   │   ├── web_research.py
│       │   │   ├── fact_checker.py
│       │   │   └── report_writer.py
│       │   ├── tools/
│       │   │   ├── web/
│       │   │   ├── crypto/
│       │   │   ├── defi/
│       │   │   ├── onchain/          # [P3]
│       │   │   ├── stocks/
│       │   │   ├── financials/
│       │   │   ├── sec/
│       │   │   └── system/
│       │   ├── providers/            # 外部 API 客户端
│       │   │   ├── base.py           # 缓存/限流/重试基类
│       │   │   ├── coingecko.py
│       │   │   ├── defillama.py
│       │   │   ├── fmp.py
│       │   │   ├── sec_edgar.py
│       │   │   └── search/
│       │   ├── models/               # LLM 抽象层
│       │   │   ├── registry.py
│       │   │   ├── catalog.py        # 模型+能力声明
│       │   │   └── capabilities.py
│       │   ├── schemas/              # Pydantic：事件/计划/报告/来源
│       │   ├── prompts/              # .md 模板文件
│       │   ├── observability/        # 事件总线 / 计时 / token 计费
│       │   └── config.py
│       ├── tests/
│       ├── pyproject.toml
│       └── README.md
│
├── packages/
│   └── shared/                       # 从 Pydantic 生成的 TS 类型
│       ├── src/generated/            # ← 自动生成，不手改
│       └── src/index.ts
│
├── evals/                            # [P7]
├── scripts/
│   ├── gen-types.sh                  # Pydantic → JSON Schema → TS
│   └── dev.sh                        # 同时起两个服务
├── docs/
│   ├── DEVELOPMENT_PLAN.md
│   ├── ROADMAP.md
│   └── adr/                          # 架构决策记录
├── docker/                           # [P8]
├── .env.example
├── .python-version
├── .nvmrc
├── package.json
├── pnpm-workspace.yaml
└── README.md
```

### 5.1 跨语言类型一致性（重要）

事件协议、ResearchPlan、Report、Source 需要 Python 和 TypeScript 两侧都有类型。**Pydantic 是唯一真源**：

```text
Pydantic models  →  scripts/gen-types.sh  →  JSON Schema  →  json-schema-to-typescript
                                                                    ↓
                                                       packages/shared/src/generated/*.ts
```

CI 中运行该脚本并检查 `git diff --exit-code`，防止两侧类型漂移。此机制在 **Phase 1** 建立。

---

## 6. Agent 架构

### 6.1 Agent vs Tool 的判定标准

> **能用 Tool 解决的问题，不要创建 Agent。**
> 只有需要**独立推理 + 独立上下文窗口 + 独立职责边界**的任务才建 Agent。

对需求 §21 建议的 9 个 Agent 逐一裁决：

| 需求中的 Agent             | 裁决                                | 理由                                                                                                                               |
| -------------------------- | ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Research Manager           | ✅ **独立 Agent**（仅做 planning）  | 需要独立推理：理解问题、分类、拆解任务。只输出结构化 plan，不执行。                                                                |
| Market Agent               | ❌ **降级为 Tools**                 | 取价格/市值/成交量是确定性 API 调用，没有需要推理的部分。归入 `tools/crypto/` 与 `tools/stocks/`，由 Crypto/Stock Agent 调用。     |
| Crypto Research Agent      | ✅ **独立 Agent**                   | 独立领域知识与 tool 集合，需要多轮 tool 调用与推理。                                                                               |
| Stock Research Agent       | ✅ **独立 Agent**                   | 同上，且 tool 集合完全不同（财报/SEC/估值）。                                                                                      |
| On-chain Agent             | ❌ **合并进 Crypto Research Agent** | 链上数据获取是 tool；对 whale/exchange flow 的解读与市场/TVL 数据高度耦合，拆开会造成上下文割裂和重复取数。                        |
| News / Web Research Agent  | ✅ **独立 Agent**                   | 职责与数据源正交（搜索+抓取），且需要处理**不可信输入**——独立 Agent 便于施加隔离与 guardrail。                                     |
| Fundamental Analysis Agent | ❌ **不建**                         | 与 Crypto/Stock Research Agent 的职责 90% 重叠；分析能力体现在 prompt 与工具组合上。**MVP 后如出现"分析深度不足"的实测证据再拆**。 |
| Fact Checker Agent         | ✅ **独立 Agent**                   | 必须有**干净的上下文**：只看 claims + sources，不看产出 claim 时的推理过程，否则失去独立校验意义。                                 |
| Report Writer Agent        | ✅ **独立 Agent**                   | 长文生成，独立上下文与专用 prompt，模型可单独配置（长上下文/写作强）。                                                             |

**结果：6 个 Agent。** 从 9 降到 6，且每一个都有明确的"为什么必须是 Agent"的理由。

### 6.2 各 Agent 契约

所有子 Agent 都用 `output_type` 约束为 Pydantic 结构化输出（不支持 structured output 的模型走降级路径，见 §9.4）。

| Agent              | 输入                                    | 工具                                   | 输出类型          | 默认模型档位        |
| ------------------ | --------------------------------------- | -------------------------------------- | ----------------- | ------------------- |
| `research_manager` | 用户问题 + 当前日期                     | 无（纯 planning）                      | `ResearchPlan`    | reasoning           |
| `crypto_research`  | `ResearchTask`                          | crypto / defi / onchain / web_search   | `ResearchFinding` | balanced            |
| `stock_research`   | `ResearchTask`                          | stocks / financials / sec / web_search | `ResearchFinding` | balanced            |
| `web_research`     | `ResearchTask`                          | web_search / web_fetch / news_search   | `ResearchFinding` | fast                |
| `fact_checker`     | `Claim[]` + `Source[]`                  | web_search / web_fetch（复核用）       | `FactCheckResult` | balanced            |
| `report_writer`    | `ResearchFinding[]` + `FactCheckResult` | 无                                     | `ResearchReport`  | writing（长上下文） |

`ResearchFinding` 是子 Agent 的统一返回结构，这是**报告质量的关键**：

```python
class Claim(BaseModel):
    id: str
    text: str
    epistemic_type: EpistemicType   # 见 §15.3
    confidence: Literal["high", "medium", "low"]
    source_ids: list[str]           # 指向 Source.id
    as_of: datetime | None          # 数据时点，不是抓取时点

class ResearchFinding(BaseModel):
    task_id: str
    agent: str
    summary: str
    claims: list[Claim]
    sources: list[Source]
    metrics: list[MetricPoint]      # 结构化数值，供前端画图
    data_gaps: list[str]            # 明确声明"拿不到什么"
    tool_errors: list[ToolError]
```

`data_gaps` 是**反幻觉的关键设计**：强制 Agent 显式声明缺失数据，而不是用推测填补空白。

### 6.3 Guardrails

| 类型             | 位置                | 作用                                                                                                               |
| ---------------- | ------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Input guardrail  | Research Manager 前 | 拒绝明显越界的请求（非金融研究类、要求直接投资建议的表述转为研究口径）                                             |
| Output guardrail | Report Writer 后    | 校验：① 每个 `fact` 类 claim 至少 1 个 source；② 报告中所有 `[n]` 引用都能解析到 source；③ 无"建议买入/卖出"式表述 |
| 代码层校验       | 引用完整性          | 确定性检查，不用 LLM（见 §15.4）                                                                                   |

---

## 7. Multi-Agent Workflow

### 7.1 主流程

```text
                    ┌──────────────────────────┐
User Question ─────►│ 1. Intent Classify       │  确定性 + 轻量 LLM
                    │    crypto/stock/macro/   │
                    │    compare/generic       │
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 2. Research Manager      │  LLM
                    │    → ResearchPlan        │  含任务列表+依赖+分配
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 3. Plan 校验（代码）      │  任务数上限/agent 合法性/
                    │                          │  无环检测
                    └────────────┬─────────────┘
                                 ▼
        ┌────────────────────────┴───────────────────────┐
        │ 4. Executor：按依赖分层，同层 asyncio.gather    │
        │                                                │
        │   Layer 0 (并行)                               │
        │   ├─ crypto_research  ─► tools ─► APIs         │
        │   ├─ web_research     ─► search/fetch          │
        │   └─ stock_research   ─► tools ─► APIs         │
        │                                                │
        │   Layer 1 (依赖 Layer 0 的任务)                 │
        │   └─ 如"对比 A/B 估值"需先拿到 A、B 数据        │
        └────────────────────────┬───────────────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 5. Merge & Dedup（代码）  │  Source 按 URL 归一去重
                    │                          │  Claim 冲突检测（数值差异）
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 6. Fact Checker          │  LLM，干净上下文
                    │    仅校验 high-impact     │  可再次检索复核
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 7. Gap Check（代码+规则） │  是否需要补充研究？
                    │    最多 1 轮补充          │  硬上限防止无限循环
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 8. Report Writer         │  LLM
                    │    → ResearchReport      │  带 [n] 引用
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │ 9. Output Guardrail      │  引用完整性/口径校验
                    └────────────┬─────────────┘
                                 ▼
                          Final Report
```

### 7.2 执行控制参数（全部可配置，有硬上限）

| 参数                       | 默认 | 上限 | 目的            |
| -------------------------- | ---- | ---- | --------------- |
| `max_tasks_per_plan`       | 6    | 10   | 控成本与延迟    |
| `max_parallel_tasks`       | 4    | 6    | 控外部 API 并发 |
| `max_tool_calls_per_agent` | 12   | 20   | 防工具调用失控  |
| `max_supplement_rounds`    | 1    | 2    | 防无限研究循环  |
| `task_timeout_s`           | 120  | —    | 单任务超时      |
| `total_timeout_s`          | 420  | —    | 整体超时        |

**失败降级原则**：任一子任务失败或超时，**不中断整个流程**。该任务标记为 `failed`，其 `data_gaps` 进入报告的"数据限制"章节。只有 Planner 或 Report Writer 失败才导致整体失败。

### 7.3 ResearchPlan 结构

```python
class ResearchTask(BaseModel):
    id: str
    agent: AgentName                 # 枚举，代码校验
    objective: str                   # 给子 Agent 的具体目标
    entities: list[Entity]           # 已解析的标的：{type, symbol, name, chain?}
    suggested_tools: list[str]        # 建议，非强制
    depends_on: list[str] = []
    priority: int = 0

class ResearchPlan(BaseModel):
    question_type: QuestionType
    interpretation: str              # Agent 对问题的理解（展示给用户）
    entities: list[Entity]
    tasks: list[ResearchTask]
    report_sections: list[str]       # 动态报告结构（见 §16 需求 §26）
    assumptions: list[str]
```

`report_sections` 让报告结构随问题类型变化：单标的深研用完整 10 节；"为什么今天涨"用 Overview/Catalysts/Analysis/Risks 精简结构。

---

## 8. Tool Architecture

### 8.1 统一 Tool 契约

每个 tool 都是纯 async 函数，用 `@function_tool` 暴露给 Agent。**所有返回值都包裹在 `ToolResult[T]` 中**：

```python
class DataProvenance(BaseModel):
    provider: str                  # "coingecko"
    endpoint: str
    source_url: str | None         # 人类可访问的 URL（用于 Citation）
    retrieved_at: datetime         # 抓取时间
    as_of: datetime | None         # 数据本身的时点
    is_cached: bool
    cache_age_s: int | None

class DataQuality(BaseModel):
    completeness: Literal["full", "partial"]
    missing_fields: list[str] = []
    caveats: list[str] = []        # 例如 "FDV 基于最大供应量估算"

class ToolResult[T](BaseModel):
    ok: bool
    data: T | None
    error: ToolError | None
    provenance: DataProvenance | None
    quality: DataQuality | None
```

关键点：

- **provenance 从 tool 层就产生**，不是报告阶段回补。这是 Citation 系统能成立的根本。
- **错误也是结构化返回值，不抛异常给 LLM**。Agent 看到 `ok: false` + 明确 error code，可以决定换 tool 或声明 data gap。抛异常会让 LLM 看到栈信息（噪音 + 潜在信息泄露）。
- 泛型 `T` 是具体的 Pydantic 模型（如 `CryptoMarketData`），不是 `dict`——符合"所有外部数据都应该结构化"。

### 8.2 错误分类

```python
class ToolErrorCode(StrEnum):
    NOT_FOUND         # 标的不存在
    RATE_LIMITED      # 触发限流
    QUOTA_EXHAUSTED   # 免费额度耗尽（当日/当月）
    TIMEOUT
    UPSTREAM_ERROR    # 5xx
    INVALID_INPUT     # 参数校验失败
    UNSUPPORTED       # 该标的/链不支持此指标
    BLOCKED           # SSRF / 域名黑名单
```

`UNSUPPORTED` 和 `QUOTA_EXHAUSTED` 必须与 `NOT_FOUND` 区分：前者应产生 data gap，后者可能意味着标的名解析错误需要重试。

### 8.3 Provider 基础设施（`providers/base.py`）

所有外部 API 客户端继承同一基类，横切能力集中实现：

```text
BaseProvider
├── httpx.AsyncClient（共享连接池，超时配置）
├── Cache            SQLite KV，按 (provider, endpoint, params) 哈希，TTL 分级
├── RateLimiter      asyncio 令牌桶，按 provider 配置
├── QuotaTracker     日/月配额计数，耗尽时快速失败而非等待
├── Retry            tenacity：指数退避 + 抖动，仅重试 429/5xx/网络错误
└── Instrumentation  自动发出 tool/provider 事件与耗时
```

**缓存 TTL 分级**（免费额度约束下的核心设计）：

| 数据类型                | TTL                                | 理由                   |
| ----------------------- | ---------------------------------- | ---------------------- |
| 实时价格/quote          | 60s                                | 研究场景不需要秒级     |
| 市场数据（市值/供应量） | 5 min                              |                        |
| 历史价格序列            | 6 h（当日）/ 30 d（历史区间）      | 历史数据不变           |
| TVL / DeFi 指标         | 30 min                             | DefiLlama 本身按日更新 |
| 公司 profile            | 7 d                                |                        |
| 财务报表 / SEC filing   | **永久**（按 accession number 键） | 已发布的财报不会变     |
| Web 搜索结果            | 30 min                             |                        |
| Web 页面正文            | 24 h                               |                        |

### 8.4 Tool 清单

按需求 §23 组织。`[Pn]` 标注实现阶段。

```text
tools/web/
  web_search(query, max_results, time_range, include_domains)     [P2]
  web_fetch(url)                          # SSRF 防护 + 正文提取   [P2]
  news_search(query, symbols, since)                              [P2]

tools/crypto/
  resolve_asset(query)                    # "HYPE" → coin id/合约  [P3]
  get_crypto_price(asset)                                         [P3]
  get_market_data(asset)                  # 市值/FDV/供应/ATH/ATL  [P3]
  get_price_history(asset, days, interval)                        [P3]
  get_tokenomics(asset)                   # 供应/分配/解锁          [P3]

tools/defi/
  get_tvl(protocol|chain, days)                                   [P3]
  get_protocol_fees_revenue(protocol, days)                       [P3]
  get_dex_volume(protocol|chain, days)                            [P3]
  get_chain_overview(chain)               # 生态聚合               [P3]

tools/onchain/                            # 覆盖度取决于源，见 R4
  get_chain_activity(chain, days)         # 活跃地址/交易数         [P3]
  get_token_holders(asset)                                        [P3+]
  get_whale_activity(asset, threshold)                            [P3+]
  get_exchange_flow(asset, days)          # 净流入/流出             [P3+]

tools/stocks/
  resolve_ticker(query)                                           [P4]
  get_stock_quote(ticker)                                         [P4]
  get_company_profile(ticker)                                     [P4]
  get_price_history(ticker, range)                                [P4]
  get_peers(ticker)                                               [P4]
  compare_to_index(ticker, index, range)                          [P4]

tools/financials/
  get_income_statement(ticker, period, limit)                     [P4]
  get_balance_sheet(ticker, period, limit)                        [P4]
  get_cash_flow(ticker, period, limit)                            [P4]
  get_valuation_metrics(ticker)            # PE/PS/PB/EV·EBITDA…   [P4]
  get_valuation_history(ticker, years)     # 历史估值分位           [P4]
  get_growth_metrics(ticker)               # YoY/QoQ/CAGR（代码算） [P4]

tools/sec/
  list_sec_filings(ticker, form_types, limit)                     [P4]
  get_filing_section(accession, section)   # 10-K Item 7 / 1A 等   [P4]
  get_xbrl_facts(ticker, concepts)                                [P4]
  get_earnings_summary(ticker)                                    [P4]

tools/system/
  compute_metrics(series, ops)             # 增长率/CAGR/波动率
  compare_values(values)                   # 交叉验证数值一致性
  get_current_datetime()                   # 消除"今天是哪天"幻觉
```

### 8.5 确定性计算不交给 LLM

`compute_metrics` 存在的意义：**YoY、QoQ、CAGR、波动率、百分位、涨跌幅一律由 Python 计算**，LLM 只负责解读。LLM 做算术是幻觉高发区，而这些数字会直接进入报告。

---

## 9. Model Provider Architecture

### 9.1 分层

```text
Agent（业务代码，只知道 "role"，不知道具体模型）
   ↓
ModelRegistry.resolve(model_id | role) → ResolvedModel
   ↓
ResolvedModel { model: agents.Model, settings: ModelSettings, caps: ModelCapabilities }
   ↓
├── OpenAIResponsesModel        （OpenAI，支持 reasoning/Responses API）
├── OpenAIChatCompletionsModel  （OpenAI 兼容端点：Moonshot / DeepSeek / Zhipu）
└── LitellmModel                （Anthropic / Google，及长尾 provider）
```

**业务 Agent 代码不出现任何 provider 名或模型名**，只声明角色：

```python
crypto_agent = Agent(
    name="crypto_research",
    instructions=load_prompt("crypto_research"),
    tools=CRYPTO_TOOLS,
    output_type=ResearchFinding,
    model=registry.for_role(ModelRole.BALANCED),   # ← 唯一入口
)
```

### 9.2 为什么不直接用 `MultiProvider` + `litellm/` 前缀

`MultiProvider`/`LitellmProvider` 是很好的底层，但**只从环境变量读取 key 与 base_url**，且不提供能力元数据。我们额外需要：

1. **API key / base_url 由 `pydantic-settings` 统一管理**（支持 DB 中的用户设置覆盖）。
2. **能力元数据**（tool calling / structured output / streaming / reasoning / context window / vision / 定价），用于：前端下拉列表、降级决策、成本计算。
3. **角色到模型的映射**，使"换模型"是配置变更而非代码变更。

因此：`ModelRegistry` 负责 1-3，**底层 `Model` 实现全部复用 SDK**（`OpenAIChatCompletionsModel` / `LitellmModel`），绝不自己写 LLM HTTP 调用。

### 9.3 模型目录（`models/catalog.py`）

声明式配置，新增模型只改这一个文件：

```python
class ModelCapabilities(BaseModel):
    tool_calling: bool
    parallel_tool_calls: bool
    structured_output: Literal["native_schema", "json_mode", "prompt_only"]
    streaming: bool
    reasoning: bool
    vision: bool
    context_window: int
    max_output_tokens: int
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None

class ModelEntry(BaseModel):
    id: str                    # "anthropic:claude-sonnet"（我们的稳定 ID）
    provider: ProviderId       # openai|anthropic|google|moonshot|deepseek|zhipu
    upstream_model: str        # 传给 SDK 的真实模型名
    adapter: AdapterKind       # openai_responses | openai_chat | litellm
    display_name: str
    capabilities: ModelCapabilities
    default_settings: ModelSettings
```

`GET /api/models` 返回该目录（过滤掉无 API key 的 provider），前端渲染 Provider→Model 两级选择器。**没配 key 的 provider 在 UI 中显示为禁用并提示原因**，而不是调用后才报错。

### 9.4 能力差异的降级策略

这是多模型支持真正的难点。策略必须显式：

> **实测前提（2026-09 核实）**：OpenAI 全系与 Kimi K3 支持 `json_schema` + `strict: true`
> （原生 schema 约束），所以 **OpenAI 优先的默认配置下 `native_schema` 是主路径**。
> 但 DeepSeek V4 与 GLM-5.x 仅支持 `response_format={"type":"json_object"}`（保证合法 JSON、
> 不约束字段，实测有效率 96–98%），因此 `json_mode` 降级路径仍需完整实现并单测覆盖——
> 它是"用户在 UI 里选了 DeepSeek"时的正常路径，不是异常分支。
>
> 附带约束：**Pydantic schema 刻意保持扁平**（避免 `$ref` / `oneOf` / 深嵌套）。
> 这既是为了兼容弱 schema 模型，也因为 OpenAI 的 strict 模式本身对 schema 有子集限制。

| 能力缺口                    | 降级方案                                                                                                                        |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 无 native structured output | `json_mode` → 或 `prompt_only`：prompt 内嵌 schema + 代码侧 `model_validate_json`，失败最多重试 2 次（第 2 次附带解析错误信息） |
| 无并行 tool calling         | `ModelSettings(parallel_tool_calls=False)`，串行调用（更慢但正确）                                                              |
| 无 tool calling             | **拒绝选择**：UI 直接不列出，API 层返回 422                                                                                     |
| 上下文窗口小                | Report Writer 阶段对 findings 做预算裁剪（按 claim 重要度排序截断），并在报告中声明                                             |
| 无 reasoning                | 正常运行；planner 使用 CoT prompt 补偿                                                                                          |

### 9.5 角色与模型选择

| 角色       | 用途                                | 选型倾向                | 默认（见 §9.7）        |
| ---------- | ----------------------------------- | ----------------------- | ---------------------- |
| `PLANNER`  | Research Manager                    | 推理强                  | `openai:gpt-5.6-sol`   |
| `BALANCED` | Crypto/Stock Research、Fact Checker | 工具调用可靠、性价比    | `openai:gpt-5.6-terra` |
| `FAST`     | 意图分类、Web Research              | 便宜快                  | `openai:gpt-5.6-luna`  |
| `WRITING`  | Report Writer                       | 长上下文 + 中文写作质量 | `openai:gpt-5.6-sol`   |

**用户在前端选择的模型作为全局默认**，覆盖所有角色；高级设置里可以按角色单独指定（Phase 6）。所有角色使用同一模型也必须能正常工作——这是 MVP 的默认行为。

### 9.6 SDK 初始化与 tracing

**开发期模型走 DeepSeek / 智谱，但仍配置一个最低额度的 OpenAI key 专用于 tracing。**

这不是矛盾——SDK 的 tracing 导出与业务模型调用是两条独立通道。官方文档明确支持
「用非 OpenAI 模型时，向 tracing exporter 提供一个 OpenAI API key 即可启用**免费** tracing」：
trace 上传本身不计费，只需要一个有效 key 做鉴权。因此 $5 的最低充值就能保住整个
LLM 层调试面（每次 LLM 调用的输入、输出、工具选择、耗时都可在 Traces 面板回看），
而这 $5 会完整留作日后切 OpenAI 模型时的额度，不会浪费。

```python
# services/agent/src/agent_service/models/bootstrap.py
from agents import set_tracing_disabled, set_tracing_export_api_key

def bootstrap_sdk(settings: Settings) -> None:
    if settings.openai_agents_disable_tracing:
        set_tracing_disabled(True)
    elif key := settings.providers.openai_api_key:
        # tracing 上传独立设置，与业务模型调用解耦：
        # 即使某次运行用的是 Kimi/DeepSeek，trace 依然能上传到 OpenAI
        set_tracing_export_api_key(key.get_secret_value())
    else:
        set_tracing_disabled(True)  # 无 key 时必须关闭，否则每次运行报错
```

要点：

1. **不调用 `set_default_openai_client()` / `set_default_openai_api()` 这类全局设置。** 我们有多个不同 base_url 的 provider，全局单客户端反而是限制。每个 provider 各自持有一个 `AsyncOpenAI(base_url=..., api_key=...)` 实例，由 `ModelRegistry` 缓存复用；OpenAI 走 `OpenAIResponsesModel`，国内兼容端点走 `OpenAIChatCompletionsModel(model=..., openai_client=...)`。这样**每个 Agent 的模型都是显式实例，不依赖任何全局状态**——也让测试更容易。
2. `AsyncOpenAI` 构造时 `api_key` 不可为 `None`（会抛 `OpenAIError`）。provider 无 key 时应在 registry 层拒绝解析并给出明确错误，而不是构造客户端后才失败。
3. **`set_tracing_export_api_key()` 是关键**：它让 trace 导出用 OpenAI key，而业务调用继续用 DeepSeek/智谱的 key。若改用全局 `OPENAI_API_KEY` 环境变量，SDK 可能把它同时当成默认模型的 key，反而引起混淆。
4. **隐私取舍**：tracing 会把 prompt 与响应上传到 OpenAI。若不希望上传研究内容，用 `RunConfig(trace_include_sensitive_data=False)` 只上传结构与耗时，或直接 `OPENAI_AGENTS_DISABLE_TRACING=true`。默认开启（个人研究场景，换取调试便利）。
5. 自建埋点（`research_events` / `tool_calls` / `agent_runs`）**仍然要做且仍在 Phase 1**：SDK tracing 只覆盖 LLM 交互细节，而成本核算、缓存命中率、provider 配额、eval 对比、`/debug` 页面都依赖我们自己的结构化指标。国内模型的分时计价与缓存命中率更是只能自己算——这是 §9.8 要求记录 `prompt_cache_hit_tokens` 的原因。
6. **若不充 OpenAI key**：`OPENAI_AGENTS_DISABLE_TRACING=true`，此时第 5 条的自建埋点成为**唯一**调试面，P1-13 的优先级需进一步提前。

### 9.7 首批模型清单（Phase 1 落地目标）

**开发期用国内模型，上线后切 OpenAI。** 两套配置都写进 catalog，切换只改 `.env.local` 的 `MODEL_ROLE_*`，不改代码——这正是 §9.1 模型抽象层要解决的问题。

**开发期**（DeepSeek 主力 + 智谱作第二 provider），单位 ¥/1M tokens：

| 我们的 ID                    | adapter       | 输入      | 输出        | 缓存命中      | 角色                                           |
| ---------------------------- | ------------- | --------- | ----------- | ------------- | ---------------------------------------------- |
| `deepseek:deepseek-v4-pro`   | `openai_chat` | ¥4.5 / ¥9 | ¥13.5 / ¥27 | ¥0.15 / ¥0.30 | **`PLANNER`** · **`BALANCED`** · **`WRITING`** |
| `deepseek:deepseek-v4-flash` | `openai_chat` | ¥1.5 / ¥3 | ¥4.5 / ¥9   | ¥0.05 / ¥0.10 | **`FAST`**                                     |
| `zhipu:glm-5.3-flash`        | `openai_chat` | ¥0.8      | ¥2.8        | ¥0.23         | 第二 provider（验证抽象层）· `WRITING` 备选    |
| `zhipu:glm-5.3`              | `openai_chat` | ¥8        | ¥28         | ¥2            | 可选升档                                       |
| `moonshot:kimi-k3`           | `openai_chat` | ¥20       | ¥100        | ¥2            | 仅在需要 strict json_schema 兜底时启用（贵）   |

> DeepSeek 为**分时计价**，上表两个数字分别是「闲时 / 高峰」。高峰为北京时间
> **09:00–12:00 与 14:00–18:00（工作日）**，其余时段（含周末全天）为闲时，价格减半。
> 这恰好覆盖工作时间，因此 Phase 5 的批量 eval 应安排在夜间跑，成本直接对折。

**上线期**（OpenAI），单位 $/1M tokens，全系支持 `native_schema`：

| 我们的 ID              | adapter            | 输入  | 输出  | 角色                     |
| ---------------------- | ------------------ | ----- | ----- | ------------------------ |
| `openai:gpt-5.6-terra` | `openai_responses` | $2    | $12   | `BALANCED`               |
| `openai:gpt-5.6-sol`   | `openai_responses` | $4    | $20   | `PLANNER` · `WRITING`    |
| `openai:gpt-5.6-luna`  | `openai_responses` | $0.20 | $1.20 | `FAST`                   |
| `openai:gpt-6-astra`   | `openai_responses` | $10   | $50   | 可选升档（1.05M 上下文） |

Anthropic / Google 条目**写入 catalog 但标记 `available=false`**，拿到 key 即可用。

**单次研究成本估算**（约 9.3 万输入 + 2 万输出 token）：

| 配置                             | 单次成本         | 相对 |
| -------------------------------- | ---------------- | ---- |
| DeepSeek Pro 闲时                | ≈ ¥0.69（$0.10） | 基准 |
| DeepSeek Pro 闲时 + 60% 缓存命中 | ≈ ¥0.45（$0.06） | 0.7× |
| DeepSeek Flash 闲时              | ≈ ¥0.23（$0.03） | 0.3× |
| OpenAI terra/sol（上线配置）     | ≈ $0.50          | 5×   |

`MAX_SESSION_COST_USD=1.0` 对两套配置都是安全护栏。

### 9.8 Prompt 缓存的设计约束（影响成本一个数量级）

DeepSeek Pro 的缓存命中价 ¥0.15 只有未命中 ¥4.5 的 **3%**，比闲时折扣（50%）重要得多；OpenAI 与 Kimi 同为 1/10 量级。各家的自动前缀缓存都要求**请求前缀逐字节一致**，由此得出两条硬性编码规则：

1. **system prompt 必须字节级稳定。** 严禁把当前时间、session_id、请求序号等易变内容插入 system prompt——那会让每次请求都缓存未命中，成本相差一个数量级。
2. **易变内容一律放在消息序列末尾。** 研究场景确实需要"当前日期"（用于判断数据时效与 `as_of`），它必须作为 user message 的一部分传入，而不是拼进 system prompt。这一点很容易在写 prompt 时无意破坏，需在 code review 时专门检查。

缓存生效有最小长度门槛（Kimi 为 256 token，其余各家类似），我们的 Agent system prompt 均远超此值。`tool_calls` / `agent_runs` 必须记录 `prompt_cache_hit_tokens` 与 `prompt_cache_miss_tokens`（DeepSeek 在 `usage` 中返回），否则无法验证缓存是否真的生效。

---

## 10. 数据库 Schema

Drizzle + SQLite，schema 在 `apps/web/db/schema.ts`。

### 10.1 可迁移性约束（PostgreSQL-ready）

| 约束       | 做法                                                                   |
| ---------- | ---------------------------------------------------------------------- |
| 主键       | `text` 存 UUIDv7（时间有序，兼具排序性与全局唯一）                     |
| 时间       | `integer` 存 **Unix 毫秒**（避免 SQLite 无 timestamp 类型 / 时区歧义） |
| 布尔       | `integer` + Drizzle `{ mode: 'boolean' }`                              |
| JSON       | `text` + Drizzle `{ mode: 'json' }` + TS 泛型（PG 迁移时改 `jsonb`）   |
| 枚举       | `text` + TS union 类型 + CHECK 约束（不用 SQLite 特有技巧）            |
| 禁用       | `AUTOINCREMENT`、`rowid` 依赖、无类型列、`INSERT OR REPLACE`           |
| 未来多用户 | 所有业务表预留 `user_id text`（MVP 恒为 `'local'`，已建索引）          |

### 10.2 表设计

```text
research_sessions
  id                text PK
  user_id           text        DEFAULT 'local'
  question          text
  question_type     text        -- crypto|stock|macro|compare|generic
  status            text        -- pending|planning|researching|checking|writing|completed|failed|cancelled
  model_id          text        -- 用户选择的模型
  model_snapshot    json        -- 当次运行的完整模型配置（可复现）
  plan              json        -- ResearchPlan
  error             json
  duration_ms       integer
  token_usage       json        -- {input, output, cached, by_agent}
  cost_usd          real
  created_at        integer
  started_at        integer
  completed_at      integer
  INDEX (user_id, created_at DESC)

research_events                 -- append-only 事件日志
  id                text PK
  session_id        text FK → research_sessions
  seq               integer     -- 会话内单调递增，用于重连
  type              text        -- 见 §12.2
  agent             text
  tool              text
  task_id           text
  payload           json
  created_at        integer
  UNIQUE (session_id, seq)
  INDEX (session_id, seq)

research_reports
  id                text PK
  session_id        text FK UNIQUE
  title             text
  executive_summary text
  sections          json        -- [{ id, title, markdown, claim_ids }]
  markdown          text        -- 渲染后完整报告（含 [n] 引用）
  metadata          json        -- {report_sections, generated_by_model, data_gaps}
  created_at        integer

sources
  id                text PK
  session_id        text FK
  url               text
  url_canonical     text        -- 归一化后用于去重
  title             text
  domain            text
  source_type       text        -- web|news|official|sec|api|docs|github|social
  provider          text        -- coingecko|defillama|fmp|sec|tavily|…
  reliability       text        -- primary|secondary|aggregator|unknown  见 §15.2
  published_at      integer
  retrieved_at      integer
  excerpt           text        -- 支撑证据片段（用于 UI 悬浮预览）
  UNIQUE (session_id, url_canonical)
  INDEX (session_id)

claims
  id                text PK
  session_id        text FK
  task_id           text
  agent             text
  text              text
  epistemic_type    text        -- fact|source_backed_fact|analysis|inference|prediction|opinion
  confidence        text        -- high|medium|low
  as_of             integer
  verification      text        -- unverified|verified|conflicting|unsupported|refuted
  verification_note text
  citation_index    integer     -- 报告中的 [n]
  INDEX (session_id)

claim_sources                   -- 多对多
  claim_id          text FK
  source_id         text FK
  PRIMARY KEY (claim_id, source_id)

tool_calls                      -- 可观察性（§20）
  id                text PK
  session_id        text FK
  task_id           text
  agent             text
  tool              text
  provider          text
  input             json
  output_summary    json        -- 摘要，不存全量 payload
  ok                integer     {mode:boolean}
  error_code        text
  cache_hit         integer     {mode:boolean}
  duration_ms       integer
  created_at        integer
  INDEX (session_id), INDEX (tool, ok)

agent_runs                      -- 可观察性
  id                text PK
  session_id        text FK
  task_id           text
  agent             text
  model_id          text
  status            text
  tokens_in / tokens_out / tokens_cached  integer
  cost_usd          real
  duration_ms       integer
  error             json
  created_at        integer
  INDEX (session_id)

settings                        -- KV，单用户
  key               text PK
  user_id           text DEFAULT 'local'
  value             json
  updated_at        integer

watchlist                       -- [P8]
  id                text PK
  user_id           text DEFAULT 'local'
  asset_type        text        -- crypto|stock
  symbol            text
  display_name      text
  notes             text
  created_at        integer
  UNIQUE (user_id, asset_type, symbol)
```

### 10.3 写入策略

- `research_events` 是**唯一 append-only 表**，其余表可视为它的物化投影 → 任何时候都能从事件重放出会话状态（调试利器）。
- 事件写入**批量化**：每 200ms 或积累 20 条 flush 一次，避免逐条 SQLite 事务。
- `sources` / `claims` / `research_reports` 在 run 结束时**单事务写入**，保证报告与引用的一致性。
- SQLite 配置：`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=5000`、`foreign_keys=ON`。

---

## 11. 前后端通信方式

### 11.1 职责边界

|                      | Next.js                                  | Python Agent Service |
| -------------------- | ---------------------------------------- | -------------------- |
| UI 渲染              | ✅                                       | ❌                   |
| 数据库读写           | ✅ 唯一 writer                           | ❌ 不接触业务库      |
| Session 生命周期     | ✅                                       | ❌                   |
| 事件持久化           | ✅                                       | ❌（只产生）         |
| LLM 调用             | ❌                                       | ✅                   |
| Tool 执行 / 外部 API | ❌                                       | ✅                   |
| API Key 持有         | 仅 DB 中用户设置的 key，**不下发浏览器** | ✅ 从环境变量读取    |

### 11.2 一次研究的完整时序

```text
Browser                Next.js Route Handler          Python /research/stream
   │                            │                              │
   │ POST /api/research         │                              │
   │ {question, modelId}   ────►│                              │
   │                            │ 1. INSERT research_sessions  │
   │                            │    (status=pending)          │
   │                            │ 2. POST + SSE ──────────────►│
   │                            │                              │ 3. run pipeline
   │                            │◄──── event: plan_created ────│
   │                            │ 4. 落库(批量) + 转发          │
   │◄─── SSE frame ─────────────│                              │
   │                            │◄──── event: agent_started ───│
   │◄─── SSE frame ─────────────│                              │
   │         ⋮                  │            ⋮                 │
   │                            │◄──── event: report_completed │
   │                            │ 5. 单事务写 report/sources/  │
   │                            │    claims；更新 session       │
   │◄─── SSE done ──────────────│                              │
```

**客户端断开时**：Next.js 侧的消费循环不绑定 request 的 `AbortSignal`，继续读到 upstream 结束并完成落库。用户刷新页面后从 DB 读取完整结果。

**取消研究**：`DELETE /api/research/{id}` → Next.js 调用 Python `POST /research/{run_id}/cancel` → Python 取消 asyncio task → session 标记 `cancelled`（Phase 6）。

### 11.3 内部服务安全

Python 服务只监听 `127.0.0.1:8000`，不暴露公网。请求头带 `X-Internal-Token`（`.env` 中的共享随机串）做基本校验，防止本机其他进程误调。

---

## 12. Streaming / Event Protocol

### 12.1 设计原则

1. **Discriminated union**，`type` 为判别字段，Pydantic 定义 → 生成 TS 类型。
2. **每个事件自带 `seq`**（会话内单调递增），支持断线重连与顺序保证。
3. **事件是"状态变更通知"而非"日志字符串"**：payload 结构化，前端据此维护状态树，而不是 append 文本。
4. **事件粒度对齐 UX 需求**（§13）：能画出"计划树 + 逐节点点亮"。
5. Agents SDK 的 `RunItemStreamEvent`（`tool_called` / `tool_output` / `message_output_created` / `reasoning_item_created` / `handoff_*`）在编排层**翻译**为本协议事件，不直接透传——避免前端耦合 SDK 内部结构。

### 12.2 事件类型

```python
class EventType(StrEnum):
    # 会话级
    SESSION_STARTED
    SESSION_COMPLETED
    SESSION_FAILED
    SESSION_CANCELLED
    # 规划
    INTENT_CLASSIFIED       # {question_type, entities}
    PLAN_CREATED            # {plan}  ← 前端据此一次性画出完整任务树
    PLAN_UPDATED            # {added_tasks}（补充研究轮）
    # 阶段
    STAGE_CHANGED           # {stage: planning|researching|checking|writing}
    # Agent
    AGENT_STARTED           # {agent, task_id, objective, model_id}
    AGENT_PROGRESS          # {agent, task_id, message}  人类可读的当前动作
    AGENT_REASONING         # {agent, summary}  仅 reasoning 模型的摘要
    AGENT_COMPLETED         # {agent, task_id, summary, claim_count, source_count, duration_ms}
    AGENT_FAILED            # {agent, task_id, error}
    AGENT_HANDOFF           # {from_agent, to_agent, reason}
    # Tool
    TOOL_STARTED            # {tool, agent, task_id, call_id, input_summary}
    TOOL_COMPLETED          # {call_id, ok, provider, cache_hit, duration_ms, result_summary}
    TOOL_FAILED             # {call_id, error_code, message}
    # 数据/来源
    SOURCE_FOUND            # {source}  ← Source Panel 实时增长
    METRIC_FOUND            # {metric}  ← 图表实时增长
    # 事实核查
    FACT_CHECK_STARTED      # {claim_count}
    FACT_CHECK_PROGRESS     # {checked, total}
    CLAIM_VERIFIED          # {claim_id, verification, note}
    CONFLICT_DETECTED       # {claim_ids, description, values}
    # 报告
    REPORT_STARTED
    REPORT_SECTION_DELTA    # {section_id, text}  可选：逐段流式
    REPORT_COMPLETED        # {report}
    # 其他
    USAGE_UPDATED           # {tokens, cost_usd}
    WARNING                 # {code, message}  额度不足/数据缺失等
    HEARTBEAT               # 保活，防代理超时
```

### 12.3 信封格式

```python
class ResearchEvent(BaseModel):
    seq: int
    session_id: str
    type: EventType
    ts: datetime
    message: str | None       # 面向用户的一句话（前端可直接显示）
    payload: EventPayload     # 判别联合
```

SSE 帧：

```text
id: 42
event: research
data: {"seq":42,"session_id":"…","type":"tool_started","ts":"…","message":"Fetching HYPE TVL from DefiLlama","payload":{…}}

```

`message` 字段是有意的冗余设计：前端不需要为每种事件类型都写文案逻辑，未覆盖的事件类型也能优雅显示。

### 12.4 前端消费

`lib/sse.ts` 提供 `streamResearch(req): AsyncIterable<ResearchEvent>`：`fetch` + `ReadableStream` + `TextDecoderStream` + 手写行解析器（处理 `\n\n` 分帧、`id:`/`event:`/`data:` 字段、多行 data）。

事件经 reducer 归约为 `ResearchViewState`：

```ts
type ResearchViewState = {
  stage: Stage;
  plan: ResearchPlan | null;
  tasks: Record<string, TaskNode>; // status/agent/objective/toolCalls[]/timing
  sources: Source[];
  metrics: MetricPoint[];
  claims: Record<string, ClaimState>;
  conflicts: Conflict[];
  report: ResearchReport | null;
  usage: Usage;
  warnings: Warning[];
};
```

`useSyncExternalStore` 订阅，避免高频事件导致的过度 re-render。

---

## 13. Agent Activity UI 设计

### 13.1 页面布局

```text
┌────────────────────────────────────────────────────────────────────┐
│  Research: 帮我做一份 HYPE 的投资研究报告      [Model: Claude ▾]    │
│  ● Researching · 00:42 · 1.2k in / 3.4k out · $0.03                │
├──────────────────────────┬─────────────────────────────────────────┤
│  ACTIVITY (左, 可折叠)   │  REPORT (右, 主区)                      │
│                          │                                         │
│  ✓ 理解问题              │  ┌───────────────────────────────────┐  │
│    识别为: Crypto 单标的  │  │ 研究计划                          │  │
│    实体: HYPE (Hyperliquid)│ │ 我将从 4 个方向研究 HYPE:         │  │
│                          │  │ 1. 市场表现与估值 …               │  │
│  ✓ 制定研究计划 (4 任务)  │  └───────────────────────────────────┘  │
│                          │                                         │
│  ● Crypto Research       │  Executive Summary                      │
│    │ 分析 TVL 与协议收入  │  （流式生成中…）                        │
│    ├ ✓ get_market_data   │                                         │
│    │   CoinGecko · 340ms │  1. Overview                            │
│    ├ ✓ get_tvl           │  HYPE 是 Hyperliquid 的原生代币…[1]     │
│    │   DefiLlama · 1.2s  │                                         │
│    └ ● get_protocol_fees │  2. Market Performance                  │
│                          │  过去 30 天 TVL 增长约 18%。[2]         │
│  ● Web Research          │  ┌────── TVL 30d ──────┐                │
│    ├ ✓ web_search (5)    │  │      ╱╲    ╱        │                │
│    └ ● web_fetch         │  │  ╱╲╱   ╲╱           │                │
│        hyperliquid.xyz   │  └─────────────────────┘                │
│                          │                                         │
│  ○ Fact Checker  待执行   │                                         │
│  ○ Report Writer 待执行   │                                         │
│                          │                                         │
│  ─────────────────────── │                                         │
│  SOURCES (12)            │                                         │
│  [1] DefiLlama           │                                         │
│  [2] Hyperliquid Docs    │                                         │
│  …                       │                                         │
└──────────────────────────┴─────────────────────────────────────────┘
```

### 13.2 核心交互

| 交互         | 行为                                                                                                                |
| ------------ | ------------------------------------------------------------------------------------------------------------------- |
| **计划先行** | `PLAN_CREATED` 一到，立刻画出完整任务树（全部 `○ pending`），用户马上知道"要做什么、要多久"——这是消除等待焦虑的关键 |
| 节点状态     | `○` pending · `●` running（脉冲动画）· `✓` completed · `⚠` failed · `⊘` skipped                                     |
| Tool 节点    | 显示 provider、耗时、`⚡`（缓存命中）；点击展开完整 input/output JSON                                               |
| 实时耗时     | running 节点显示 `elapsed`，completed 显示最终耗时                                                                  |
| 引用交互     | 点击报告中 `[1]` → 右侧 Source Panel 高亮滚动 + 悬浮卡显示 excerpt/domain/retrieved_at                              |
| 认知标记     | claim 按 epistemic_type 显示徽标：`事实`（无标记）·`分析`（蓝）·`推测`（黄）·`预测`（橙）·`观点`（灰）              |
| 冲突提示     | `CONFLICT_DETECTED` → 报告内联黄色警示条，列出冲突数值与各自来源                                                    |
| 数据缺口     | 报告固定末节"数据限制"，列出全部 `data_gaps`                                                                        |
| 折叠策略     | 完成的任务自动折叠为一行；失败/冲突的保持展开                                                                       |
| 无障碍       | 状态变更用 `aria-live="polite"` 播报，不只靠颜色区分状态                                                            |

### 13.3 不做的事

- 不做逐 token 打字机效果覆盖整个 activity（噪音大）；仅 Report 正文可选流式。
- 不显示原始 LLM 推理全文，只显示 reasoning summary（如模型提供）。
- 不在前端做任何数据计算或格式推断——全部由后端结构化字段驱动。

---

## 14. Research Session 设计

### 14.1 状态机

```text
pending ──► planning ──► researching ──► checking ──► writing ──► completed
   │           │              │              │            │
   └───────────┴──────────────┴──────────────┴────────────┴──► failed
                                                          └──► cancelled
```

状态迁移只在 Next.js 侧根据 `STAGE_CHANGED` / 终态事件写入，Python 不持有状态。

### 14.2 可复现性

`model_snapshot` 存下当次运行的完整模型配置（model_id、upstream_model、temperature、max_tokens、capabilities 快照），加上 append-only 的 `research_events`，任何一次历史研究都能被完整重建和 diff——这是评估"哪个模型效果更好"的数据基础。

### 14.3 History 页面

列表展示：问题、类型、模型、状态、耗时、来源数、成本、时间。支持按类型/模型/状态过滤（Phase 6）。点击进入的详情页与实时页面**复用同一套组件**，只是事件源从 SSE 换成 DB 回放——保证两种视图永不脱节。

---

## 15. Source / Citation 设计

### 15.1 Source 产生链路

```text
Tool 调用 → ToolResult.provenance → Agent 在 claim 中引用 source_ids
                    ↓
         SOURCE_FOUND 事件（实时进 Source Panel）
                    ↓
         Merge 阶段按 url_canonical 去重、重新编号
                    ↓
         Report Writer 只允许引用已注册的 source_id
                    ↓
         代码层校验引用完整性（output guardrail）
```

关键：**Source 由 tool 层自动产生，不依赖 LLM 自觉**。API 类数据源也是 Source（provider + endpoint + 人类可访问 URL），保证"HYPE TVL 增长 18%"这类来自 API 的数字同样可溯源。

### 15.2 来源可靠性分级

| 级别         | 含义          | 示例                                       |
| ------------ | ------------- | ------------------------------------------ |
| `primary`    | 一手/权威     | SEC EDGAR、公司 IR、项目官方文档、链上数据 |
| `secondary`  | 可信媒体/研究 | 主流财经媒体                               |
| `aggregator` | 聚合平台      | CoinGecko、DefiLlama、FMP                  |
| `unknown`    | 无法判定      | 博客、论坛、社媒                           |

Fact Checker 与 Report Writer 的 prompt 中明确：**冲突时优先 primary**；仅有 `unknown` 支撑的 claim 必须降级为 `inference` 或标注低置信度。

### 15.3 认知类型（Fact / Analysis / Prediction 分离）

```python
class EpistemicType(StrEnum):
    FACT                # 可验证的客观事实（但当前未附来源）
    SOURCE_BACKED_FACT  # 有来源支撑的事实 ← 报告主体应以此为主
    ANALYSIS            # 基于已获取数据的分析（数据可溯源，结论是推导）
    INFERENCE           # 基于不完整信息的推测
    PREDICTION          # 对未来的预测
    OPINION             # 观点
```

三重强制机制，缺一不可：

1. **Schema 强制**：`Claim.epistemic_type` 必填，LLM 无法省略。
2. **Prompt 强制**：每个 Agent 的 instructions 含分类定义与正反例。
3. **代码强制**：output guardrail 检查 `SOURCE_BACKED_FACT` 必须有 ≥1 source，否则自动降级为 `FACT` 并记 warning。

报告中 `ANALYSIS` / `INFERENCE` / `PREDICTION` 类内容必须出现在带标记的段落或明确的 Bull/Bear Case 章节中，不得与事实段落混排。

### 15.4 引用完整性的确定性校验

不用 LLM 校验，纯代码：

1. 提取报告 markdown 中所有 `[n]` → 必须都能映射到 `sources[n-1]`；
2. 每个 `SOURCE_BACKED_FACT` claim 的 `source_ids` 必须都存在于 `sources` 表；
3. 孤儿 source（未被任何 claim 引用）不进入最终编号；
4. URL 格式校验 + 抓取时 HTTP 状态记录（404 的 source 标记为不可用）。

任一项失败 → 触发 Report Writer 一次修正重试；仍失败则报告中降级标注并记录 warning 事件。

---

## 16. API 设计

### 16.1 Next.js BFF

| 方法                  | 路径                                    | 说明                                                            |
| --------------------- | --------------------------------------- | --------------------------------------------------------------- |
| `POST`                | `/api/research`                         | 启动研究。Body `{question, modelId?}`。返回 `text/event-stream` |
| `GET`                 | `/api/research`                         | 会话列表（分页、过滤）                                          |
| `GET`                 | `/api/research/{id}`                    | 会话详情（session + plan + report + sources + claims）          |
| `GET`                 | `/api/research/{id}/events?after={seq}` | 事件回放 / 重连 [P6]                                            |
| `DELETE`              | `/api/research/{id}`                    | 取消进行中 / 删除已完成 [P6]                                    |
| `GET`                 | `/api/models`                           | 可用模型目录（含 `available` 与禁用原因）                       |
| `GET`/`PATCH`         | `/api/settings`                         | 用户设置（默认模型、角色映射、报告语言）                        |
| `GET`                 | `/api/health`                           | 自身 + Python 服务健康状态                                      |
| `GET`/`POST`/`DELETE` | `/api/watchlist`                        | [P8]                                                            |

### 16.2 Python Agent Service

| 方法   | 路径                           | 说明                                                                 |
| ------ | ------------------------------ | -------------------------------------------------------------------- |
| `POST` | `/v1/research/stream`          | 核心端点。Body `{session_id, question, model_id, options}`。返回 SSE |
| `POST` | `/v1/research/{run_id}/cancel` | 取消运行 [P6]                                                        |
| `GET`  | `/v1/models`                   | 模型目录（含能力与可用性）                                           |
| `GET`  | `/v1/health`                   | 健康检查 + 各 provider 配额剩余                                      |
| `POST` | `/v1/tools/{name}/invoke`      | **调试专用**：直接调用单个 tool，用于开发与 eval                     |
| `GET`  | `/v1/debug/providers`          | 各 provider 缓存命中率与配额状态                                     |

`/v1/tools/{name}/invoke` 很重要：让 tool 可以脱离 LLM 独立测试和评估。

### 16.3 错误响应统一格式

```json
{ "error": { "code": "MODEL_NOT_AVAILABLE", "message": "…", "details": {} } }
```

---

## 17. 环境变量设计

`.env.example`（提交）/ `.env.local`（gitignore）。Python 与 Next.js 各自读取根目录 `.env.local`。

```dotenv
# ── LLM Providers（开发期以 DeepSeek 为主力，见 §9.7）
# OPENAI_API_KEY 开发期仅用于 tracing 上传（免费），不跑模型（见 §9.6）
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
MOONSHOT_API_KEY=
DEEPSEEK_API_KEY=
ZHIPU_API_KEY=
# 可选 base url 覆盖（自建代理/兼容端点）
OPENAI_BASE_URL=
MOONSHOT_BASE_URL=https://api.moonshot.cn/v1
DEEPSEEK_BASE_URL=https://api.deepseek.com
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# ── 数据源
TAVILY_API_KEY=
COINGECKO_API_KEY=              # Demo key，可空（走公共限流）
FMP_API_KEY=
SEC_USER_AGENT="market-research-agent your@email.com"   # SEC 强制要求

# ── 服务
AGENT_SERVICE_URL=http://127.0.0.1:8000
AGENT_SERVICE_HOST=127.0.0.1
AGENT_SERVICE_PORT=8000
INTERNAL_API_TOKEN=             # 两侧共享的随机串

# ── 数据库
DATABASE_URL=file:./data/app.db
AGENT_CACHE_DB=./data/provider-cache.db

# ── 模型默认值（开发期国内模型，见 §9.7）
DEFAULT_MODEL_ID=deepseek:deepseek-v4-pro
MODEL_ROLE_PLANNER=
MODEL_ROLE_BALANCED=
MODEL_ROLE_FAST=
MODEL_ROLE_WRITING=

# ── 执行上限（见 §7.2）
MAX_TASKS_PER_PLAN=6
MAX_PARALLEL_TASKS=4
MAX_TOOL_CALLS_PER_AGENT=12
TASK_TIMEOUT_S=120
TOTAL_TIMEOUT_S=420

# ── 可观察性
LOG_LEVEL=info
# 有 OPENAI_API_KEY 时设 false（开启免费 tracing）；无 key 时必须 true，否则每次运行报错（§9.6）
OPENAI_AGENTS_DISABLE_TRACING=false
```

### 17.2 需要申请的 key

**Phase 1 前（LLM）**

| 服务          | 用途                                                     | 充值建议         | 入口                  |
| ------------- | -------------------------------------------------------- | ---------------- | --------------------- |
| **DeepSeek**  | 主力模型（`v4-pro` / `v4-flash`）                        | **¥300**         | platform.deepseek.com |
| **智谱 GLM**  | 第二 provider，验证模型抽象层 + 中文写作对比             | **¥50**          | bigmodel.cn           |
| **OpenAI**    | **仅 tracing 上传**（免费，不跑模型，见 §9.6）           | **$5**（最低档） | platform.openai.com   |
| Moonshot Kimi | 可选。仅当 DeepSeek 的 strict json_schema 不可靠时作兜底 | 暂不充           | platform.kimi.com     |

**Phase 2 前（数据源）**

| 服务      | 用途                 | 免费额度        | 入口              |
| --------- | -------------------- | --------------- | ----------------- |
| Tavily    | Web 搜索与内容抓取   | 1000 credits/月 | tavily.com        |
| CoinGecko | 币价 / 市值 / 成交量 | Demo 档免费     | coingecko.com/api |
| DefiLlama | TVL / 协议数据       | **无需 key**    | —                 |

**Phase 3 前（美股数据源）**

| 服务      | 用途                   | 免费额度                                                                           | 入口                      |
| --------- | ---------------------- | ---------------------------------------------------------------------------------- | ------------------------- |
| FMP       | 财报 / 估值 / 指标     | 免费档有限流                                                                       | financialmodelingprep.com |
| SEC EDGAR | 10-K / 10-Q / 8-K 原文 | **无需 key**，但**必须**设 `SEC_EDGAR_USER_AGENT`（格式 `姓名 邮箱`），否则被封 IP | sec.gov                   |

> 开发期总计约 **¥350 + $5**。上线切 OpenAI 后单次研究成本约 $0.5（§9.7），
> 按实际使用频率再充即可。
>
> 三项建议在各控制台设好：**关掉自动续费**（代码有 `MAX_SESSION_COST_USD` 护栏，
> 但只挡单次会话，循环调用类 bug 需要账户层兜底）、**设月度消费上限**、**开消费提醒**。

---

## 18. 测试策略

### 18.1 分层

| 层                   | 工具                       | 覆盖                                                               | 是否调 LLM |
| -------------------- | -------------------------- | ------------------------------------------------------------------ | ---------- |
| Python 单元          | pytest                     | schema 校验、指标计算、URL 归一化、SSRF 判定、事件序列化、降级逻辑 | 否         |
| Provider 契约        | pytest + **respx**         | 每个 provider：正常/404/429/超时/畸形响应/缓存命中                 | 否         |
| Tool 集成            | pytest（录制 fixture）     | 真实响应快照 → 断言结构化解析与 provenance                         | 否         |
| **Workflow（关键）** | pytest + **ScriptedModel** | 全流程：planning→fan-out→fact check→report。含失败/超时/冲突场景   | **否**     |
| Live smoke           | pytest `-m live`           | 少量真实 API + 真实 LLM 调用，本地手动/每周 CI                     | 是         |
| 前端单元             | Vitest                     | 事件 reducer、SSE 解析、引用解析、格式化                           | 否         |
| 前端组件             | Vitest + RTL               | Activity Panel 状态渲染、Citation 交互                             | 否         |
| DB                   | Vitest + 内存 SQLite       | migration、queries、事务、事件批写                                 | 否         |
| E2E                  | Playwright [P6]            | 打桩 Python 服务，跑完整 UI 流程                                   | 否         |

### 18.2 `ScriptedModel` — 测试策略的核心

用 SDK 自带的 `agents.testing.ScriptedModel` 按脚本返回预设的 tool call 与结构化输出：

```python
from agents.testing import ScriptedModel, assistant_message, function_call

model = ScriptedModel([
    [function_call("get_market_data", {"asset": "HYPE"}, call_id="c1")],
    [function_call("get_tvl", {"protocol": "hyperliquid", "days": 30}, call_id="c2")],
    [assistant_message('{"summary": "…"}')],
])
```

价值：整个 multi-agent workflow 可以**确定性、零成本、毫秒级**地测试，包括工具失败、超时、schema 解析失败重试、引用缺失等难以用真实 LLM 复现的路径。这也验证了 §9 模型抽象层的设计正确性。

> **决策修正（P1-7 期间）**：原计划自建 `FakeModel`，已实现并通过测试后被替换掉。原因是它只实现了 `get_response`，而 `Runner.run_streamed` 走的是 `stream_response`——P1-7 的翻译层测试一跑，13 个用例一次性报错。这暴露了自建替身的根本问题：它必须跟着 SDK 的内部约定走，而那些约定我们既不控制、也难以独立验证。`ScriptedModel` 由 SDK 维护，同时覆盖两条调用路径，并额外提供调用快照（`ModelCall`，含 `streamed` 标记）、步骤级错误注入（`ModelStep.raise_error`）与 `assert_complete()`。
>
> 教训值得记下来：**针对 SDK 边界写测试时，要让测试跑在真实的 SDK 入口上**（这里是 `Runner.run_streamed`），而不是手搓 SDK 事件对象——后者只能验证我们对 SDK 结构的假设，而假设正是最容易错的地方。

### 18.3 CI（GitHub Actions）

单 workflow，两个 job 并行：

```text
python:  uv sync → ruff check → ruff format --check → basedpyright → pytest -m "not live"
web:     pnpm install → gen-types + git diff --exit-code → eslint → tsc --noEmit
         → drizzle-kit check → vitest run → next build
```

`gen-types + git diff --exit-code` 确保 Pydantic 与 TS 类型不漂移（§5.1）。

---

## 19. Evaluation Strategy

### 19.1 目录

```text
evals/
├── datasets/
│   ├── intent_routing.jsonl      # 问题 → 期望 question_type / agent
│   ├── tool_selection.jsonl      # 问题 → 期望调用的 tool 集合
│   ├── crypto_project.jsonl      # HYPE/BTC/ARB… 事实性问答
│   ├── stock_analysis.jsonl      # NVDA/AMD… 财务与估值
│   ├── financial_report.jsonl    # 财报解读
│   ├── fact_check.jsonl          # 含注入错误声明，检验能否识别
│   └── epistemic_labeling.jsonl  # 陈述 → 期望的认知类型
├── graders/
│   ├── deterministic.py          # 代码打分（可精确验证的项）
│   └── llm_judge.py              # LLM-as-judge（主观项）
├── runner.py                     # 跑 eval，输出 JSON + Markdown 报告
└── results/                      # 按 (日期, 模型) 归档，可对比
```

### 19.2 评估维度

| 维度                 | 打分方式                                          | 目标         |
| -------------------- | ------------------------------------------------- | ------------ |
| Agent 路由正确率     | 确定性（集合比对）                                | ≥ 90%        |
| Tool 选择召回/精确率 | 确定性                                            | recall ≥ 85% |
| **引用覆盖率**       | 确定性：有 source 的 fact claim / 全部 fact claim | ≥ 95%        |
| **引用有效性**       | 确定性：URL 可访问 + excerpt 在页面中可定位       | ≥ 90%        |
| **幻觉率**           | LLM judge：claim 是否被其 source 支撑             | ≤ 5%         |
| 认知类型正确率       | LLM judge + 人工抽检                              | ≥ 85%        |
| 冲突检出率           | 确定性（注入已知冲突数据）                        | ≥ 80%        |
| 报告完整性           | 确定性：`report_sections` 是否齐全、非空          | 100%         |
| 数值准确性           | 确定性：与 fixture 真值比对（容差）               | ≥ 95%        |
| 成本/延迟            | 确定性统计                                        | p50 < 90s    |

### 19.3 使用方式

- **每个 Phase 结束时**跑一次全量 eval，结果归档，与上一版对比 → 防止回归。
- 更换模型或改 prompt 后跑对应子集 → 这是回答"哪个模型效果最好"的唯一可靠方式。
- **幻觉率与引用覆盖率是本项目的核心指标**，优先级高于响应速度。
- Eval 尽量使用录制的 fixture 数据（数据源稳定可比），只在专门的 live eval 中打真实 API。

---

## 20. 可观察性

### 20.1 三条数据线

1. **事件日志**（`research_events`）：append-only，可完整重放任何一次研究。
2. **结构化指标**（`tool_calls` / `agent_runs`）：耗时、成本、token、缓存命中、错误码。
3. **OpenAI Agents SDK Tracing**：有 OpenAI key，默认开启（§9.6）。覆盖 LLM 交互细节，且对国内模型的 run 同样有效。

> 三者不重叠，都要做：
> ① SDK tracing 回答"模型看到了什么、为什么这么决策"（LLM 层）；
> ② `agent_runs` / `tool_calls` 回答"多慢、多贵、哪个工具在失败、缓存命中率多少"（工程层）——
> 这些是 `/debug` 页面与跨模型 eval 对比的唯一数据源，SDK tracing 给不了；
> ③ `research_events` 回答"这次研究到底发生了什么"，且是前端 UI 与历史回放的数据源。
>
> `agent_runs` 额外记录 prompt hash 与 token 数（**不记录 prompt 全文**，只记 hash + 长度），
> 便于在不翻 trace 的情况下定位"同一问题两次结果不同"这类问题。

### 20.2 内置分析页（`/debug`，Phase 6）

直接回答需求 §36 提出的问题：

- 阶段耗时瀑布图 → "为什么这次研究花了 2 分钟？"
- Agent 耗时/成本排行 → "哪个 Agent 最慢？"
- Tool 失败率与 p95 耗时排行 → "哪个 Tool 最容易失败？"
- 按模型聚合的成本 / 耗时 / eval 分数 → "哪个模型效果最好？"
- Provider 缓存命中率与配额剩余

### 20.3 日志

structlog JSON 输出，每条日志绑定 `session_id` / `task_id` / `agent` / `tool`。**日志中禁止出现 API key 与完整 LLM prompt**（prompt 只记 hash 与长度）。

---

## 21. MVP 范围

### 21.1 MVP 完成的定义（Definition of Done）

MVP = Phase 0 ~ Phase 5。达成标准：

> 输入「帮我做一份 HYPE 的投资研究报告」或「分析 NVDA 最近一季财报」，能在 2 分钟内：
> 展示研究计划 → 实时显示多个 Agent 与 Tool 的执行状态 → 产出带 `[n]` 引用、区分事实/分析/推测、声明数据缺口的结构化 Markdown 报告 → 会话完整落库并可从历史记录重新查看。
> 且：可在 UI 切换至少 3 个不同 Provider 的模型并均能跑通全流程。

对应需求 §44 的必须项逐条核对：

| 必须项            | 落地位置                           |
| ----------------- | ---------------------------------- |
| 多模型选择        | Phase 1（§9）                      |
| Tool Calling      | Phase 1-4（§8）                    |
| Multi-Agent       | Phase 5（§6）                      |
| Streaming         | Phase 1（§12）                     |
| Agent Activity UI | Phase 1 骨架 → Phase 6 完善（§13） |
| Research Session  | Phase 1（§14）                     |
| Source Citation   | Phase 2（§15）                     |
| SQLite            | Phase 0（§10）                     |
| Research History  | Phase 6（§14.3）                   |

### 21.2 MVP 明确不做

登录/多用户 · Watchlist/Alerts · 宏观经济数据 · RAG/知识库 · MCP · PostgreSQL/Redis · LangGraph · 定时研究 · 报告导出 PDF · 移动端适配 · 多语言 UI

---

## 22. 后续扩展路线

| 阶段 | 内容                                    | 触发条件                            |
| ---- | --------------------------------------- | ----------------------------------- |
| P7   | Evaluation 系统建设完整                 | MVP 跑通后立刻做                    |
| P8a  | Watchlist + 资产详情页                  | 手动重复查询同一批标的时            |
| P8b  | 宏观模块（Fed/CPI/DXY/VIX，FRED API）   | 需要跨市场联动分析时                |
| P8c  | PostgreSQL + pgvector + RAG 知识库      | 需要存 PDF/研报/笔记时              |
| P8d  | 定时研究 + Alerts（需任务队列 + Redis） | 需要"每天早上给我 BTC 简报"时       |
| P8e  | MCP Server 暴露自身 tools               | 想在 Claude/Cursor 里复用这些工具时 |
| P8f  | LangGraph 迁移                          | §4.1 决策清单满足                   |
| P8g  | 报告导出（PDF/Notion）、多轮追问对话    | 按需                                |

---

## 23. 技术风险

| ID  | 风险                                                                                  | 影响                               | 概率                      | 缓解措施                                                                                                                                                                                                  |
| --- | ------------------------------------------------------------------------------------- | ---------------------------------- | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | **FMP 免费额度 250/day** 很快耗尽                                                     | 美股研究不可用                     | 高                        | 财报/filing 永久缓存；SEC EDGAR（无限额）作为财务数据的主路径，FMP 只补估值比率；配额监控 + `QUOTA_EXHAUSTED` 明确降级；预留付费开关                                                                      |
| R2  | **LLM 成本失控**（多 Agent × 多轮 tool call）                                         | 费用不可预期                       | 中                        | §7.2 硬上限；每 session 成本上限 + 超限告警事件；默认使用便宜模型；成本实时显示在 UI                                                                                                                      |
| R3  | **部分模型 structured output 支持弱**（DeepSeek/GLM 仅 `json_object`，不强制 schema） | 用户切到这些模型时 schema 解析失败 | 中（OpenAI 优先后已缓解） | §9.4 的 `json_mode` 路径作为**一等实现**并单测覆盖；**schema 保持扁平**；解析失败带错误信息重试 2 次；每个模型在 Phase 5 末冒烟 eval，结果写入 catalog `verified` 字段，UI 标注"已验证/未验证"            |
| R3b | **LLM 调用成本**：默认配置下约 $0.5/次研究（§9.7）                                    | 高频使用时费用可观                 | 中                        | 成本实时显示 + `MAX_SESSION_COST_USD` 护栏；缓存命中降低重复取数的 LLM 轮次；成本敏感时把 `BALANCED` 降到 `luna`（约 $0.1/次）；prompt 缓存（OpenAI cached input 为标准价 1/10）                          |
| R3c | **tracing 会上传 prompt/响应到 OpenAI**                                               | 研究内容外泄至第三方               | 低                        | 默认开启换取调试便利；可用 `trace_include_sensitive_data=False` 或 `OPENAI_AGENTS_DISABLE_TRACING=true` 关闭（§9.6-4）                                                                                    |
| R4  | **链上明细数据源缺失**（holders/whale/exchange flow 无好的免费源）                    | 需求 §10 覆盖不全                  | 高                        | Phase 3 明确降级：优先做 DefiLlama 能给的（TVL/fees/volume）+ 项目官方 API（如 Hyperliquid）+ 区块浏览器免费档；拿不到的一律进 `data_gaps` **绝不让 LLM 编造**；付费源（Dune/Nansen/Artemis）留作后续开关 |
| R5  | **Prompt injection**（抓取的网页含恶意指令）                                          | 报告被污染                         | 中                        | §17.1-5 隔离标签 + 指令声明 + planner 不接触 web 内容 + 注入检测 warning；eval 中加入注入用例                                                                                                             |
| R6  | **SSE 长连接被中断**（Next.js dev server / 反代 / 浏览器超时）                        | 研究结果丢失                       | 中                        | HEARTBEAT 事件保活；Next.js 侧消费不绑 request signal（断开也落库）；Phase 6 做 `?after=seq` 重连回放                                                                                                     |
| R7  | **SQLite 写锁竞争**                                                                   | 写失败                             | 低                        | 决策 C（单 writer）+ WAL + busy_timeout + 事件批写                                                                                                                                                        |
| R8  | **Agents SDK 版本演进**导致 breaking change                                           | 需要改造                           | 中                        | 锁定 minor 版本；SDK 类型只出现在 `agents/` 与 `models/` 目录（业务代码不 import SDK 类型）；事件协议自有定义不透传 SDK 结构                                                                              |
| R9  | **数据源指标定义不一致**（不同源的 TVL/市值算法不同）                                 | 报告出现矛盾数字                   | 高                        | Merge 阶段做数值冲突检测 → `CONFLICT_DETECTED`；报告中并列展示各源数值而非取平均；provenance 必带 provider                                                                                                |
| R10 | **研究延迟过长**（>3min）导致体验差                                                   | 可用性                             | 中                        | 并行 fan-out；缓存；plan 先行显示；分阶段渐进渲染报告；p50 延迟作为 eval 指标                                                                                                                             |
| R11 | **金融信息准确性责任**                                                                | 误导决策                           | —                         | 报告固定免责声明；严禁"买入/卖出建议"口径（output guardrail 检查）；一切结论必须可溯源                                                                                                                    |
| R12 | **网页正文提取质量差**（SPA / 付费墙）                                                | 信息缺失                           | 中                        | Tavily 已清洗正文为主路径，trafilatura 为备；提取失败明确记 `data_gaps` 不猜内容                                                                                                                          |

---

## 24. 技术债务登记

MVP 阶段**有意接受**的妥协，集中记录以免遗忘：

| ID  | 债务                                                        | 接受理由                             | 偿还时机                      |
| --- | ----------------------------------------------------------- | ------------------------------------ | ----------------------------- |
| D1  | 无用户系统，`user_id` 恒为 `'local'`                        | 个人使用                             | 需要多用户时（schema 已预留） |
| D2  | SQLite 单文件，无备份策略                                   | 数据可重新生成                       | Phase 8 迁 PG                 |
| D3  | 研究任务与 HTTP 生命周期绑定，进程重启即丢失                | 单次研究 <3min                       | §4.1 触发时                   |
| D4  | 断线不可恢复正在进行的 run（只能等它落库）                  | 场景少                               | Phase 6                       |
| D5  | Provider 缓存无主动失效/预热机制                            | TTL 够用                             | 出现数据陈旧投诉时            |
| D6  | Fact Checker 只校验 high-impact claims（非全量）            | 控成本                               | 成本允许时放开                |
| D7  | `data_gaps` 依赖 LLM 自觉声明（虽有 schema 强制）           | 无完美方案                           | eval 持续监控                 |
| D8  | 补充研究最多 1 轮                                           | 防失控                               | 有可靠停止判据后放开          |
| D9  | 前端无虚拟滚动，长事件流可能卡顿                            | MVP 事件量小                         | 事件 >500 条时                |
| D10 | Prompt 硬编码在 `.md` 文件，无版本管理/AB                   | 简单优先                             | Phase 7 eval 需要对比时       |
| D11 | 报告仅中文；`report_language` 设置项预留未实现              | 个人使用                             | 按需                          |
| D12 | 无 tool 级别的并发配额协调（多任务可能同时打同一 provider） | 限流器已在 provider 层全局共享，足够 | 出现 429 频发时               |

---

## 25. 性能与可扩展性

### 25.1 延迟预算（目标 p50 < 90s）

| 阶段           | 预算 | 优化手段                                                                            |
| -------------- | ---- | ----------------------------------------------------------------------------------- |
| 意图分类       | 2s   | 用 FAST 模型；简单模式用正则/词典短路，完全跳过 LLM                                 |
| Planning       | 8s   | 单次调用，prompt 精简                                                               |
| 研究 fan-out   | 40s  | **并行是最大杠杆**：4 路并发；tool 内部也并发（`asyncio.gather`）；缓存命中直接返回 |
| Fact Check     | 12s  | 只查 high-impact claims；批量单次调用而非逐 claim                                   |
| Report Writing | 25s  | 输入预算裁剪；可选分段流式，首字节更快                                              |

### 25.2 关键优化点

1. **并行**：同层任务 `asyncio.gather`；单个 Agent 内的独立 tool 调用允许并行（模型支持时）。
2. **缓存**：分级 TTL（§8.3）。财报/filing 永久缓存收益最大。
3. **连接复用**：全局 `httpx.AsyncClient`（HTTP/2 + keep-alive），避免每次握手。
4. **前端**：事件 reducer 用不可变增量更新 + `useSyncExternalStore`；Activity 列表按任务分片记忆化；报告 markdown 增量解析。
5. **DB**：事件批写；列表页只查必要列；`(user_id, created_at DESC)` 索引覆盖历史列表。
6. **Token**：findings 传给 Report Writer 前按重要度裁剪；工具输出摘要化（不把全量 JSON 塞进上下文）。

### 25.3 可扩展性的预留（低成本，不增当前复杂度）

| 未来需求             | 现在做的预留                                                                 |
| -------------------- | ---------------------------------------------------------------------------- |
| PostgreSQL           | §10.1 schema 约束；DB 访问全部经 `db/queries/` 收敛                          |
| 多用户               | 所有表有 `user_id` + 索引                                                    |
| 水平扩展 Python 服务 | 服务无状态（决策 C）；缓存是独立文件，可换成外部缓存                         |
| 任务队列             | 编排层与 HTTP 层已分离（`orchestrator/` 不 import FastAPI），可直接接 worker |
| 新增数据源           | Provider 基类 + Tool 契约固定，新增 provider 不动 Agent 代码                 |
| 新增模型             | 只改 `models/catalog.py`                                                     |
| MCP                  | Tool 已是纯函数 + Pydantic schema，包一层即可暴露                            |

---

## 26. 每阶段的收尾检查清单

对应需求 §47，每个 Phase 结束必须全部通过：

```text
[ ] 1.  pytest -m "not live" 全绿
[ ] 2.  pnpm vitest run 全绿
[ ] 3.  tsc --noEmit 无错误
[ ] 4.  ruff check + ruff format --check 通过
[ ] 5.  basedpyright 无错误
[ ] 6.  drizzle-kit check 通过；migration 可从零重建
[ ] 7.  端到端手动验证一个真实问题（Agent workflow 正常）
[ ] 8.  Streaming 验证：事件顺序正确、无丢失、断开后仍完整落库
[ ] 9.  更新 README + docs（含新增环境变量）
[ ] 10. 更新 ROADMAP.md 状态标记 + 写阶段小结
[ ] 11. 跑一次 evals（Phase 7 起）并归档对比
[ ] 12. git commit（约定式提交，阶段结束打 tag）
```

---

## 27. 开放问题与已确认事项

### 27.1 已确认（2026-09-07）

| #   | 问题           | 结论                                                                                                                            |
| --- | -------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Q1  | 项目/仓库名    | ✅ 沿用 `market-research-agent`                                                                                                 |
| Q3  | 手上的 API key | ✅ 现有国内模型（Kimi / DeepSeek / GLM 之一）；**将申请 OpenAI key 并以 OpenAI 模型为首选** → 见 §9.6、§9.7、§17.2              |
| Q4  | 具体型号       | ✅ OpenAI 首选：`gpt-5.6-terra`（主力）/ `sol`（planner+writer）/ `luna`（fast）/ `gpt-6-astra`（可选升档）；国内模型保留为备选 |
| —   | Agent 数量     | ✅ 采纳 9 → 6 的裁剪（§6.1），后续有实测证据再拆                                                                                |

### 27.2 仍待确认（不确认则按默认执行）

| #   | 问题                                                      | 默认方案                                             |
| --- | --------------------------------------------------------- | ---------------------------------------------------- |
| Q2  | 报告语言                                                  | 中文（技术术语保留英文）                             |
| Q5  | 是否需要 Docker 开发环境                                  | 不需要，本机直跑；Phase 8 再补                       |
| Q7  | 是否允许 tracing 上传研究内容到 OpenAI                    | 允许（换取调试便利），随时可用环境变量关闭（§9.6-4） |
| Q6  | 链上深度数据（whale/holders/exchange flow）是否愿意付费源 | 先做免费能覆盖的部分，缺失明确声明                   |

---

## 附录 A：架构决策记录（ADR）索引

重要决策会在 `docs/adr/` 下留档，格式：背景 / 选项 / 决策 / 后果。

| ADR  | 主题                                          | 状态                 |
| ---- | --------------------------------------------- | -------------------- |
| 0001 | 使用 OpenAI Agents SDK，暂不引入 LangGraph    | 已决定（§3.1, §4.1） |
| 0002 | 编排层为确定性 Python 代码，非 LLM 自主循环   | 已决定（决策 A）     |
| 0003 | 子 Agent 采用 Agents-as-Tools 而非 Handoff    | 已决定（决策 B）     |
| 0004 | Next.js 为数据库唯一 writer                   | 已决定（决策 C）     |
| 0005 | 自建 ModelRegistry 而非直接使用 MultiProvider | 已决定（§9.2）       |
| 0006 | Pydantic 为跨语言类型真源                     | 已决定（§5.1）       |
| 0007 | SSE 而非 WebSocket                            | 已决定（§3.4）       |

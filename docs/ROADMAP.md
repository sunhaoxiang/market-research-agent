# Roadmap & 开发状态

> 本文档是**执行文档**（频繁更新），跟踪阶段任务与进度。
> 架构与技术决策见 [`DEVELOPMENT_PLAN.md`](./DEVELOPMENT_PLAN.md)，引用记作 `[DP §n]`。
>
> **每完成一个任务就更新此表的状态**；每完成一个阶段，跑 `[DP §26]` 的收尾清单并写阶段小结。

- 最后更新：2026-09-07
- 当前阶段：**Phase 0 已完成 → Phase 1 待开始**

### 已确认的前置决策（2026-09-07）

| 项         | 结论                                                                                                                                                            | 影响                                                                                                                  |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 开发计划   | ✅ 通过                                                                                                                                                         | 开始 Phase 0                                                                                                          |
| Agent 数量 | ✅ 9 → 6（采纳 `[DP §6.1]` 裁剪）                                                                                                                               | 后续有实测证据再拆                                                                                                    |
| 项目名     | ✅ 沿用 `market-research-agent`                                                                                                                                 | —                                                                                                                     |
| 模型选型   | ✅ **开发期国内模型，上线后切 OpenAI**：开发用 `deepseek-v4-pro`（主力）+ `v4-flash`（fast）+ `glm-5.3-flash`（第二 provider）；OpenAI 仅充 $5 用于免费 tracing | 成本降至约 ¥0.69/次（OpenAI 配置的 1/5）；`json_mode` 降级路径重回主路径（P1-4b 关键）；新增 §9.8 prompt 缓存编码约束 |

---

## 状态图例

| 标记 | 含义                            |
| ---- | ------------------------------- |
| ⬜   | 未开始                          |
| 🟡   | 进行中                          |
| ✅   | 已完成                          |
| 🔴   | 阻塞（在备注写明阻塞原因）      |
| ⏭️   | 已跳过 / 延后（在备注写明理由） |

---

## 总体进度

| Phase | 名称                            | 任务数 | 状态 | 说明                             |
| ----- | ------------------------------- | ------ | ---- | -------------------------------- |
| —     | 开发计划确认                    | 1      | ✅   | 已确认，见上表                   |
| P0    | 项目初始化                      | 10     | ✅   | Monorepo / 工具链 / DB / CI      |
| P1    | Agent 骨架 + 多模型 + Streaming | 13     | ⬜   | **端到端最小闭环**               |
| P2    | Web Research + Citation         | 10     | ⬜   | Source / Claim / 报告雏形        |
| P3    | Crypto 数据能力                 | 11     | ⬜   | 市场 / TVL / Tokenomics / 链上   |
| P4    | 美股数据能力                    | 11     | ⬜   | 行情 / 财务 / 估值 / SEC         |
| P5    | Multi-Agent 完整编排            | 9      | ⬜   | Fact Checker / 并行 / 报告结构   |
| P5.5  | **MVP 验收**                    | 1      | ⬜   | 见 `[DP §21.1]`                  |
| P6    | 高级 UX + 历史 + 可观察性       | 11     | ⬜   | Activity 完善 / History / /debug |
| P7    | Evaluation 系统                 | 8      | ⬜   | 数据集 / grader / runner         |
| P8    | 扩展能力                        | 8      | ⬜   | 按需触发，非线性                 |

---

## Phase 0 — 项目初始化

**目标**：两个服务能起来、能互相通、数据库能建表、CI 能跑。**不含任何业务逻辑。**

| ID    | 任务                                                                                                                                                        | 依赖             | 状态 | 验收                                                        |
| ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ---- | ----------------------------------------------------------- |
| P0-1  | Monorepo 骨架：`pnpm-workspace.yaml`、根 `package.json`（scripts + `packageManager`）、`.nvmrc`、`.python-version`、`.gitignore`、`.editorconfig`、`.npmrc` | —                | ✅   | `pnpm install` 成功                                         |
| P0-2  | `.env.example` 按 `[DP §17]` 编写；根目录 `.env.local` 加载机制（Python + Next 共用）                                                                       | P0-1             | ✅   | 两侧都能读到变量                                            |
| P0-3  | gitleaks 配置 + husky pre-commit（secret 扫描 + 拒绝提交 `.env` + 格式检查）                                                                                | P0-1             | ✅   | 钩子就位；gitleaks 未安装时优雅降级并提示                   |
| P0-4  | Next.js 16 app 初始化（App Router + TS strict + Tailwind 4 + ESLint flat config + Prettier）                                                                | P0-1             | ✅   | `pnpm dev` 起 3000，首页可访问；`next build` 通过           |
| P0-5  | Python 服务初始化：uv 项目、Ruff + basedpyright、structlog（含脱敏）、FastAPI + `/v1/health`                                                                | P0-1             | ✅   | uvicorn 起 8000，health 200；7 个测试通过                   |
| P0-6  | Drizzle 配置 + `[DP §10.2]` 全量 schema（10 张表）+ migration + PRAGMA + CHECK 约束                                                                         | P0-4             | ✅   | 从零建库成功；8 条 CHECK 约束进入 DDL                       |
| P0-7  | `db/client.ts`（懒初始化）+ `db/queries/sessions.ts` + schema 测试（内存 SQLite）                                                                           | P0-6             | ✅   | 13 个测试通过，覆盖 CHECK/外键级联/事务回滚/JSON 往返       |
| P0-8  | `packages/shared` 骨架 + `scripts/gen-types.sh`（Pydantic→JSON Schema→TS）                                                                                  | P0-5, P0-4       | ✅   | 脚本可运行；Phase 1 前输出占位类型                          |
| P0-9  | `scripts/dev.sh`（并行启动 + 进程联动退出）+ Next `/api/health` 聚合探测                                                                                    | P0-4, P0-5       | ✅   | 一条命令起全栈；`/api/health` 返回 database ok + agent 状态 |
| P0-10 | GitHub Actions CI（python / web / secret-scan 三个 job，见 `[DP §18.3]`）                                                                                   | P0-5, P0-7, P0-8 | ✅   | 本地等价命令全绿（远端待首次 push 验证）                    |

**阶段验收 ✅**：`scripts/dev.sh` 同时起两个服务，首页与 `/api/health` 均能显示 Python 服务健康状态与 provider 配置情况，10 张表已建，本地全套检查通过。

---

## Phase 1 — Agent 骨架 + 多模型 + Streaming

**目标**：**打通端到端最小闭环**。用户提问 → Planner 出计划 → 一个 Agent 调一个假 tool → 事件流实时显示 → 结果落库。这一阶段跑通后，后续都是"往框架里填内容"。

| ID    | 任务                                                                                                                                                                                                   | 依赖              | 状态 | 验收                                                                                                      |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------- | ---- | --------------------------------------------------------------------------------------------------------- |
| P1-1  | Pydantic schemas：`ResearchEvent` 全事件类型、`ResearchPlan`、`ResearchTask`、`Entity`、`Claim`、`Source`、`ResearchFinding`、`ToolResult`                                                             | P0-5              | ✅   | 单元测试覆盖序列化/反序列化                                                                               |
| P1-2  | 类型生成打通：P1-1 的 schema 全部导出为 TS 类型，CI 检查漂移                                                                                                                                           | P1-1, P0-8        | ✅   | `git diff --exit-code` 通过                                                                               |
| P1-3  | `models/capabilities.py` + `models/catalog.py`：按 `[DP §9.7]` 落库开发期 5 个 + 上线期 4 个模型 + Anthropic/Google 条目标记 `available=false`                                                         | P0-5              | ✅   | 目录可加载并校验；角色默认映射到 DeepSeek                                                                 |
| P1-4  | `models/registry.py`：`resolve(model_id)` / `for_role(role)`，三种 adapter（`openai_responses` / `openai_chat` / `litellm`），per-provider 客户端缓存；`bootstrap_sdk()` 处理 tracing 开关 `[DP §9.6]` | P1-3              | ✅   | 每种 adapter 有构造测试（不发真请求）；无 key 的 provider 在 registry 层明确报错；不依赖任何 SDK 全局状态 |
| P1-4b | **结构化输出三路径**：`native_schema`（主）/ `json_mode` / `prompt_only` + Pydantic 二次校验 + 带错误信息重试 2 次 `[DP §9.4, R3]`；schema 扁平化约束                                                  | P1-4, P1-1        | ✅   | 三条路径各有单测；故意返回坏 JSON 能被修正                                                                |
| P1-5  | `GET /v1/models` + Next `GET /api/models`：过滤无 key 的 provider 并给出禁用原因                                                                                                                       | P1-4              | ✅   | 只配 1 个 key 时其余显示禁用                                                                              |
| P1-6  | 事件总线 `observability/event_bus.py`：`asyncio.Queue` + seq 分配 + heartbeat 任务                                                                                                                     | P1-1              | ✅   | 并发发事件时 seq 严格递增                                                                                 |
| P1-7  | SDK 事件翻译层：`RunItemStreamEvent`/`AgentUpdatedStreamEvent` → 本协议事件 `[DP §12.1]`                                                                                                               | P1-6              | ✅   | 用 ScriptedModel 验证翻译正确                                                                             |
| P1-8  | ~~自建 `FakeModel`~~ → **改用 SDK 自带 `agents.testing.ScriptedModel`**（自建版只实现 `get_response`，`run_streamed` 走 `stream_response`，P1-7 一跑就暴露）`[DP §18.2]`                               | P1-4              | ✅   | 能驱动一次完整 run                                                                                        |
| P1-9  | Research Manager Agent（planner）+ prompt + plan 代码校验（任务数上限/agent 合法/无环）                                                                                                                | P1-4, P1-1        | ✅   | v4-pro 对 3 个样例问题 3/3 产出合法 plan，零修复                                                          |
| P1-10 | Orchestrator 骨架：`state.py` / `executor.py`（分层 fan-out + 超时 + 失败降级）/ `pipeline.py`；接入执行上限 `[DP §7.2]`                                                                               | P1-9, P1-6        | ✅   | ScriptedModel 下走完 planning→执行→完成                                                                   |
| P1-11 | `POST /v1/research/stream` SSE 端点 + Next `POST /api/research`（消费/落库/转发，客户端断开仍落库）+ `lib/sse.ts` 解析器                                                                               | P1-10, P0-7       | ⬜   | 浏览器实时收到事件；断开后 DB 完整                                                                        |
| P1-12 | 前端最小闭环：提问框 + 模型选择器 + Activity Panel 骨架（计划树 + 状态点亮）+ 事件 reducer + store                                                                                                     | P1-11, P1-5, P1-2 | ⬜   | 提问后能看到计划与逐节点点亮                                                                              |
| P1-13 | **可观察性埋点**（`[DP §20.1]`）：`agent_runs` / `tool_calls` 写入 + prompt hash / token / 成本记录；SDK tracing 开关验证                                                                              | P1-11             | ⬜   | 一次研究后两张表数据完整，成本可核算；`/debug` 与 eval 的数据基础就绪                                     |

**阶段验收**：输入任意问题 → 前端实时显示"理解问题 / 制定计划 / 任务树点亮 / 完成"，session 与 events 完整落库；至少 2 个不同 Provider 的模型都能跑通。

---

## Phase 2 — Web Research + Source / Citation

**目标**：第一个有真实价值的能力——能搜、能读、能引用。

| ID    | 任务                                                                                                                      | 依赖        | 状态 | 验收                                        |
| ----- | ------------------------------------------------------------------------------------------------------------------------- | ----------- | ---- | ------------------------------------------- |
| P2-1  | `providers/base.py`：httpx 客户端池 + 分级缓存（SQLite KV）+ 令牌桶限流 + 配额计数 + tenacity 重试 + 自动埋点 `[DP §8.3]` | P1-1        | ⬜   | 契约测试覆盖 429/5xx/超时/缓存命中          |
| P2-2  | `SearchProvider` 抽象 + Tavily 实现（可切 Exa/Brave 的接口设计）                                                          | P2-1        | ⬜   | respx 契约测试通过                          |
| P2-3  | `web_fetch` 抓取器：SSRF 防护（DNS→IP 校验、协议/重定向/大小/超时限制）+ trafilatura 正文提取                             | P2-1        | ⬜   | 内网 IP / 超大响应被正确拦截                |
| P2-4  | `tools/web/`：`web_search` / `web_fetch` / `news_search`，全部返回 `ToolResult` + provenance                              | P2-2, P2-3  | ⬜   | `/v1/tools/{name}/invoke` 可单独调用        |
| P2-5  | Prompt injection 隔离：`<untrusted_web_content>` 包裹 + instructions 声明 + 注入迹象 warning 事件 `[DP §17.1-5]`          | P2-4        | ⬜   | 含注入指令的样例页面不改变 Agent 行为       |
| P2-6  | Web Research Agent + prompt（产出 `ResearchFinding`，含 claims / sources / data_gaps）                                    | P2-4, P1-10 | ⬜   | 对"HYPE 最近有什么新闻"产出带来源的 finding |
| P2-7  | Source 归一化与去重：URL canonical、`reliability` 分级、`SOURCE_FOUND` 事件、引用重新编号 `[DP §15.1-15.2]`               | P2-6        | ⬜   | 同一 URL 不同参数被正确合并                 |
| P2-8  | Report Writer Agent v1 + `ResearchReport` schema + `[n]` 引用生成                                                         | P2-7, P1-4  | ⬜   | 产出带引用的 Markdown                       |
| P2-9  | 引用完整性确定性校验 + output guardrail + 一次修正重试 `[DP §15.4]`                                                       | P2-8        | ⬜   | 故意注入坏引用能被检出并修正                |
| P2-10 | 前端：Report 渲染（sanitize + `[n]` 可点击）+ Source Panel（悬浮预览 excerpt/domain/时间）+ 认知类型徽标                  | P2-8, P1-12 | ⬜   | 点 `[1]` 高亮对应来源                       |

**阶段验收**：提问「HYPE 最近有什么重要进展？」能产出带真实可点击引用、区分事实/分析的 Markdown 报告。

---

## Phase 3 — Crypto 数据能力

**目标**：从"只会搜网页"升级为"有结构化金融数据"。

| ID    | 任务                                                                                             | 依赖            | 状态 | 验收                                    |
| ----- | ------------------------------------------------------------------------------------------------ | --------------- | ---- | --------------------------------------- |
| P3-1  | CoinGecko provider（限流/缓存/错误映射）                                                         | P2-1            | ⬜   | 契约测试通过                            |
| P3-2  | DefiLlama provider                                                                               | P2-1            | ⬜   | 契约测试通过                            |
| P3-3  | `resolve_asset`：符号→coin id 消歧（含同名冲突处理，多候选时返回列表让 Agent 选）                | P3-1            | ⬜   | "HYPE" 正确解析到 Hyperliquid           |
| P3-4  | `tools/crypto/`：`get_crypto_price` / `get_market_data` / `get_price_history`                    | P3-1, P3-3      | ⬜   | 结构化输出 + provenance 完整            |
| P3-5  | `tools/system/compute_metrics`：涨跌幅 / CAGR / 波动率 / 百分位（纯 Python）`[DP §8.5]`          | P1-1            | ⬜   | 单元测试含边界值                        |
| P3-6  | `tools/defi/`：`get_tvl` / `get_protocol_fees_revenue` / `get_dex_volume` / `get_chain_overview` | P3-2            | ⬜   | HYPE/Hyperliquid 数据正确               |
| P3-7  | `tools/crypto/get_tokenomics`：供应量 / 分配 / 解锁（数据源覆盖度调研 + 缺失明确声明）           | P3-1            | ⬜   | 拿不到的字段进 `data_gaps`              |
| P3-8  | 链上数据源选型调研 + 可行子集实现（`get_chain_activity` 等），受限项写入风险登记 `[DP §23 R4]`   | P3-2            | ⬜   | 输出实际覆盖度结论文档                  |
| P3-9  | Crypto Research Agent + prompt（整合 crypto/defi/onchain/web tools）                             | P3-4~P3-8, P2-6 | ⬜   | 对 3 个样例 crypto 问题产出完整 finding |
| P3-10 | 数值冲突检测：多源同指标差异 → `CONFLICT_DETECTED` + 报告并列展示 `[DP §23 R9]`                  | P3-9            | ⬜   | 注入冲突数据能被检出                    |
| P3-11 | 前端：`METRIC_FOUND` 驱动的 Recharts 图表（价格 / TVL 走势）内嵌报告                             | P3-9, P2-10     | ⬜   | 报告中显示 30d TVL 曲线                 |

**阶段验收**：「介绍一下 HYPE」「分析 HYPE 最近一个月上涨的原因」「查询 HYPE 的 TVL、交易量和资金变化」三个问题都能产出带数据、图表和引用的报告。

---

## Phase 4 — 美股数据能力

**目标**：对称地补齐美股研究。**注意 FMP 250/day 额度**，优先用 SEC EDGAR。

| ID    | 任务                                                                                                                       | 依赖         | 状态 | 验收                                |
| ----- | -------------------------------------------------------------------------------------------------------------------------- | ------------ | ---- | ----------------------------------- |
| P4-1  | FMP provider（含日配额计数 + `QUOTA_EXHAUSTED` 快速失败）                                                                  | P2-1         | ⬜   | 配额耗尽时优雅降级                  |
| P4-2  | SEC EDGAR provider（`User-Agent` 合规、10 req/s 限流、`submissions` + `companyfacts`）                                     | P2-1         | ⬜   | 契约测试通过                        |
| P4-3  | `resolve_ticker` + ticker↔CIK 映射（本地缓存 SEC company_tickers.json）                                                    | P4-2         | ⬜   | NVDA→CIK 正确                       |
| P4-4  | `tools/stocks/`：`get_stock_quote` / `get_company_profile` / `get_price_history` / `get_peers` / `compare_to_index`        | P4-1, P4-3   | ⬜   | 结构化输出完整                      |
| P4-5  | `tools/financials/` 三表：income / balance / cash flow（优先 XBRL，FMP 兜底）                                              | P4-2, P4-1   | ⬜   | 与官方财报数字一致                  |
| P4-6  | `tools/financials/get_growth_metrics`：YoY / QoQ / CAGR / margin trend（**Python 计算**）                                  | P4-5, P3-5   | ⬜   | 与手算一致                          |
| P4-7  | `tools/financials/get_valuation_metrics` + `get_valuation_history`（历史估值分位）                                         | P4-1         | ⬜   | "NVDA PE 处于 5 年 X 分位"可回答    |
| P4-8  | `tools/sec/`：`list_sec_filings` / `get_filing_section`（10-K Item 1A/7、10-Q）/ `get_xbrl_facts` / `get_earnings_summary` | P4-2         | ⬜   | 能取出指定章节正文                  |
| P4-9  | 大文件处理策略：10-K 全文分节 + 按需截取 + 永久缓存（避免爆上下文）                                                        | P4-8         | ⬜   | 单次注入上下文可控                  |
| P4-10 | Stock Research Agent + prompt                                                                                              | P4-4~P4-9    | ⬜   | 对 4 个样例股票问题产出完整 finding |
| P4-11 | 多标的对比支持：Plan 层生成依赖任务 + 报告对比表格                                                                         | P4-10, P1-10 | ⬜   | 「比较 NVDA/AMD/AVGO 基本面」可用   |

**阶段验收**：「NVDA 是做什么的」「分析 NVDA 最近一季财报」「NVDA 估值贵不贵」「比较 NVDA、AMD、AVGO」四个问题都能产出带财务数据与 SEC 引用的报告。

---

## Phase 5 — Multi-Agent 完整编排

**目标**：把 6 个 Agent 完整串成 `[DP §7.1]` 的流程，并验证多模型兼容性。

| ID   | 任务                                                                                                       | 依赖              | 状态 | 验收                                        |
| ---- | ---------------------------------------------------------------------------------------------------------- | ----------------- | ---- | ------------------------------------------- |
| P5-1 | 意图分类层（词典/正则短路 + FAST 模型兜底）`[DP §25.1]`                                                    | P1-9              | ⬜   | 常见问题不走 LLM 也能正确分类               |
| P5-2 | Agents-as-Tools 装配：Manager 通过工具调用子 Agent，结果回编排层 `[DP 决策 B]`                             | P3-9, P4-10, P2-6 | ⬜   | 单次研究可同时用到 crypto + web             |
| P5-3 | 完整并行 fan-out：依赖分层 + `asyncio.gather` + 单任务超时 + 部分失败降级                                  | P5-2              | ⬜   | 1 个任务失败不影响整体出报告                |
| P5-4 | Merge & Dedup 阶段：source 归一、claim 合并、冲突汇总                                                      | P5-3, P3-10       | ⬜   | 跨 Agent 的重复来源被合并                   |
| P5-5 | Fact Checker Agent（干净上下文，只看 claims + sources，可复检索）+ 校验事件流                              | P5-4              | ⬜   | 能识别注入的错误声明                        |
| P5-6 | Gap Check + 最多 1 轮补充研究（`PLAN_UPDATED`）                                                            | P5-5              | ⬜   | 缺关键数据时能补一轮                        |
| P5-7 | Report Writer v2：动态 `report_sections`、Executive Summary、Bull/Bear Case、Risks、数据限制章节、免责声明 | P5-6, P2-8        | ⬜   | 不同问题类型报告结构不同                    |
| P5-8 | 全流程 workflow 测试（ScriptedModel）：正常 / 工具失败 / 超时 / 冲突 / 引用缺失 / schema 解析失败 6 条路径 | P5-7, P1-8        | ⬜   | 全部确定性通过                              |
| P5-9 | 多模型冒烟：6 个 provider 各跑一次完整研究，结果写入 catalog `verified` 字段 + `[DP §9.4]` 降级验证        | P5-8, P1-4        | ⬜   | 已配 key 的 provider 全部跑通或明确标注限制 |

### P5.5 — MVP 验收 ⬜

按 `[DP §21.1]` 的 Definition of Done 逐条核对，并跑完 `[DP §26]` 收尾清单。**通过后打 tag `v0.1.0-mvp`。**

---

## Phase 6 — 高级 UX + 历史 + 可观察性

| ID    | 任务                                                                                                 | 依赖 | 状态 | 验收                            |
| ----- | ---------------------------------------------------------------------------------------------------- | ---- | ---- | ------------------------------- |
| P6-1  | Activity Panel 完善：tool 详情展开、耗时、缓存标记、自动折叠、失败保持展开                           | P5.5 | ⬜   | 符合 `[DP §13.2]`               |
| P6-2  | Research Timeline / 阶段瀑布可视化                                                                   | P6-1 | ⬜   | 能看出各阶段耗时                |
| P6-3  | 无障碍与状态播报（`aria-live`，不只依赖颜色）                                                        | P6-1 | ⬜   | 键盘可完整操作                  |
| P6-4  | History 列表页 + 过滤（类型/模型/状态）+ 分页                                                        | P5.5 | ⬜   | 能翻看历史研究                  |
| P6-5  | Session 详情页与实时页**复用同一组件**（事件源切换为 DB 回放）`[DP §14.3]`                           | P6-4 | ⬜   | 两种视图渲染一致                |
| P6-6  | `GET /api/research/{id}/events?after={seq}` 重连回放（偿还 D4）                                      | P6-5 | ⬜   | 刷新页面能续上进行中的研究      |
| P6-7  | 取消研究（前端按钮 → Next → Python cancel → 状态落库）                                               | P6-6 | ⬜   | 可中断长任务                    |
| P6-8  | Settings 页：默认模型、按角色指定模型、执行上限、报告偏好                                            | P5.5 | ⬜   | 设置持久化并生效                |
| P6-9  | `/debug` 可观察性页：阶段瀑布 / Agent 排行 / Tool 失败率 / 模型成本对比 / Provider 配额 `[DP §20.2]` | P5.5 | ⬜   | 能回答 `[DP §20.2]` 的 4 个问题 |
| P6-10 | 成本与 token 实时显示 + 单 session 成本上限告警                                                      | P6-9 | ⬜   | 超限发 WARNING 事件             |
| P6-11 | Playwright E2E（打桩 Python 服务，跑完整 UI 流程）                                                   | P6-5 | ⬜   | CI 中稳定通过                   |

---

## Phase 7 — Evaluation 系统

| ID   | 任务                                                                                            | 依赖      | 状态 | 验收                       |
| ---- | ----------------------------------------------------------------------------------------------- | --------- | ---- | -------------------------- |
| P7-1 | `evals/` 骨架 + `runner.py` + 结果归档格式                                                      | P5.5      | ⬜   | 可跑并输出 JSON + Markdown |
| P7-2 | `deterministic.py` grader：路由 / tool 选择 / 引用覆盖率 / 引用有效性 / 报告完整性 / 数值准确性 | P7-1      | ⬜   | 指标可复现                 |
| P7-3 | `llm_judge.py` grader：幻觉率 / 认知类型正确率                                                  | P7-1      | ⬜   | 与人工抽检一致率 >80%      |
| P7-4 | 数据集：`intent_routing` + `tool_selection`                                                     | P7-2      | ⬜   | 各 ≥30 条                  |
| P7-5 | 数据集：`crypto_project` + `stock_analysis` + `financial_report`                                | P7-2      | ⬜   | 各 ≥15 条，带真值          |
| P7-6 | 数据集：`fact_check`（注入错误声明）+ `epistemic_labeling` + prompt injection 用例              | P7-3      | ⬜   | 各 ≥20 条                  |
| P7-7 | Fixture 化外部数据（eval 结果可跨时间比较）                                                     | P7-5      | ⬜   | 同一数据集重复跑分数稳定   |
| P7-8 | 跨模型 eval 对比报告（回答"哪个模型效果最好"）                                                  | P7-1~P7-7 | ⬜   | 生成对比表并归档           |

---

## Phase 8 — 扩展能力（按需触发，非线性）

| ID   | 任务                                                                                                               | 触发条件                                                  | 状态 |
| ---- | ------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- | ---- |
| P8-1 | Watchlist + 资产详情页                                                                                             | 反复手动查同一批标的                                      | ⬜   |
| P8-2 | 宏观模块（Fed / CPI / PCE / NFP / GDP / 国债收益率 / DXY / VIX，FRED API）+ Macro→Liquidity→Stocks→Crypto 联动分析 | 需要跨市场问题（如"为什么 BTC 和纳斯达克同时涨"）深度支持 | ⬜   |
| P8-3 | PostgreSQL 迁移                                                                                                    | 需要 pgvector 或远程部署                                  | ⬜   |
| P8-4 | pgvector + RAG 个人知识库（PDF / 研报 / 笔记）                                                                     | 需要存本地文档                                            | ⬜   |
| P8-5 | 定时研究 + Alerts（价格 / 财报 / 解锁 / 巨鲸 / TVL），需任务队列 + Redis                                           | 需要"每天早上给我简报"                                    | ⬜   |
| P8-6 | MCP Server（把自身 tools 暴露给 Claude / Cursor）                                                                  | 想在别处复用这些工具                                      | ⬜   |
| P8-7 | LangGraph 迁移评估与实施                                                                                           | 满足 `[DP §4.1]` 决策清单任意两条                         | ⬜   |
| P8-8 | 报告导出（PDF / Notion）+ 多轮追问对话                                                                             | 按需                                                      | ⬜   |

---

## 阶段小结

> 每个阶段完成后在此追加小结：完成内容、遇到的问题、偏离计划之处、新增技术债、eval 分数变化。

### 开发计划确认 ✅（2026-09-07）

- 完成 `DEVELOPMENT_PLAN.md` v1.1 与本 Roadmap。
- 确认：计划通过、Agent 减至 6 个、项目名沿用。
- 模型选型经过两次修正，最终结论为**开发期国内模型 + 上线后切 OpenAI**（初版按"仅国内模型"设计 → 改为 OpenAI 优先 → 因开发期预算偏高改回国内模型）。三个要点：
  1. **DeepSeek V4 Pro 闲时 ¥4.5/¥13.5 是性价比最优的"可用档"**，单次研究约 ¥0.69，是 OpenAI 配置的 1/5。DeepSeek 为分时计价（北京时间 09–12、14–18 高峰，其余减半），Phase 5 批量 eval 应安排夜间跑。
  2. **OpenAI key 仍充 $5，但只用于 tracing**：SDK 对非 OpenAI 模型的 trace 上传免费，$5 买回整个 LLM 层调试面且额度不浪费。若不充，自建埋点成为唯一调试面，P1-13 需进一步提前。
  3. **新增 `[DP §9.8]` 缓存约束**：缓存命中价仅为未命中的 3%，比分时折扣重要得多。要求 system prompt 字节级稳定（严禁插入当前时间 / session_id），易变内容放消息末尾——这条容易在写 prompt 时无意破坏，需在 review 时专门检查。
- 实测核实的两点（影响设计）：
  - OpenAI 全系与 Kimi K3 支持 `json_schema` + `strict`，DeepSeek/GLM 仅 `json_object` → 结构化输出做三路径（P1-4b），schema 保持扁平。
  - **DeepSeek 实测结论（2026-09-07，真实 key）**：`response_format=json_schema` 被 **HTTP 400** 拒绝（`"This response_format type is unavailable now"`），只支持 `json_object` → 目录里保守标注 `json_mode` 得到证实。三个样例问题走 json_mode 路径**全部一次通过、零重试**，6360 字符的内嵌 schema 模型能正确消化。附带三点：
    1. **缓存策略验证有效**：首个问题 `cached_tokens=0`，后两个均为 1664/1725 ≈ **96% 命中**。§9.8 要求的「schema 后缀追加在末尾、前缀字节级稳定」确实拿到了 3% 的缓存价。
    2. **planner 延迟是真实风险**：单次 17–60s，与输出 token 量（1.1k–4k）正相关，因为 v4-pro 是推理模型。60s 会吃掉 `TOTAL_TIMEOUT_S=420` 的 14%，P1-9 需评估 planner 换 flash 或限制推理长度。→ P1-9 已实测，**换 flash 无效**，见下。
    3. **推理模型的空输出陷阱**：接口返回 200 但 `content` 为空。当时归因为「`reasoning_content` 与正式输出共享 `max_tokens`，预算耗尽」，据此设计为**不重试**。→ P1-9 否证了这个归因，改为重试，见下。
  - **SDK 的 `output_type` 与 `json_mode` 互斥**（读 `chatcmpl_converter.convert_response_format()` 得知）：只要设了 `output_type`，SDK 就无条件发送 `response_format={"type":"json_schema"}`，`is_strict_json_schema()` 只能切换其中的 `strict` 标志，无法降级为 `json_object`。因此 `json_mode` / `prompt_only` 两条路径必须**不设 `output_type`**，改由我们自己内嵌 schema、解析文本、带错误重试。这是 P1-4b 的核心约束，也是它不能简单委托给 SDK 的原因。
  - SDK tracing 依赖 `OPENAI_API_KEY` 上传，且**独立于业务模型**：有 OpenAI key 后，用国内模型的 run 也能上传 trace。
- 当前 OpenAI 阵容（2026-09 核实）：`gpt-6-astra` $10/$50（1.05M ctx）、`gpt-5.6-sol` $4/$20、`terra` $2/$12、`luna` $0.20/$1.20。
- 仍待确认：`[DP §27.2]` Q2、Q5、Q6、Q7（有默认方案，不阻塞）。

### Phase 0 — 项目初始化 ✅（2026-09-07）

**完成内容**：pnpm workspace（web + shared + Python 服务）、双服务启动脚本、10 张表的数据库 schema 与 migration、健康检查贯通、CI 三 job、secret 扫描钩子、类型生成链路。

**检查结果**：Python 侧 ruff/format/basedpyright 全绿 + 7 个测试通过；前端 tsc/eslint/prettier 全绿 + 13 个测试通过；`next build` 通过；`drizzle-kit check` 通过。

**实际落地的版本**（与计划一致，型号按实测确定）：

| 组件                        | 版本                        |
| --------------------------- | --------------------------- |
| openai-agents               | 0.22.0（已锁 minor，见 R8） |
| Python / FastAPI / Pydantic | 3.13.15 / 0.141.1 / 2.13.5  |
| Next.js / React             | 16.3.4 / 19.2.8             |
| TypeScript                  | **6.0.3**（见下方 D13）     |
| Drizzle ORM / drizzle-kit   | 0.45.2 / 0.31.10            |
| Tailwind CSS                | 4.3.3                       |

**遇到的问题与处理**：

1. **TypeScript 7 与 typescript-eslint 不兼容**：TS 7（Go 原生编译器）下 `tsc` 正常但 ESLint 直接崩溃（typescript-eslint 尚未支持）。降到 TS 6.0.3 → 记为技术债 D13。
2. **eslint-plugin-react 在 ESLint 10 下崩溃**：`detectReactVersion` 依赖已移除的 `context.getFilename`。显式声明 `settings.react.version` 绕过自动探测，无需降级 ESLint。
3. **eslint-config-next 16 已原生支持 flat config**：去掉了 `FlatCompat` 兼容层。
4. **`no-restricted-imports` 规则写得过宽**：无法从导入路径区分服务端/客户端文件，把合法调用也拦了。收窄到只管 `components/**`；真正的防线是 `import "server-only"`（客户端引入会构建失败）。
5. **构建期数据库副作用**：`db/client.ts` 模块级建连接会让 `next build` 创建数据库文件。改为 `getDb()` 懒初始化。
6. **相对路径数据库位置歧义**：drizzle-kit 从 `apps/web` 运行、Python 从仓库根运行，同一个 `./data/app.db` 会指向两个文件。统一用 `findRepoRoot()`（查找 `pnpm-workspace.yaml`）作为基准。
7. **`wait -n` 在 macOS bash 3.2 上不支持**：`dev.sh` 改用可移植的轮询监控循环。
8. **structlog 中文被转义**：`JSONRenderer(ensure_ascii=False)`，并补了测试防回归。
9. **pnpm 12 配置项变更**：构建脚本白名单从 `onlyBuiltDependencies` 改为 `allowBuilds` 映射。
10. **慢网络下大二进制包超时**：`.npmrc` 放宽 fetch 重试与超时（`@next/swc` 约 31MB）。

**新增技术债**：D13（TS 6 降级）、D14（CI 远端未验证）、D15（shadcn/ui 未初始化）。

### P1-9 — Research Manager（planner）✅（2026-09-07）

**完成内容**：`prompts/`（模板加载 + 占位符校验）、`prompts/research_manager.md`、`agents/research_manager.py`、`orchestrator/plan_validation.py`、`orchestrator/planner.py`、`scripts/probe_planner.py`（取代 `probe_deepseek.py`）。离线测试 29 个。

**验收结果**（真实 DeepSeek key，2026-09-07 高峰时段）：v4-pro 对三个样例问题 **3/3 产出合法计划、零修复**，中位延迟 42s，均成本 $0.010。`report_sections` 确实随问题类型变化（对比类问题精确产出 `Executive Summary / Comparison / Key Differences / Conclusion`），实体解析（英伟达→NVDA）与假设披露都符合预期。

**两个被实测否证的先前判断**：

1. **「planner 换 v4-flash 提速」不成立。** flash 在规划任务上输出 4.3k–4.7k token，而 v4-pro 只要 2.1k–2.7k；推理模型的延迟由输出量决定，于是 flash 中位延迟 42s **反而略高于** pro 的 38s，标称 1/3 的价格优势也只剩 1.6 倍。更糟的是稳定性：flash 3 个问题只成功 2 个，同一问题连跑 5 次有 1 次返回空 `content`，延迟方差 14–71s。**结论：PLANNER 保留 v4-pro。**
2. **空输出不是 `max_tokens` 被推理耗尽。** P1-4b 时据此把 `EmptyOutputError` 设计成不重试。实测否证：把出错的那次请求原样直接发给接口，正常返回（`finish_reason=stop`，reasoning 仅用 2126 token，远未打满 384k 预算）；且它是偶发的（5 次 1 次）。既然规划失败会带走整个会话（§7.2），为一次偶发空响应放弃会话不值得 → **改为可重试**，但重试时**重发原请求而不回喂历史**（空输出没有可纠正的信息，且空 assistant 消息有被 provider 拒绝的风险，重发还能保住 prompt 缓存命中）。

**另一个观察到的失败模式**：模型偶尔把内嵌的 JSON Schema **本身**当输出返回（`{"properties": {...}}`）。P1-4b 的「回喂字段级错误 + 重试」机制正确救回，这是该机制第一次在真实模型上被触发。

**设计取舍：计划校验以修复为主而非拒绝。** §7.2 规定 planner 失败即整体失败，而「引用了不存在的任务 id」这类笔误对研究结果影响微乎其微。因此只有**无法安全修复**的才拒绝（任务列表为空；任务 id 重复——`depends_on` 指向哪一个无从判断），其余修复并记录：超限按 `priority` 降序截断、丢弃悬空/自引用依赖、按「只保留指向前方的边」确定性地打断环。修复记录以 `warning` 事件暴露给用户——静默修复等于让用户看到一份悄悄缩水的报告。截断必须先于依赖清理，否则会留下指向已删除任务的依赖，执行器分层时永远等不到它（已加回归测试）。

### P1-10 — Orchestrator 骨架 ✅（2026-09-07）

**完成内容**：`orchestrator/state.py`（运行时账本 + 阶段迁移 + 用量累加）、`orchestrator/executor.py`（分层 fan-out + 并发/超时/预算三道护栏 + 失败降级）、`orchestrator/pipeline.py`（会话全流程与终态事件）、`observability/cost.py`。测试 +40（执行器 14、流程 12、状态与成本 14），累计 229。

**三个设计决策**：

1. **依赖失败时，依赖方照常执行而不是跳过。** 跳过会把一次失败放大成整条依赖链的失败，与 §7.2「不中断整个流程」相悖；而 prompt 要求每个 objective 自包含，多数任务缺了上游数据仍能完成大部分工作（「对比 A 和 B」里 A 挂了，B 的数据照样有价值）。代价是产出有缺口，所以执行器会把缺失的上游任务 id 写进该任务的 `data_gaps` —— 不披露比缺数据更糟，读者会以为那一节是在完整信息下得出的。`SKIPPED` 保留给预算/时间耗尽的情形。
2. **预算与时间的护栏放在层边界，不放在任务边界。** 同层任务已经并发出去了，中途叫停只会得到一堆半成品。时间预算的算法是 `min(task_timeout_s, 整体剩余)`：不减去已用时间的话，最后一个任务能把报告撰写的时间全吃掉。执行阶段的额度由 pipeline 从 `total_timeout_s` 里划出并传入，Phase 5 想为报告预留时间时改的是流程编排而不是执行器。
3. **`CancelledError` 必须穿透降级逻辑。** 它是 `BaseException` 而非 `Exception` 的子类，所以 `except Exception` 天然不会误捕；但仍需显式 `except asyncio.CancelledError: raise`（放在 `except TimeoutError` 之后），否则任务会被标成失败而不是跳过。吞掉它的后果是整体超时与用户取消双双失效——会话再也停不下来。测试专门覆盖了这条路径。

**pipeline 的唯一硬性契约是「终态事件恰好一个」**：正常完成、规划失败、被取消、未预期异常四条路径都必须发出且只发出一个终态事件。漏发一次，用户看到的就是永远转圈的进度条，比报错更糟。因此 `run_research` 不抛业务异常（失败通过事件与 `outcome.succeeded` 表达）——否则 SSE 端点要在事件流和异常两处处理失败，两套通路迟早不一致。

**顺带修正的一处算错**：`cost.py` 与相关注释原本写「96% 缓存命中率下混算会高估 20 倍以上」。实测数字是 **约 14 倍**——30 倍的价差是 100% 命中时的上限，96% 命中只拿到其中一部分。已在代码与测试里改为真实数字。

**成本核算已在真实模型上验证**：v4-pro 三个问题的 `cached_tokens=2304` 均被正确识别，闲时时段自动套用半价（$0.0046/次，对比高峰 $0.0101）。`to_token_usage` 里 `getattr` 的防御式取值也顺带确认了不是白写——DeepSeek 的 `input_tokens` 确实包含缓存部分。

---

## 技术债跟踪

`[DP §24]` 登记的债务在此跟踪偿还状态：

| ID  | 债务                                                                                                                                                                                        | 偿还阶段                           | 状态 |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | ---- |
| D1  | 无用户系统                                                                                                                                                                                  | 按需                               | ⬜   |
| D2  | SQLite 无备份                                                                                                                                                                               | P8-3                               | ⬜   |
| D3  | 进程重启丢失进行中任务                                                                                                                                                                      | P8-7                               | ⬜   |
| D4  | 断线不可恢复进行中的 run                                                                                                                                                                    | P6-6                               | ⬜   |
| D5  | 缓存无主动失效                                                                                                                                                                              | 按需                               | ⬜   |
| D6  | Fact Check 非全量                                                                                                                                                                           | 按需                               | ⬜   |
| D7  | `data_gaps` 依赖 LLM 自觉                                                                                                                                                                   | P7 持续监控                        | ⬜   |
| D8  | 补充研究仅 1 轮                                                                                                                                                                             | 按需                               | ⬜   |
| D9  | 前端无虚拟滚动                                                                                                                                                                              | 按需                               | ⬜   |
| D10 | Prompt 无版本管理                                                                                                                                                                           | P7                                 | ⬜   |
| D11 | 报告仅中文                                                                                                                                                                                  | 按需                               | ⬜   |
| D12 | 无跨任务 tool 配额协调                                                                                                                                                                      | 按需                               | ⬜   |
| D13 | **TypeScript 固定在 6.x**：TS 7 下 typescript-eslint 崩溃（[typescript-eslint#10940](https://github.com/typescript-eslint/typescript-eslint/issues/10940)）。代价仅是编译慢一些，功能无影响 | typescript-eslint 支持 TS 7 后升级 | ⬜   |
| D14 | CI 仅验证了本地等价命令，GitHub Actions 未实际跑过                                                                                                                                          | 首次 push 后                       | ⬜   |
| D15 | shadcn/ui 未初始化（Phase 0 无组件需求，避免空目录）                                                                                                                                        | P1-12 需要组件时                   | ⬜   |

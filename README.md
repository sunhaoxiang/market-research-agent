# Market Research Agent

个人使用的 **AI Financial Research Platform**：用自然语言提问，由多个 Agent 协作完成 Crypto 与美股研究，产出带来源引用的结构化研究报告。

```text
用户问题 → Research Plan → 多 Agent 并行研究 → 事实核查 → 带引用的研究报告
```

- 设计文档：[`docs/DEVELOPMENT_PLAN.md`](docs/DEVELOPMENT_PLAN.md)
- 开发进度：[`docs/ROADMAP.md`](docs/ROADMAP.md)
- **当前状态：MVP（Phase 0–5）已验收**，tag `v0.1.0-mvp`。本机只有 DeepSeek key。P6 已改成报告居中 + 历史 / 续订 / 取消。限制见 [`docs/ROADMAP.md`](docs/ROADMAP.md) 的 P5.5。

---

## 架构

```text
Browser
   ↓  fetch + SSE
Next.js (apps/web)          ← UI · BFF · 数据库唯一 writer
   ↓  HTTP + SSE (127.0.0.1)
Python Agent Service (services/agent)   ← 无状态研究引擎
   ↓
Agents → Tools → 外部 API（LLM / CoinGecko / DefiLlama / FMP / SEC / Tavily）
```

三条核心架构决策见设计文档：编排层是确定性 Python 代码而非 LLM 自主循环；子 Agent 用 Agents-as-Tools 而非 Handoff；Next.js 是数据库的唯一 writer。

## 技术栈

| 层    | 选择                                                             |
| ----- | ---------------------------------------------------------------- |
| Agent | Python 3.13 · OpenAI Agents SDK · FastAPI · Pydantic v2 · uv     |
| 前端  | Next.js 16（App Router）· React 19 · TypeScript · Tailwind CSS 4 |
| 数据  | SQLite · Drizzle ORM                                             |
| 工程  | pnpm workspace · Ruff · basedpyright · pytest · Vitest · ESLint  |

---

## 环境要求

| 工具     | 版本    | 说明                               |
| -------- | ------- | ---------------------------------- |
| Python   | 3.13.15 | 由 pyenv 管理（`.python-version`） |
| uv       | ≥ 0.9   | 管理 Python 依赖与虚拟环境         |
| Node.js  | 24.20.0 | 由 nvm 管理（`.nvmrc`）            |
| pnpm     | 12.3.4  |                                    |
| gitleaks | 可选    | pre-commit 的 secret 扫描          |

## 快速开始

```bash
# 1. 配置环境变量（至少填一个 LLM provider key）
cp .env.example .env.local

# 2. 安装依赖
pnpm install
(cd services/agent && uv sync)

# 3. 初始化数据库
pnpm db:migrate

# 4. 同时启动两个服务
pnpm dev
```

- Web：http://localhost:3000
- Agent Service：http://127.0.0.1:8000（文档 `/docs`）
- 健康检查：http://localhost:3000/api/health

> 数据库文件位于仓库根的 `data/app.db`。`DATABASE_URL` 中的相对路径一律按**仓库根**解析，与运行目录无关。

## 常用命令

```bash
pnpm dev              # 同时启动 web + agent
pnpm dev:web          # 仅 Next.js
pnpm dev:agent        # 仅 Agent Service

pnpm check            # 全套检查（下列各项）
pnpm lint             # ESLint
pnpm typecheck        # tsc --noEmit
pnpm test             # Vitest
pnpm py:lint          # Ruff
pnpm py:typecheck     # basedpyright
pnpm py:test          # pytest（跳过 live 标记）

pnpm db:generate      # 从 schema 生成 migration
pnpm db:migrate       # 执行 migration
pnpm db:check         # 校验 migration 一致性
pnpm db:studio        # Drizzle Studio

pnpm gen:types        # Pydantic → JSON Schema → TypeScript
```

`pytest -m live` 会调用真实外部 API 与 LLM（产生费用），默认跳过。

## 目录结构

```text
apps/web/              Next.js：UI · Route Handlers · Drizzle schema 与查询
services/agent/        Python：FastAPI · Agents · Tools · Model Registry
packages/shared/       从 Pydantic 生成的 TypeScript 类型（勿手改）
docs/                  设计文档与开发计划
scripts/               dev.sh · gen-types.sh
```

## 约定

- **API key 只在服务端。** 不使用 `NEXT_PUBLIC_` 承载任何 secret；所有外部请求由 Python 服务发起。
- **Pydantic 是跨语言类型的唯一真源。** 改了事件协议等模型后运行 `pnpm gen:types` 并提交生成结果，CI 会检查漂移。
- **数据库 schema 保持可迁移。** 主键用 UUIDv7 文本、时间用 Unix 毫秒整数、JSON 用文本列，不依赖 SQLite 特有能力（详见设计文档 §10.1）。
- **关键结论必须有来源，事实与推测必须分离**（§15）。

## 免责声明

本项目输出的内容仅供个人研究参考，不构成投资建议。

## 许可

私有项目，暂未授权。

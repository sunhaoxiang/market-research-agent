#!/usr/bin/env bash
#
# Pydantic → JSON Schema → TypeScript
#
# Pydantic 模型是跨语言类型的唯一真源（DEVELOPMENT_PLAN.md §5.1）。
# CI 会在跑完此脚本后执行 `git diff --exit-code`，防止两侧类型漂移。
#
# 用法：pnpm gen:types
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/packages/shared/src/generated"

cd "$REPO_ROOT/services/agent"

mkdir -p "$OUT_DIR"

# Phase 1 会在 agent_service.schemas 中定义事件协议等模型，并在此导出 JSON Schema。
# Phase 0 仅打通链路：若尚无可导出的模型，生成占位文件并退出。
if ! uv run python -c "import agent_service.schemas" 2>/dev/null; then
  echo "→ agent_service.schemas 尚未创建（Phase 1 任务），写入占位类型"
  cat >"$OUT_DIR/events.ts" <<'EOF'
/**
 * 由 scripts/gen-types.sh 生成，请勿手改。
 *
 * 占位文件：事件协议的 Pydantic 模型将在 Phase 1（任务 P1-1）定义，
 * 之后此文件会被真实生成的类型替换。
 */

export type PlaceholderGeneratedTypes = never;
EOF
  echo "✓ 已写入占位类型：$OUT_DIR/events.ts"
  exit 0
fi

echo "→ 从 Pydantic 导出 JSON Schema"
uv run python -m agent_service.schemas.export --out "$OUT_DIR/schema.json"

echo "→ JSON Schema 转 TypeScript"
cd "$REPO_ROOT"
pnpm exec json-schema-to-typescript \
  --input "$OUT_DIR/schema.json" \
  --output "$OUT_DIR/events.ts" \
  --bannerComment "/** 由 scripts/gen-types.sh 生成，请勿手改。真源：services/agent 的 Pydantic 模型 */"

pnpm exec prettier --write "$OUT_DIR/events.ts" >/dev/null
echo "✓ 类型已生成：$OUT_DIR/events.ts"

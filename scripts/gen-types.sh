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
SCHEMA_JSON="$OUT_DIR/schema.json"
OUT_TS="$OUT_DIR/types.ts"

mkdir -p "$OUT_DIR"

echo "→ 从 Pydantic 导出 JSON Schema"
(cd "$REPO_ROOT/services/agent" && uv run python -m agent_service.schemas.export --out "$SCHEMA_JSON")

echo "→ JSON Schema 转 TypeScript"
cd "$REPO_ROOT"
pnpm exec json2ts \
  --input "$SCHEMA_JSON" \
  --output "$OUT_TS" \
  --additionalProperties false \
  --enableConstEnums false \
  --unknownAny false \
  --bannerComment "/* eslint-disable */
/**
 * 由 scripts/gen-types.sh 生成，请勿手改。
 * 真源：services/agent/src/agent_service/schemas/
 * 修改模型后运行 \`pnpm gen:types\` 重新生成并提交。
 */"

pnpm exec prettier --write "$OUT_TS" "$SCHEMA_JSON" >/dev/null
echo "✓ 类型已生成：$OUT_TS"

#!/usr/bin/env bash
#
# 同时启动 Next.js（:3000）与 Python Agent Service（:8000）。
# 任一进程退出时，一并关闭另一个，避免留下孤儿进程。
#
# 用法：pnpm dev
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env.local && ! -f .env ]]; then
  echo "⚠️  未找到 .env.local。请先执行：cp .env.example .env.local 并填入 API key"
  echo ""
fi

pids=()

cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "→ 启动 Agent Service  http://127.0.0.1:8000"
(
  cd services/agent
  exec uv run uvicorn agent_service.main:app \
    --reload --host 127.0.0.1 --port 8000 \
    --reload-dir src
) &
pids+=($!)

echo "→ 启动 Next.js         http://localhost:3000"
pnpm --filter @mra/web dev &
pids+=($!)

echo ""
echo "两个服务已启动。健康检查：http://localhost:3000/api/health"
echo "按 Ctrl-C 停止。"

# 任一子进程退出即整体退出（由 cleanup 收尾）。
# 用轮询而非 `wait -n`：后者在 macOS 自带的 bash 3.2 上不支持。
while :; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo ""
      echo "✖ 进程 $pid 已退出，正在停止其余服务"
      exit 1
    fi
  done
  sleep 1
done

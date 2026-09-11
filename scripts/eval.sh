#!/usr/bin/env bash
#
# 跑 evals/runner.py（[DP §19]）。
#
# 用法：
#   ./scripts/eval.sh --suite smoke
#   ./scripts/eval.sh --list
#   pnpm eval -- --suite smoke
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$REPO_ROOT/services/agent"
exec uv run python -m evals "$@"

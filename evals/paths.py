"""仓库内 evals 目录约定。"""

from __future__ import annotations

from pathlib import Path

# evals/paths.py → 上溯一层即仓库根
REPO_ROOT = Path(__file__).resolve().parents[1]

EVALS_DIR = REPO_ROOT / "evals"
DATASETS_DIR = EVALS_DIR / "datasets"
RESULTS_DIR = EVALS_DIR / "results"
LOCAL_RESULTS_DIR = RESULTS_DIR / "local"

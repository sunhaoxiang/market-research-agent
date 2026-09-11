"""读取 `evals/datasets/*.jsonl`。"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from evals.schemas import EvalCase


class DatasetError(ValueError):
    """某一行不是合法用例。"""


def list_dataset_files(datasets_dir: Path) -> list[Path]:
    return sorted(path for path in datasets_dir.glob("*.jsonl") if path.is_file())


def load_jsonl(path: Path) -> list[EvalCase]:
    """按文件顺序加载。空行跳过；`suite` 必须等于文件名（不含后缀）。"""

    suite = path.stem
    cases: list[EvalCase] = []
    seen: set[str] = set()
    text = path.read_text(encoding="utf-8")
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
            case = EvalCase.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DatasetError(f"{path}:{line_no}: {exc}") from exc
        if case.suite != suite:
            raise DatasetError(f"{path}:{line_no}: suite={case.suite!r} 与文件名 {suite!r} 不一致")
        if case.id in seen:
            raise DatasetError(f"{path}:{line_no}: 重复 id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return cases

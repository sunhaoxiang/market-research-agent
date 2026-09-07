"""把 Pydantic 模型导出为 JSON Schema，供 scripts/gen-types.sh 生成 TS 类型。

用法：
    uv run python -m agent_service.schemas.export --out <path>

导出策略：把所有需要跨语言共享的模型收进一个 root schema 的 `$defs`，
再由 json-schema-to-typescript 一次性生成。这样共享的嵌套类型（Entity、Source…）
只会生成一份，不会出现 Entity1 / Entity2 这类重复定义。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import RootModel, TypeAdapter, create_model

from agent_service.schemas import (
    AgentFinding,
    Claim,
    ClaimDraft,
    ClaimVerification,
    Conflict,
    DataProvenance,
    DataQuality,
    Entity,
    ErrorInfo,
    FactCheckResult,
    MetricPoint,
    ReportMetadata,
    ReportSection,
    ResearchEvent,
    ResearchFinding,
    ResearchPlan,
    ResearchReport,
    ResearchTask,
    Source,
    TokenUsage,
    ToolError,
)

_ResearchEventRoot = RootModel[ResearchEvent]
# 让判别联合在 JSON Schema 中获得独立的 $defs 条目。
# 不这样做的话它会被内联到 root property，json2ts 会据属性名派生出 `Researchevent`
# 这种错误大小写的类型名。
_ResearchEventRoot.__name__ = "ResearchEvent"

# 需要在前端使用的类型。事件协议是主角，其余是它引用到的与页面直接用到的模型。
EXPORTED_TYPES: dict[str, Any] = {
    "ResearchEvent": _ResearchEventRoot,
    "ResearchPlan": ResearchPlan,
    "ResearchTask": ResearchTask,
    "Entity": Entity,
    "MetricPoint": MetricPoint,
    "Source": Source,
    "Claim": Claim,
    "ClaimDraft": ClaimDraft,
    "ClaimVerification": ClaimVerification,
    "Conflict": Conflict,
    "AgentFinding": AgentFinding,
    "ResearchFinding": ResearchFinding,
    "FactCheckResult": FactCheckResult,
    "ResearchReport": ResearchReport,
    "ReportSection": ReportSection,
    "ReportMetadata": ReportMetadata,
    "ToolError": ToolError,
    "DataProvenance": DataProvenance,
    "DataQuality": DataQuality,
    "ErrorInfo": ErrorInfo,
    "TokenUsage": TokenUsage,
}


def _mark_all_properties_required(schema: dict[str, Any]) -> None:
    """把每个对象 schema 的全部属性标记为 required（就地修改）。

    为什么必须这样做：Pydantic 把「有默认值」等同于「JSON Schema 非必填」，
    但序列化时这些字段**总是**会出现在输出里（`message: str | None = None`
    在线上就是显式的 `"message": null`）。若照搬到 TS，会得到两个后果：

      1. `type?: "tool_started"` 这样的可选判别字段会让
         `Extract<ResearchEvent, { type: T }>` 退化成 `never`，判别联合失效；
      2. 所有字段都变成 `T | undefined`，消费方要写一堆无意义的空值判断。

    因为 TS 侧只消费事件（不构造），全部标 required 才是对线上格式的准确描述。
    """
    if schema.get("type") == "object" and "properties" in schema:
        schema["required"] = list(schema["properties"])
    for value in schema.values():
        if isinstance(value, dict):
            _mark_all_properties_required(value)  # pyright: ignore[reportUnknownArgumentType]
        elif isinstance(value, list):
            for item in value:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(item, dict):
                    _mark_all_properties_required(item)  # pyright: ignore[reportUnknownArgumentType]


def build_schema() -> dict[str, Any]:
    """构造包含全部导出类型的单一 JSON Schema。"""
    root = create_model(  # pyright: ignore[reportCallIssue]
        "MraSharedTypes",
        **{name: (tp, ...) for name, tp in EXPORTED_TYPES.items()},  # pyright: ignore[reportArgumentType]
    )
    schema = TypeAdapter(root).json_schema(ref_template="#/$defs/{model}")
    _mark_all_properties_required(schema)
    schema["title"] = "MraSharedTypes"
    schema["description"] = (
        "由 services/agent 的 Pydantic 模型生成，请勿手改。"
        "真源见 agent_service/schemas/，改动后运行 pnpm gen:types。"
    )
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="输出 JSON Schema 的路径")
    args = parser.parse_args()

    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_schema(), indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"✓ 已导出 {len(EXPORTED_TYPES)} 个类型 → {out}")


if __name__ == "__main__":
    main()

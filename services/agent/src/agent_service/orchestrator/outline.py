"""报告章节大纲（P5-7）。

Writer 只填正文；章节 id、数据限制和免责声明由代码定。Planner 的
`report_sections` 可以少写或多写，这里按问题类型补齐必有章、去掉 Executive
Summary（那是 `executive_summary` 字段），并在末尾固定追加数据限制与免责声明。

不同问题类型必须走出不同结构：深研带看多/看空，对比带 Comparison，
「为什么今天涨」走催化剂精简章。不要让模型自己发明章节名。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum

from agent_service.orchestrator.comparison import COMPARISON_SECTION_ID
from agent_service.schemas.common import QuestionType
from agent_service.schemas.report import ReportSection, ResearchReport

EXECUTIVE_SUMMARY_ID = "Executive Summary"
BULL_BEAR_ID = "Bull/Bear Case"
DATA_LIMITATIONS_ID = "Data Limitations"
DISCLAIMER_ID = "Disclaimer"

DISCLAIMER_MARKDOWN = (
    "本报告由自动化研究系统根据公开信息生成，仅供参考，"
    "不构成投资建议，也不构成买入、卖出或持有任何证券或加密资产的推荐。"
    "信息可能不完整、滞后或有误。投资有风险，请独立判断并自行承担决策后果。"
)
MISSING_SECTION_MARKDOWN = "本节暂无足够材料，详见数据限制。"
NO_GAPS_MARKDOWN = "本次研究未发现额外数据缺口。"

_COMPARE_RE = re.compile(r"比较|对比|相比|versus|\bvs\.?\b", re.IGNORECASE)
_CATALYST_RE = re.compile(
    r"为什么.{0,16}(涨|跌)|今日(涨|跌)|今天.{0,8}(涨|跌)|催化剂|最近有什么|重要进展",
    re.IGNORECASE,
)


class ReportShape(StrEnum):
    DEEP = "deep"
    CATALYST = "catalyst"
    COMPARE = "compare"
    MACRO = "macro"
    GENERIC = "generic"


_TEMPLATES: dict[ReportShape, tuple[str, ...]] = {
    ReportShape.DEEP: (
        "Overview",
        "Market Performance",
        "Fundamentals",
        "Valuation",
        BULL_BEAR_ID,
        "Risks",
        "Conclusion",
    ),
    ReportShape.CATALYST: ("Overview", "Catalysts", "Analysis", "Risks"),
    ReportShape.COMPARE: (
        COMPARISON_SECTION_ID,
        "Key Differences",
        "Risks",
        "Conclusion",
    ),
    ReportShape.MACRO: ("Overview", "Analysis", "Risks", "Conclusion"),
    ReportShape.GENERIC: ("Overview", "Analysis", "Risks", "Conclusion"),
}
"""各形态的完整章序。深研 7 节对应 DP「单标的深研」；催化剂走精简结构。"""

_REQUIRED: dict[ReportShape, frozenset[str]] = {
    ReportShape.DEEP: frozenset({"Overview", BULL_BEAR_ID, "Risks", "Conclusion"}),
    ReportShape.CATALYST: frozenset({"Overview", "Catalysts", "Analysis", "Risks"}),
    ReportShape.COMPARE: frozenset(
        {COMPARISON_SECTION_ID, "Key Differences", "Risks", "Conclusion"}
    ),
    ReportShape.MACRO: frozenset({"Overview", "Analysis", "Risks", "Conclusion"}),
    ReportShape.GENERIC: frozenset({"Overview", "Analysis", "Risks", "Conclusion"}),
}

_TRAILING: tuple[str, ...] = (DATA_LIMITATIONS_ID, DISCLAIMER_ID)

SECTION_TITLES: dict[str, str] = {
    "Overview": "概述",
    "Market Performance": "市场表现",
    "Fundamentals": "基本面",
    "Valuation": "估值",
    BULL_BEAR_ID: "看多 / 看空",
    "Risks": "风险",
    "Conclusion": "结论",
    "Catalysts": "催化剂",
    "Analysis": "分析",
    COMPARISON_SECTION_ID: "对比",
    "Key Differences": "关键差异",
    DATA_LIMITATIONS_ID: "数据限制",
    DISCLAIMER_ID: "免责声明",
}


def detect_report_shape(
    question_type: QuestionType | None,
    planned: Sequence[str] = (),
    question: str = "",
) -> ReportShape:
    """问题类型优先；Planner 显式写了 Catalysts 则走精简结构。"""
    planned_ids = {_canonical(item) for item in planned if _canonical(item)}
    if question_type is QuestionType.COMPARE or COMPARISON_SECTION_ID in planned_ids:
        shape = ReportShape.COMPARE
    elif question_type is QuestionType.MACRO:
        shape = ReportShape.MACRO
    elif question_type is QuestionType.GENERIC:
        shape = ReportShape.GENERIC
    elif "Catalysts" in planned_ids or _CATALYST_RE.search(question):
        shape = ReportShape.CATALYST
    elif question_type in {QuestionType.CRYPTO, QuestionType.STOCK}:
        shape = ReportShape.DEEP
    elif _COMPARE_RE.search(question):
        shape = ReportShape.COMPARE
    elif question.strip():
        shape = ReportShape.DEEP
    else:
        shape = ReportShape.GENERIC
    return shape


def resolve_section_ids(
    question_type: QuestionType | None,
    planned: Sequence[str] = (),
    question: str = "",
) -> tuple[str, ...]:
    """给 Writer 的 `sections[].id` 列表。不含 Executive Summary。"""
    shape = detect_report_shape(question_type, planned, question)
    template = _TEMPLATES[shape]
    required = _REQUIRED[shape]
    planned_body = [
        cid
        for item in planned
        if (cid := _canonical(item)) and cid not in {EXECUTIVE_SUMMARY_ID, *_TRAILING}
    ]

    if planned_body:
        wanted = {item for item in planned_body if item in template or item in required}
        wanted.update(required)
        body = [item for item in template if item in wanted]
        extras = [item for item in planned_body if item not in body and item in SECTION_TITLES]
        body.extend(extras)
    else:
        body = list(template)

    trailing = [item for item in _TRAILING if item not in body]
    return (*body, *trailing)


def align_report_sections(
    draft: ResearchReport,
    outline: Sequence[str],
    *,
    data_gaps: Sequence[str] | None = None,
) -> ResearchReport:
    """按大纲重排章节；数据限制与免责声明始终覆盖模型输出。"""
    gaps = list(data_gaps) if data_gaps is not None else list(draft.data_gaps)
    by_id = _index_sections(draft.sections)
    summary = draft.executive_summary.strip()
    dropped = by_id.pop(EXECUTIVE_SUMMARY_ID.casefold(), None)
    if not summary and dropped is not None and dropped.markdown.strip():
        summary = dropped.markdown.strip()

    sections: list[ReportSection] = []
    for section_id in outline:
        if section_id == EXECUTIVE_SUMMARY_ID:
            continue
        if section_id == DATA_LIMITATIONS_ID:
            sections.append(_owned_section(section_id, _data_limitations_markdown(gaps)))
            continue
        if section_id == DISCLAIMER_ID:
            sections.append(_owned_section(section_id, DISCLAIMER_MARKDOWN))
            continue
        existing = by_id.pop(section_id.casefold(), None)
        if existing is not None and existing.markdown.strip():
            sections.append(existing.model_copy(update={"id": section_id}))
            continue
        sections.append(_owned_section(section_id, MISSING_SECTION_MARKDOWN))

    return draft.model_copy(
        update={
            "executive_summary": summary or draft.executive_summary,
            "sections": sections,
            "data_gaps": list(gaps),
        }
    )


def _data_limitations_markdown(gaps: Sequence[str]) -> str:
    unique = list(dict.fromkeys(gap.strip() for gap in gaps if gap.strip()))
    if not unique:
        return NO_GAPS_MARKDOWN
    return "\n".join(f"- {gap}" for gap in unique)


def _owned_section(section_id: str, markdown: str) -> ReportSection:
    return ReportSection(
        id=section_id,
        title=SECTION_TITLES.get(section_id, section_id),
        markdown=markdown,
        claim_ids=[],
    )


def _index_sections(sections: Sequence[ReportSection]) -> dict[str, ReportSection]:
    indexed: dict[str, ReportSection] = {}
    for section in sections:
        key = section.id.casefold()
        if key not in indexed:
            indexed[key] = section
    return indexed


def _canonical(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if text.casefold() == EXECUTIVE_SUMMARY_ID.casefold():
        return EXECUTIVE_SUMMARY_ID
    for known in (*SECTION_TITLES, COMPARISON_SECTION_ID):
        if text.casefold() == known.casefold():
            return known
    return text

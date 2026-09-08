"""报告大纲（P5-7）：问题类型决定章节，数据限制与免责声明由代码写入。"""

from __future__ import annotations

from agent_service.orchestrator.outline import (
    BULL_BEAR_ID,
    DATA_LIMITATIONS_ID,
    DISCLAIMER_ID,
    DISCLAIMER_MARKDOWN,
    MISSING_SECTION_MARKDOWN,
    ReportShape,
    align_report_sections,
    detect_report_shape,
    resolve_section_ids,
)
from agent_service.schemas.common import QuestionType
from agent_service.schemas.report import ReportSection, ResearchReport


def test_crypto_deep_and_compare_and_catalyst_differ() -> None:
    """验收：不同问题类型报告结构不同。"""
    deep = resolve_section_ids(QuestionType.CRYPTO, question="分析 HYPE 的代币经济与估值")
    catalyst = resolve_section_ids(
        QuestionType.CRYPTO,
        planned=("Overview", "Catalysts", "Analysis", "Risks"),
        question="HYPE 为什么今天涨",
    )
    compare = resolve_section_ids(
        QuestionType.COMPARE,
        planned=("Executive Summary", "Comparison", "Key Differences", "Conclusion"),
        question="比较 NVDA、AMD、AVGO",
    )
    assert BULL_BEAR_ID in deep
    assert "Valuation" in deep
    assert BULL_BEAR_ID not in catalyst
    assert "Catalysts" in catalyst
    assert "Valuation" not in catalyst
    assert "Comparison" in compare
    assert "Key Differences" in compare
    assert BULL_BEAR_ID not in compare
    assert deep != catalyst
    assert catalyst != compare
    assert deep != compare
    for outline in (deep, catalyst, compare):
        assert DATA_LIMITATIONS_ID in outline
        assert DISCLAIMER_ID in outline
        assert "Executive Summary" not in outline


def test_planner_overview_risks_still_gets_bull_bear() -> None:
    outline = resolve_section_ids(
        QuestionType.STOCK,
        planned=("Overview", "Risks"),
        question="NVDA 估值贵不贵",
    )
    assert outline[:4] == ("Overview", BULL_BEAR_ID, "Risks", "Conclusion")
    assert "Market Performance" not in outline


def test_news_question_without_plan_uses_catalyst_shape() -> None:
    assert detect_report_shape(None, question="HYPE 最近有什么新闻") is ReportShape.CATALYST
    outline = resolve_section_ids(None, question="HYPE 最近有什么新闻")
    assert outline[:4] == ("Overview", "Catalysts", "Analysis", "Risks")


def test_macro_and_generic_omit_valuation() -> None:
    macro = resolve_section_ids(QuestionType.MACRO, question="美联储会不会降息")
    generic = resolve_section_ids(QuestionType.GENERIC, question="你好")
    assert "Valuation" not in macro
    assert BULL_BEAR_ID not in macro
    assert "Analysis" in macro
    assert generic != resolve_section_ids(QuestionType.CRYPTO, question="分析 SOL 基本面")


def test_align_overwrites_disclaimer_and_fills_gaps() -> None:
    draft = ResearchReport(
        title="T",
        executive_summary="摘要",
        sections=[
            ReportSection(id="Overview", title="概述", markdown="有材料。", claim_ids=[]),
            ReportSection(
                id="Disclaimer",
                title="免责声明",
                markdown="模型瞎写的法律文字。",
                claim_ids=[],
            ),
        ],
        data_gaps=["未找到官方解锁时间表"],
    )
    outline = resolve_section_ids(QuestionType.CRYPTO, planned=("Overview", "Risks"))
    aligned = align_report_sections(draft, outline)
    ids = [section.id for section in aligned.sections]
    assert ids[0] == "Overview"
    assert aligned.sections[0].markdown == "有材料。"
    assert BULL_BEAR_ID in ids
    assert ids[-2:] == [DATA_LIMITATIONS_ID, DISCLAIMER_ID]
    assert "- 未找到官方解锁时间表" in aligned.sections[-2].markdown
    assert aligned.sections[-1].markdown == DISCLAIMER_MARKDOWN
    risks = next(section for section in aligned.sections if section.id == "Risks")
    assert risks.markdown == MISSING_SECTION_MARKDOWN


def test_align_promotes_executive_summary_section_into_field() -> None:
    draft = ResearchReport(
        title="T",
        executive_summary="  ",
        sections=[
            ReportSection(
                id="Executive Summary",
                title="摘要",
                markdown="结论先行的三句话。",
                claim_ids=[],
            )
        ],
    )
    aligned = align_report_sections(draft, (DATA_LIMITATIONS_ID, DISCLAIMER_ID))
    assert aligned.executive_summary == "结论先行的三句话。"
    assert all(section.id != "Executive Summary" for section in aligned.sections)

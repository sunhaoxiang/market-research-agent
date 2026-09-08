"""Report Writer（P2-8）：产出带 [n] 引用的 Markdown。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.report_writer import (
    build_report_writer,
    merge_report_gaps,
    report_writer_user_message,
)
from agent_service.models.registry import ModelRegistry
from agent_service.models.structured_output import run_structured
from agent_service.schemas.claims import Claim
from agent_service.schemas.common import AgentName, ConfidenceLevel, EpistemicType, SourceType
from agent_service.schemas.findings import ResearchFinding
from agent_service.schemas.report import ResearchReport
from agent_service.schemas.sources import Source
from agent_service.sources.citations import assign_citation_indices
from agent_service.testing import IsolatedProviderCredentials, IsolatedSettings

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_URL = "https://theblock.co/hyperliquid-fee-share"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _source() -> Source:
    return Source(
        ref="s1",
        url=_URL,
        url_canonical=_URL,
        title="Fee share",
        domain="theblock.co",
        source_type=SourceType.NEWS,
        retrieved_at=_NOW,
    )


def _finding(source: Source) -> ResearchFinding:
    return ResearchFinding(
        task_id="t1",
        agent=AgentName.WEB_RESEARCH,
        summary="近期有手续费分享讨论。",
        claims=[
            Claim(
                text="Hyperliquid 正在讨论将部分交易手续费分享给 HYPE 持有人。",
                epistemic_type=EpistemicType.SOURCE_BACKED_FACT,
                confidence=ConfidenceLevel.MEDIUM,
                source_ids=[source.id],
            )
        ],
        sources=[source],
        data_gaps=["未找到官方解锁时间表"],
    )


def test_user_message_uses_citation_numbers_not_urls() -> None:
    raw = _source()
    numbered = assign_citation_indices([raw], _finding(raw).claims)
    text = report_writer_user_message(
        "HYPE 最近有什么新闻",
        [_finding(numbered[0])],
        numbered,
        section_ids=("Overview", "Risks"),
        now=_NOW,
    )
    assert "[1] Fee share" in text
    assert "source_backed_fact" in text
    assert "2026-09-08" in text
    built = build_report_writer(_registry())
    assert "2026-09-08" not in str(built.agent.instructions)


def test_report_writer_prompt_hash_is_stable() -> None:
    first = build_report_writer(_registry())
    second = build_report_writer(_registry())
    assert first.prompt.hash == second.prompt.hash
    assert first.prompt.hash != ""


def test_merge_report_gaps_keeps_task_gaps() -> None:
    draft = ResearchReport(title="T", executive_summary="s", data_gaps=["模型提到的缺口"])
    merged = merge_report_gaps(draft, [_finding(_source())])
    assert "模型提到的缺口" in merged.data_gaps
    assert "未找到官方解锁时间表" in merged.data_gaps


async def test_scripted_writer_emits_markdown_with_citation() -> None:
    """验收：产出带引用的 Markdown。"""
    source = _source()
    numbered = assign_citation_indices([source], _finding(source).claims)
    finding = _finding(numbered[0])
    built = build_report_writer(_registry())
    payload = {
        "title": "HYPE 近况",
        "executive_summary": "Hyperliquid 正在讨论手续费分享。[1]",
        "sections": [
            {
                "id": "Overview",
                "title": "概述",
                "markdown": "Hyperliquid 正在讨论把部分交易手续费分享给 HYPE 持有人。[1]",
                "claim_ids": [finding.claims[0].id],
            }
        ],
        "data_gaps": ["未找到官方解锁时间表"],
    }
    scripted = built.agent.clone(
        model=ScriptedModel([[assistant_message(json.dumps(payload, ensure_ascii=False))]])
    )
    structured = await run_structured(
        scripted,
        report_writer_user_message("HYPE 最近有什么新闻", [finding], numbered, now=_NOW),
        strategy=built.strategy,
    )
    report = merge_report_gaps(structured.output, [finding])
    assert "[1]" in report.executive_summary
    assert "[1]" in report.sections[0].markdown
    assert report.sections[0].id == "Overview"
    assert "未找到官方解锁时间表" in report.data_gaps

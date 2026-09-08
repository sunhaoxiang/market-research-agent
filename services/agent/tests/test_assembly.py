"""P5-2：一次研究同时跑 crypto + web，结果回到编排层。

Research Manager 仍然没有工具（决策 A）。子 Agent 由 `SubAgentRunner` 装配，
Python 执行器 fan-out，finding 交给 Writer——不是 Handoff，也不是让 Planner
自己调 `as_tool()`。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agents.testing import ScriptedModel, assistant_message
from pydantic import SecretStr

from agent_service.agents.crypto_research import CryptoResearchAgent, build_crypto_research
from agent_service.agents.report_writer import ReportWriterAgent, build_report_writer
from agent_service.agents.research_manager import PlannerAgent, build_research_manager
from agent_service.agents.runner import SubAgentRunner
from agent_service.agents.web_research import WebResearchAgent, build_web_research
from agent_service.models.registry import ModelRegistry
from agent_service.observability.event_bus import EventBus
from agent_service.orchestrator.pipeline import run_research
from agent_service.schemas.common import AgentName, TaskStatus
from agent_service.schemas.events import AgentStartedPayload, EventType
from agent_service.testing import (
    IsolatedExecutionLimits,
    IsolatedProviderCredentials,
    IsolatedSettings,
)

_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
_CRYPTO_SUMMARY = "HYPE 市值约 140 亿美元（脚本化 crypto）。"
_WEB_SUMMARY = "近期有报道称 Hyperliquid 在讨论手续费分享（脚本化 web）。"


def _registry() -> ModelRegistry:
    return ModelRegistry(
        IsolatedSettings(
            providers=IsolatedProviderCredentials(deepseek_api_key=SecretStr("sk-test"))
        )
    )


def _limits() -> IsolatedExecutionLimits:
    return IsolatedExecutionLimits()


def _plan_json() -> str:
    return json.dumps(
        {
            "question_type": "crypto",
            "interpretation": "用户想了解 Hyperliquid 的基本面与近期进展",
            "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
            "tasks": [
                {
                    "id": "t1",
                    "agent": "crypto_research",
                    "objective": "获取 Hyperliquid 的市值与 TVL",
                    "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
                    "suggested_tools": [],
                    "depends_on": [],
                    "priority": 1,
                },
                {
                    "id": "t2",
                    "agent": "web_research",
                    "objective": "查找 HYPE 最近的重要新闻与官方公告",
                    "entities": [{"type": "crypto", "symbol": "HYPE", "name": "Hyperliquid"}],
                    "suggested_tools": [],
                    "depends_on": [],
                    "priority": 0,
                },
            ],
            "report_sections": ["Overview", "Catalysts"],
            "assumptions": [],
        },
        ensure_ascii=False,
    )


def _finding_json(summary: str) -> str:
    return json.dumps(
        {
            "summary": summary,
            "claims": [
                {
                    "text": summary,
                    "epistemic_type": "fact",
                    "confidence": "medium",
                    "source_refs": [],
                }
            ],
            "metrics": [],
            "data_gaps": [],
        },
        ensure_ascii=False,
    )


def _report_json() -> str:
    return json.dumps(
        {
            "title": "Hyperliquid 研究",
            "executive_summary": "同时用到了结构化数据与网页检索。",
            "sections": [
                {
                    "id": "Overview",
                    "title": "概述",
                    "markdown": _CRYPTO_SUMMARY,
                    "claim_ids": [],
                },
                {
                    "id": "Catalysts",
                    "title": "催化剂",
                    "markdown": _WEB_SUMMARY,
                    "claim_ids": [],
                },
            ],
            "data_gaps": [],
        },
        ensure_ascii=False,
    )


def _scripted_planner() -> PlannerAgent:
    built = build_research_manager(_registry(), _limits())
    return PlannerAgent(
        agent=built.agent.clone(model=ScriptedModel([[assistant_message(_plan_json())]])),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _scripted_crypto() -> CryptoResearchAgent:
    built = build_crypto_research(_registry())
    return CryptoResearchAgent(
        agent=built.agent.clone(
            model=ScriptedModel([[assistant_message(_finding_json(_CRYPTO_SUMMARY))]])
        ),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _scripted_web() -> WebResearchAgent:
    built = build_web_research(_registry())
    return WebResearchAgent(
        agent=built.agent.clone(
            model=ScriptedModel([[assistant_message(_finding_json(_WEB_SUMMARY))]])
        ),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


def _scripted_writer() -> ReportWriterAgent:
    built = build_report_writer(_registry())
    return ReportWriterAgent(
        agent=built.agent.clone(model=ScriptedModel([[assistant_message(_report_json())]])),
        strategy=built.strategy,
        entry=built.entry,
        prompt=built.prompt,
    )


async def test_one_research_uses_crypto_and_web_together() -> None:
    """验收：单次研究可同时用到 crypto + web，finding 都回到编排层。"""
    bus = EventBus("sess-p5-2", heartbeat_interval_s=60.0)
    runner = SubAgentRunner(
        _registry(),
        limits=_limits(),
        fallback_model_id="deepseek:deepseek-v4-pro",
        crypto=_scripted_crypto(),
        web=_scripted_web(),
    )

    outcome = await run_research(
        "Hyperliquid 怎么样？最近有什么进展？",
        planner=_scripted_planner(),
        runner=runner,
        writer=_scripted_writer(),
        limits=_limits(),
        bus=bus,
        now=_NOW,
    )

    assert outcome.succeeded
    by_agent = {finding.agent: finding for finding in outcome.findings}
    assert set(by_agent) == {AgentName.CRYPTO_RESEARCH, AgentName.WEB_RESEARCH}
    assert by_agent[AgentName.CRYPTO_RESEARCH].summary == _CRYPTO_SUMMARY
    assert by_agent[AgentName.WEB_RESEARCH].summary == _WEB_SUMMARY
    assert set(outcome.state.task_status.values()) == {TaskStatus.COMPLETED}
    assert outcome.report is not None
    assert outcome.report.title == "Hyperliquid 研究"

    bus.close()
    events = [event async for event in bus.stream()]
    started = [
        event.payload.agent
        for event in events
        if event.type is EventType.AGENT_STARTED and isinstance(event.payload, AgentStartedPayload)
    ]
    assert AgentName.CRYPTO_RESEARCH in started
    assert AgentName.WEB_RESEARCH in started


def test_planner_still_has_no_tools_after_assembly() -> None:
    """决策 A：装配子 Agent 不能把工具挂到规划者身上。"""
    built = build_research_manager(_registry(), _limits())
    assert built.agent.tools == []

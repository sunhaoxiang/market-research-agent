"""Report Writer Agent（§6.2，P2-8 / P5-7）。

无工具：只把已有 findings 写成 `ResearchReport`。[n] 编号由代码先分配，
模型只在正文里引用这些编号——让它自己编序号会和来源账对不上。
章节 id、数据限制与免责声明由编排层对齐，不要让模型抄 claim id。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings

from agent_service.models.structured_output import build_strategy
from agent_service.observability.prompts import PromptFingerprint, fingerprint
from agent_service.prompts import load_prompt
from agent_service.schemas.common import AgentName, ModelRole, VerificationStatus
from agent_service.schemas.report import ResearchReport
from agent_service.sources.citations import bibliography

if TYPE_CHECKING:
    from agent_service.models.catalog import ModelEntry
    from agent_service.models.registry import ModelRegistry
    from agent_service.models.structured_output import StructuredOutputStrategy
    from agent_service.schemas.findings import Conflict, FactCheckResult, ResearchFinding
    from agent_service.schemas.sources import Source

PROMPT_NAME = "report_writer"


@dataclass(frozen=True)
class ReportWriterAgent:
    agent: Agent[Any]
    strategy: StructuredOutputStrategy[ResearchReport]
    entry: ModelEntry
    prompt: PromptFingerprint

    @property
    def model_id(self) -> str:
        return self.entry.id


def build_report_writer(
    registry: ModelRegistry, *, model_id: str | None = None
) -> ReportWriterAgent:
    resolved = registry.resolve(model_id) if model_id else registry.for_role(ModelRole.WRITING)
    instructions = load_prompt(PROMPT_NAME)
    return ReportWriterAgent(
        agent=Agent(
            name=AgentName.REPORT_WRITER.value,
            instructions=instructions,
            model=resolved.model,
            model_settings=resolved.settings.resolve(ModelSettings(temperature=0.3)),
            tools=[],
        ),
        strategy=build_strategy(ResearchReport, resolved.entry.capabilities),
        entry=resolved.entry,
        prompt=fingerprint(instructions),
    )


def report_writer_user_message(
    question: str,
    findings: list[ResearchFinding],
    sources: list[Source],
    *,
    section_ids: tuple[str, ...] = (),
    conflicts: Sequence[Conflict] = (),
    comparison_table: str | None = None,
    fact_check: FactCheckResult | None = None,
    now: datetime | None = None,
) -> str:
    """日期、问题、发现都放 user 消息，避免污染 system prompt 缓存前缀。"""
    moment = now or datetime.now(UTC)
    cited = bibliography(sources)
    parts = [
        f"当前日期：{moment.strftime('%Y-%m-%d')}（UTC）",
        f"用户问题：{question.strip()}",
    ]
    if section_ids:
        parts.append("报告章节（sections[].id 必须使用这些值）：" + " / ".join(section_ids))
    parts.append(_findings_block(findings, {item.id: item for item in cited}))
    parts.append(_bibliography_block(cited))
    gaps = [gap for finding in findings for gap in finding.data_gaps]
    if gaps:
        unique = list(dict.fromkeys(gaps))
        parts.append(
            "各任务声明的数据缺口（必须写入 data_gaps）：\n" + "\n".join(f"- {g}" for g in unique)
        )
    conflict_block = _conflicts_block(conflicts)
    if conflict_block is not None:
        parts.append(conflict_block)
    check_block = _fact_check_block(fact_check)
    if check_block is not None:
        parts.append(check_block)
    if comparison_table:
        parts.append("对比表（Comparison 章节必须原样纳入，不要改数字）：\n" + comparison_table)
    parts.append("请根据以上发现撰写结构化报告。")
    return "\n\n".join(parts)


def merge_report_gaps(draft: ResearchReport, findings: list[ResearchFinding]) -> ResearchReport:
    """LLM 可能漏抄缺口；编排层把各任务的 data_gaps 并进去。"""
    merged = list(
        dict.fromkeys([*draft.data_gaps, *[gap for item in findings for gap in item.data_gaps]])
    )
    if merged == draft.data_gaps:
        return draft
    return draft.model_copy(update={"data_gaps": merged})


def _findings_block(findings: list[ResearchFinding], cited: dict[str, Source]) -> str:
    if not findings:
        return "研究发现：本次没有成功的任务产出。"
    chunks = ["研究发现："]
    for finding in findings:
        chunks.append(f"## 任务 {finding.task_id}（{finding.agent.value}）")
        chunks.append(finding.summary)
        for claim in finding.claims:
            marks = "".join(
                f"[{cited[sid].citation_index}]"
                for sid in claim.source_ids
                if sid in cited and cited[sid].citation_index is not None
            )
            bits = [claim.epistemic_type.value, claim.confidence.value]
            if claim.verification is not VerificationStatus.UNVERIFIED:
                bits.append(claim.verification.value)
            label = " / ".join(bits)
            chunks.append(f"- [{label}] {claim.text} {marks}".rstrip())
        if finding.metrics:
            chunks.append(
                "指标："
                + "；".join(
                    f"{(metric.entity_symbol or '').strip()} {metric.name}={metric.value:g}".strip()
                    for metric in finding.metrics
                )
            )
    return "\n".join(chunks)


def _conflicts_block(conflicts: Sequence[Conflict]) -> str | None:
    if not conflicts:
        return None
    lines = ["数值冲突（必须并列写出各源数值与出处，不要取平均、不要只保留其中一个）："]
    for item in conflicts:
        lines.append(f"- {item.description}")
        if item.values:
            lines.append("  " + "；".join(item.values))
    return "\n".join(lines)


def _fact_check_block(result: FactCheckResult | None) -> str | None:
    if result is None:
        return None
    lines: list[str] = []
    if result.verifications:
        lines.append(
            "事实核查（refuted / unsupported 不得写成确定事实；"
            "verified 可按事实写；conflicting 并列各源）："
        )
        for item in result.verifications:
            note = f"：{item.note}" if item.note else ""
            lines.append(f"- {item.claim_id} → {item.verification.value}{note}")
    if result.notes:
        if not lines:
            lines.append("事实核查备注：")
        for note in result.notes:
            lines.append(f"- {note}")
    if not lines:
        return None
    return "\n".join(lines)


def _bibliography_block(cited: list[Source]) -> str:
    if not cited:
        return "来源清单：无。正文不要使用 [n] 引用。"
    lines = ["来源清单（正文只允许使用这些 [n]）："]
    for source in cited:
        title = source.title or source.domain or source.url
        lines.append(
            f"[{source.citation_index}] {title} — {source.domain or ''} "
            f"({source.reliability.value})"
        )
        if source.excerpt:
            lines.append(f"    摘录：{source.excerpt}")
    return "\n".join(lines)

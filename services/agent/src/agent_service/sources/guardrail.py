"""引用完整性校验（§15.4 / §6.3，P2-9）。

纯代码，不调 LLM。报告写完后跑一遍：坏的 `[n]`、落空的来源、404、
「建议买入」这类投资建议。不过就回喂 Writer 改一次；再不过就降级标注。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

from agent_service.schemas.claims import Claim
from agent_service.schemas.report import ResearchReport
from agent_service.schemas.sources import Source
from agent_service.sources.citations import bibliography

CITATION_WARNING_CODE = "citation.integrity"

# `[n]` 且后面不是 markdown 链接的 `(url)`
_CITATION_RE = re.compile(r"\[(\d+)\](?!\()")
_ADVICE_RE = re.compile(
    r"(建议|应当|应该)(立即)?(买入|卖出|加仓|减仓|建仓|清仓)(?!价)"
    r"|强烈(买入|卖出)(?!价)"
    r"|(?<!不构成)(?<!并非)(?<!不是)(买入|卖出)建议"
)


@dataclass(frozen=True)
class CitationIssue:
    code: str
    message: str


def extract_citation_numbers(text: str) -> tuple[int, ...]:
    """按出现顺序去重，保留正文里的 `[n]`。"""
    seen: list[int] = []
    for match in _CITATION_RE.finditer(text):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return tuple(seen)


def check_report(
    report: ResearchReport,
    sources: Sequence[Source],
    claims: Sequence[Claim],
) -> list[CitationIssue]:
    """返回发现的问题。空列表表示通过。"""
    cited = bibliography(sources)
    valid = {item.citation_index for item in cited if item.citation_index is not None}
    by_index = {item.citation_index: item for item in cited if item.citation_index is not None}
    known_ids = {item.id for item in sources}
    issues: list[CitationIssue] = []

    for number in extract_citation_numbers(_report_text(report)):
        if number not in valid:
            allowed = "、".join(f"[{n}]" for n in sorted(valid)) or "（来源清单为空，不应出现 [n]）"
            issues.append(
                CitationIssue(
                    code="dangling_citation",
                    message=f"正文出现了 [{number}]，来源清单里没有这个编号。只能使用 {allowed}。",
                )
            )
            continue
        source = by_index[number]
        if not _usable_url(source.url):
            issues.append(
                CitationIssue(
                    code="invalid_url",
                    message=f"[{number}] 的 URL 不是可访问的 http(s) 地址：{source.url}",
                )
            )
        if source.http_status == HTTPStatus.NOT_FOUND:
            issues.append(
                CitationIssue(
                    code="unavailable",
                    message=f"[{number}] 抓取时 HTTP 404，不能作为有效引用。",
                )
            )

    for claim in claims:
        if not claim.requires_source:
            continue
        if not claim.source_ids:
            issues.append(
                CitationIssue(
                    code="missing_source",
                    message=f"有来源支撑的陈述缺少 source_ids：{claim.text}",
                )
            )
            continue
        if any(sid not in known_ids for sid in claim.source_ids):
            issues.append(
                CitationIssue(
                    code="unknown_source",
                    message=f"陈述引用了未登记的来源：{claim.text}",
                )
            )

    advice = _ADVICE_RE.search(_report_text(report))
    if advice is not None:
        issues.append(
            CitationIssue(
                code="investment_advice",
                message=f"报告含投资建议表述「{advice.group(0)}」，请改成中性描述风险与情景。",
            )
        )
    return issues


def format_citation_feedback(issues: Sequence[CitationIssue]) -> str:
    """回喂 Writer 的修正说明。只改引用与口径，不要重做研究。"""
    lines = [
        "引用完整性检查未通过。请只输出修正后的 JSON，不要解释。",
        "问题：",
        *[f"- {item.message}" for item in issues],
    ]
    return "\n".join(lines)


def strip_unknown_citations(text: str, valid: set[int]) -> str:
    """把对不上来源清单的 `[n]` 从正文拿掉，避免前端出现死链。"""

    def keep(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return match.group(0) if number in valid else ""

    return _CITATION_RE.sub(keep, text)


def apply_degradation(
    report: ResearchReport,
    sources: Sequence[Source],
    issues: Sequence[CitationIssue],
) -> ResearchReport:
    """第二次仍失败：去掉无法解析的 `[n]`，把问题写进 data_gaps。"""
    valid = {
        item.citation_index for item in bibliography(sources) if item.citation_index is not None
    }
    note = "引用完整性校验未完全通过：" + "；".join(item.message for item in issues)
    sections = [
        section.model_copy(update={"markdown": strip_unknown_citations(section.markdown, valid)})
        for section in report.sections
    ]
    summary = strip_unknown_citations(report.executive_summary, valid)
    title = strip_unknown_citations(report.title, valid)
    gaps = list(report.data_gaps)
    if note not in gaps:
        gaps.append(note)
    return report.model_copy(
        update={
            "title": title,
            "executive_summary": summary,
            "sections": sections,
            "data_gaps": gaps,
        }
    )


def _report_text(report: ResearchReport) -> str:
    parts = [report.title, report.executive_summary, *[s.markdown for s in report.sections]]
    return "\n".join(parts)


def _usable_url(url: str) -> bool:
    parsed = urlsplit(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

"""给被引用的来源编上报告里的 [n]（§15.4-3）。

编号是确定性的：按登记顺序，只给至少被一条 claim 引用过的来源发号。
没人引用的（孤儿）保持 `citation_index=None`，不进参考文献。
章节的 `claim_ids` 也在这里回填：模型抄 id 会抄错，正文里的 `[n]` 才可靠。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from agent_service.schemas.claims import Claim
from agent_service.schemas.report import ResearchReport
from agent_service.schemas.sources import Source

# `[n]` 且后面不是 markdown 链接的 `(url)`
CITATION_RE = re.compile(r"\[(\d+)\](?!\()")


def assign_citation_indices(sources: Sequence[Source], claims: Sequence[Claim]) -> list[Source]:
    """返回带 `citation_index` 的新列表，不改传入对象。"""
    cited = {source_id for claim in claims for source_id in claim.source_ids}
    index = 0
    numbered: list[Source] = []
    for source in sources:
        if source.id in cited:
            index += 1
            numbered.append(source.model_copy(update={"citation_index": index}))
        else:
            numbered.append(source.model_copy(update={"citation_index": None}))
    return numbered


def bibliography(sources: Sequence[Source]) -> list[Source]:
    """已编号、按 [n] 排序的参考文献。孤儿不在内。"""
    cited = [source for source in sources if source.citation_index is not None]
    return sorted(cited, key=lambda item: item.citation_index or 0)


def extract_citation_numbers(text: str) -> tuple[int, ...]:
    """按出现顺序去重，保留正文里的 `[n]`。"""
    seen: list[int] = []
    for match in CITATION_RE.finditer(text):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return tuple(seen)


def attach_section_claims(
    report: ResearchReport,
    sources: Sequence[Source],
    claims: Sequence[Claim],
) -> ResearchReport:
    """用正文 `[n]` 与 claim 原文回填各节 `claim_ids`，覆盖模型输出。"""
    index_by_id = {
        source.id: source.citation_index for source in sources if source.citation_index is not None
    }
    sections = [
        section.model_copy(
            update={
                "claim_ids": _section_claim_ids(section.markdown, claims, index_by_id),
            }
        )
        for section in report.sections
    ]
    if sections == report.sections:
        return report
    return report.model_copy(update={"sections": sections})


def _section_claim_ids(
    markdown: str,
    claims: Sequence[Claim],
    index_by_id: dict[str, int],
) -> list[str]:
    cited = set(extract_citation_numbers(markdown))
    ids: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        claim_indices = {index_by_id[sid] for sid in claim.source_ids if sid in index_by_id}
        matched = bool(cited & claim_indices) or (bool(claim.text) and claim.text in markdown)
        if matched and claim.id not in seen:
            seen.add(claim.id)
            ids.append(claim.id)
    return ids

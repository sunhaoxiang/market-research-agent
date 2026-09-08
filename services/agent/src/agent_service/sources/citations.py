"""给被引用的来源编上报告里的 [n]（§15.4-3）。

编号是确定性的：按登记顺序，只给至少被一条 claim 引用过的来源发号。
没人引用的（孤儿）保持 `citation_index=None`，不进参考文献。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_service.schemas.claims import Claim
from agent_service.schemas.sources import Source


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

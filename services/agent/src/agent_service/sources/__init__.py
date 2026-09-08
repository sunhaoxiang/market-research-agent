"""Source 归一化、可靠性分级、引用编号与会话级登记（§15.1–15.4）。"""

from agent_service.sources.canonical import canonicalize_url
from agent_service.sources.citations import (
    assign_citation_indices,
    attach_section_claims,
    bibliography,
    extract_citation_numbers,
)
from agent_service.sources.guardrail import check_report
from agent_service.sources.registry import SourceRegistry
from agent_service.sources.reliability import classify_reliability

__all__ = [
    "SourceRegistry",
    "assign_citation_indices",
    "attach_section_claims",
    "bibliography",
    "canonicalize_url",
    "check_report",
    "classify_reliability",
    "extract_citation_numbers",
]

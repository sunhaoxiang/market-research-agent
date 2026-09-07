"""研究报告（§16 / §15.4）。"""

from __future__ import annotations

from pydantic import Field

from agent_service.schemas.common import Schema


class ReportSection(Schema):
    id: str = Field(description="章节标识，对应 ResearchPlan.report_sections 中的项")
    title: str
    markdown: str = Field(description="章节正文。引用用 [n] 形式，n 对应 Source.citation_index")
    claim_ids: list[str] = Field(
        default_factory=list, description="本章节引用的 claim，供 UI 反查与引用完整性校验"
    )


class ResearchReport(Schema):
    """Report Writer 的 `output_type`。"""

    title: str
    executive_summary: str = Field(description="结论先行，3-5 句")
    sections: list[ReportSection] = Field(default_factory=list)
    data_gaps: list[str] = Field(
        default_factory=list, description="汇总各任务的 data_gaps，进入报告的「数据限制」章节"
    )


class ReportMetadata(Schema):
    """报告的附加信息，由编排层填充而非 LLM 输出。"""

    report_sections: list[str] = Field(default_factory=list)
    generated_by_model: str
    data_gaps: list[str] = Field(default_factory=list)
    citation_check_passed: bool = True
    citation_warnings: list[str] = Field(default_factory=list)

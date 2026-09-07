"""陈述（Claim）。

`ClaimDraft` 是 LLM 直接输出的形态，`Claim` 是编排层补全 id 与来源映射后的形态。
分成两个模型的理由：让 LLM 只做它擅长的事（判断陈述性质），
把 id 生成与 URL 关联交给确定性代码（§15.1）。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from agent_service.schemas.common import (
    ConfidenceLevel,
    EpistemicType,
    Schema,
    VerificationStatus,
)
from agent_service.utils.ids import new_id


class ClaimDraft(Schema):
    """LLM 输出的陈述。

    `epistemic_type` 必填是 §15.3 三重强制机制的第一重（schema 强制）——
    LLM 无法省略对陈述性质的判断。
    """

    text: str = Field(description="一句自包含的陈述，不依赖上下文即可理解")
    epistemic_type: EpistemicType
    confidence: ConfidenceLevel
    source_refs: list[str] = Field(
        default_factory=list,
        description="支撑本陈述的来源短引用，如 ['s1', 's3']。只能引用工具结果中出现过的 ref",
    )
    as_of: datetime | None = Field(
        default=None, description="数据时点（不是抓取时点）。金融数据必须填"
    )


class Claim(Schema):
    """编排层补全后的陈述。"""

    id: str = Field(default_factory=new_id)
    text: str
    epistemic_type: EpistemicType
    confidence: ConfidenceLevel
    source_ids: list[str] = Field(default_factory=list, description="指向 Source.id")
    as_of: datetime | None = None

    task_id: str | None = None
    agent: str | None = None

    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    verification_note: str | None = None
    citation_index: int | None = None

    @property
    def requires_source(self) -> bool:
        """SOURCE_BACKED_FACT 必须有来源，否则应降级为 FACT（§15.3 第三重强制）。"""
        return self.epistemic_type is EpistemicType.SOURCE_BACKED_FACT

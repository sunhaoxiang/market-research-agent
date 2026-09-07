"""研究标的与结构化数值。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from agent_service.schemas.common import AssetType, Schema


class Entity(Schema):
    """已解析的研究标的。

    由 Research Manager 在规划阶段解析（"英伟达" → `{stock, NVDA}`），
    后续所有 Agent 与 Tool 都基于此结构，避免各自重复做名称解析。
    """

    type: AssetType
    symbol: str = Field(description="标准化代号，如 NVDA / BTC / HYPE")
    name: str | None = Field(default=None, description="全称，如 NVIDIA Corporation")
    chain: str | None = Field(
        default=None,
        description="仅 crypto：所在链，如 ethereum / solana / hyperliquid",
    )
    contract_address: str | None = Field(
        default=None, description="仅 crypto：合约地址（用于链上数据查询）"
    )


class MetricPoint(Schema):
    """一个结构化数值观测点，供前端画图（§6.2）。

    刻意与 Claim 分开：Claim 是自然语言陈述，MetricPoint 是机器可读的数字。
    同一事实通常同时产生两者——"TVL 达到 12 亿美元" 与 `{name: tvl, value: 1.2e9}`。
    时间序列表示为 `name` 相同、`as_of` 不同的多个点。
    """

    name: str = Field(description="机器可读指标名，snake_case，如 tvl / market_cap / pe_ratio")
    label: str = Field(description="人类可读标签，如 TVL / 市值 / 市盈率")
    value: float
    unit: str | None = Field(default=None, description="USD / % / 倍 等")
    entity_symbol: str | None = Field(default=None, description="所属标的，便于前端分组")
    as_of: datetime | None = Field(default=None, description="数据时点，不是抓取时点")
    source_ref: str | None = Field(
        default=None, description="来源短引用（如 s1），由编排层解析为 source_id"
    )

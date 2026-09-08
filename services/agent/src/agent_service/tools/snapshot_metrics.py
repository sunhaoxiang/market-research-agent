"""从已成功的 tool 结果抽出标量指标，供超时 salvage 写入对比表。

序列点由 `series_metrics` 负责（直播图表）。这里只取最新一期的营收 / 净利 /
EPS / 估值 / 行情，避免 salvage finding 塞进整段历史。数字来自 tool 已解析
的字段，不让模型补。

本模块由 collector 加载，不能 import financials / stocks / crypto 包（循环
依赖），所以只认字段形状。
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from agent_service.schemas.entities import MetricPoint
from agent_service.tools.series_metrics import metrics_from_tool_data

if TYPE_CHECKING:
    from agent_service.schemas.tools import ToolResult
    from agent_service.tools.web.collector import SourceCollector

_MONEY = (
    ("revenue", "营收"),
    ("operating_income", "经营利润"),
    ("net_income", "净利"),
    ("eps_diluted", "稀释EPS"),
    ("eps", "EPS"),
)
_YOY = (
    ("revenue_yoy", "营收同比"),
    ("net_income_yoy", "净利同比"),
    ("operating_income_yoy", "经营利润同比"),
    ("eps_yoy", "EPS同比"),
)
_MULTIPLES = (
    ("pe", "PE"),
    ("pb", "PB"),
    ("ps", "PS"),
    ("ev_ebitda", "EV/EBITDA"),
)


def remember_tool_metrics[T](collector: SourceCollector, result: ToolResult[T]) -> None:
    """把本次 tool 已拿到的标量（外加序列最新一点）留给 salvage。"""
    if not result.ok or result.data is None or result.ref is None:
        return
    points = snapshot_metrics_from_tool_data(result.data, result.ref)
    series = metrics_from_tool_data(result.data, result.ref)
    if series:
        points.append(series[-1])
    collector.metrics.extend(points)


def snapshot_metrics_from_tool_data(data: object, source_ref: str) -> list[MetricPoint]:
    if _is_income(data):
        points = _from_income(data, source_ref)
    elif _has(data, "revenue_yoy"):
        points = _from_named(data, _YOY, unit=None, source_ref=source_ref, as_of_attr="as_of")
    elif _has(data, "current_price") and _has(data, "symbol"):
        points = _from_crypto_market(data, source_ref)
    elif _has(data, "coin_id") and _has(data, "price"):
        points = _from_crypto_price(data, source_ref)
    elif _has(data, "ticker") and _has(data, "price"):
        points = _from_quote(data, source_ref)
    elif _has(data, "revenue") and _has(data, "ticker") and not _has(data, "rows"):
        points = _from_named(
            data, _MONEY, unit="USD", source_ref=source_ref, as_of_attr="period_end"
        )
    elif _has(data, "ticker") and (_has(data, "ev_ebitda") or _has(data, "pe")):
        points = _from_named(data, _MULTIPLES, unit="x", source_ref=source_ref, as_of_attr=None)
    elif _has(data, "ticker") and _has(data, "market_cap"):
        points = _from_named(
            data,
            (("market_cap", "市值"),),
            unit="USD",
            source_ref=source_ref,
            as_of_attr=None,
        )
    else:
        points = []
    return points


def _from_income(data: object, source_ref: str) -> list[MetricPoint]:
    rows = getattr(data, "rows", None)
    if not isinstance(rows, list) or not rows:
        return []
    return _from_named(
        rows[0],
        _MONEY,
        unit="USD",
        source_ref=source_ref,
        entity=str(getattr(data, "ticker", "")),
        as_of_attr="period_end",
    )


def _from_quote(data: object, source_ref: str) -> list[MetricPoint]:
    as_of = _as_of(getattr(data, "as_of", None))
    entity = str(getattr(data, "ticker", ""))
    points = _scalars(
        data,
        (("price", "价格"), ("market_cap", "市值"), ("eps", "EPS")),
        entity=entity,
        unit="USD",
        as_of=as_of,
        source_ref=source_ref,
    )
    points.extend(
        _scalars(
            data,
            (("pe", "PE"),),
            entity=entity,
            unit="x",
            as_of=as_of,
            source_ref=source_ref,
        )
    )
    return points


def _from_crypto_price(data: object, source_ref: str) -> list[MetricPoint]:
    unit = str(getattr(data, "vs_currency", "usd")).upper()
    return _scalars(
        data,
        (("price", "价格"), ("market_cap", "市值"), ("volume_24h", "24h成交额")),
        entity=str(getattr(data, "coin_id", "")),
        unit=unit,
        as_of=_as_of(getattr(data, "as_of", None)),
        source_ref=source_ref,
    )


def _from_crypto_market(data: object, source_ref: str) -> list[MetricPoint]:
    unit = str(getattr(data, "vs_currency", "usd")).upper()
    entity = str(getattr(data, "symbol", ""))
    as_of = _as_of(getattr(data, "last_updated", None))
    points: list[MetricPoint] = []
    for name, label, attr in (
        ("price", "价格", "current_price"),
        ("market_cap", "市值", "market_cap"),
        ("volume_24h", "24h成交额", "total_volume"),
    ):
        item = _point(
            name,
            label,
            getattr(data, attr, None),
            unit=unit,
            entity=entity,
            as_of=as_of,
            source_ref=source_ref,
        )
        if item is not None:
            points.append(item)
    return points


def _from_named(
    data: object,
    fields: tuple[tuple[str, str], ...],
    *,
    unit: str | None,
    source_ref: str,
    as_of_attr: str | None,
    entity: str | None = None,
) -> list[MetricPoint]:
    symbol = entity if entity is not None else str(getattr(data, "ticker", ""))
    as_of = _as_of(getattr(data, as_of_attr, None)) if as_of_attr else None
    return _scalars(data, fields, entity=symbol, unit=unit, as_of=as_of, source_ref=source_ref)


def _scalars(
    data: object,
    fields: tuple[tuple[str, str], ...],
    *,
    entity: str,
    unit: str | None,
    as_of: datetime | None,
    source_ref: str,
) -> list[MetricPoint]:
    points: list[MetricPoint] = []
    for name, label in fields:
        item = _point(
            name,
            label,
            getattr(data, name, None),
            unit=unit,
            entity=entity,
            as_of=as_of,
            source_ref=source_ref,
        )
        if item is not None:
            points.append(item)
    return points


def _point(
    name: str,
    label: str,
    value: object,
    *,
    unit: str | None,
    entity: str,
    as_of: datetime | None,
    source_ref: str,
) -> MetricPoint | None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        return None
    symbol = entity.strip()
    if not symbol:
        return None
    return MetricPoint(
        name=name,
        label=label,
        value=float(value),
        unit=unit,
        entity_symbol=symbol,
        as_of=as_of,
        source_ref=source_ref,
    )


def _as_of(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return None


def _is_income(data: object) -> bool:
    rows = getattr(data, "rows", None)
    if not isinstance(rows, list) or not rows:
        return False
    row = rows[0]
    return _has(row, "revenue") and _has(row, "eps_diluted")


def _has(data: object, name: str) -> bool:
    return hasattr(data, name)

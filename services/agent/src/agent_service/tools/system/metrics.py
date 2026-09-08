"""确定性金融指标。不把算术交给 LLM（§8.5）。"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime

_YEAR_SECONDS = 365.25 * 24 * 3600
_DEFAULT_PERIODS_PER_YEAR = 365.0
_MIN_PAIRS = 2


def price_return(first: float, last: float) -> float | None:
    """(last / first) - 1。起点为 0 时无定义。"""
    if first == 0 or not math.isfinite(first) or not math.isfinite(last):
        return None
    return last / first - 1.0


def cagr(first: float, last: float, years: float) -> float | None:
    """(last / first) ** (1 / years) - 1。起点必须为正，年数必须为正。"""
    if years <= 0 or first <= 0 or last < 0:
        return None
    if not math.isfinite(first) or not math.isfinite(last) or not math.isfinite(years):
        return None
    return (last / first) ** (1.0 / years) - 1.0


def range_percentile(values: Sequence[float], current: float) -> float | None:
    """末值在 [min, max] 中的位置，0–100。常数序列无定义。"""
    if not values:
        return None
    lo = min(values)
    hi = max(values)
    if hi == lo or not math.isfinite(current):
        return None
    return 100.0 * (current - lo) / (hi - lo)


def log_return_vol(values: Sequence[float], *, periods_per_year: float) -> float | None:
    """对数收益率的样本标准差，再按 periods_per_year 年化。需要至少两个有效收益。"""
    if periods_per_year <= 0 or not math.isfinite(periods_per_year):
        return None
    returns: list[float] = []
    previous: float | None = None
    for value in values:
        if not math.isfinite(value) or value <= 0:
            previous = None
            continue
        if previous is not None:
            returns.append(math.log(value / previous))
        previous = value
    if len(returns) < _MIN_PAIRS:
        return None
    return statistics.stdev(returns) * math.sqrt(periods_per_year)


def years_between(start: datetime, end: datetime) -> float | None:
    delta = (_aware(end) - _aware(start)).total_seconds()
    if delta <= 0:
        return None
    return delta / _YEAR_SECONDS


def infer_periods_per_year(timestamps: Sequence[datetime]) -> float | None:
    if len(timestamps) < _MIN_PAIRS:
        return None
    gaps = [
        (_aware(timestamps[i]) - _aware(timestamps[i - 1])).total_seconds()
        for i in range(1, len(timestamps))
        if (_aware(timestamps[i]) - _aware(timestamps[i - 1])).total_seconds() > 0
    ]
    if not gaps:
        return None
    gaps.sort()
    median = gaps[len(gaps) // 2]
    return _YEAR_SECONDS / median


def default_periods_per_year() -> float:
    return _DEFAULT_PERIODS_PER_YEAR


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts

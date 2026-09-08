"""FMP 美股客户端（P4-1）。

免费档 250 次/天（§3.6 / §23 R1）。限流/缓存/日配额走 `BaseProvider`；
本文件只负责路径、JSON 形状，以及「没有这只股票」的错误映射。

key 走 `apikey` 请求头，**不放进 query**——放进 params 会进缓存键，
换 key 写法就让命中率掉到 0，SQLite 里也会留下 secret 的哈希痕迹。

五个方法是给后面任务用的原语，不要在 tool 层再包一遍 HTTP：

- `get_quote` / `get_profile` / `get_historical_prices` / `get_peers` → **P4-4**
- `get_ratios_ttm` → **P4-7** 估值。财报主路径是 SEC；FMP 只补比率。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from agent_service.providers.base import BaseProvider, ProviderResponse
from agent_service.providers.errors import ProviderError
from agent_service.providers.profiles import ProviderProfile
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

_FMP_BASE = "https://financialmodelingprep.com/stable"
_KEY_HEADER = "apikey"
_MAX_HISTORY_DAYS = 1825  # 5 年；估值分位会用到
_QUOTE_PATH = "/quote"
_PROFILE_PATH = "/profile"
_HISTORY_PATH = "/historical-price-eod/full"
_PEERS_PATH = "/stock-peers"
_RATIOS_PATH = "/ratios-ttm"


def stock_page_url(symbol: str) -> str:
    """给人点的页面，不是 API URL。引用要能打开。"""
    return f"https://financialmodelingprep.com/financial-summary/{symbol}"


@dataclass(frozen=True, slots=True)
class StockQuote:
    symbol: str
    name: str | None
    price: float
    change: float | None
    change_pct: float | None
    volume: float | None
    day_low: float | None
    day_high: float | None
    year_low: float | None
    year_high: float | None
    market_cap: float | None
    open: float | None
    previous_close: float | None
    pe: float | None
    eps: float | None
    exchange: str | None
    as_of: datetime | None
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class StockProfile:
    symbol: str
    name: str | None
    description: str | None
    cik: str | None
    exchange: str | None
    industry: str | None
    sector: str | None
    country: str | None
    currency: str | None
    website: str | None
    ceo: str | None
    ipo_date: date | None
    employees: int | None
    market_cap: float | None
    beta: float | None
    is_etf: bool | None
    is_actively_trading: bool | None
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class PriceBar:
    session: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


@dataclass(frozen=True, slots=True)
class StockHistory:
    symbol: str
    days: int
    start: date
    end: date
    bars: tuple[PriceBar, ...]
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class StockPeer:
    symbol: str
    name: str | None
    price: float | None
    market_cap: float | None
    url: str


@dataclass(frozen=True, slots=True)
class StockPeers:
    symbol: str
    peers: tuple[StockPeer, ...]
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class ValuationRatios:
    """TTM 估值比率。缺字段保持 None，不要当成 0。"""

    symbol: str
    pe: float | None
    pb: float | None
    ps: float | None
    ev_ebitda: float | None
    dividend_yield: float | None
    url: str
    provenance: DataProvenance


class FmpProvider(BaseProvider):
    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = _FMP_BASE,
        profile: ProviderProfile | None = None,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                "FMP API key 为空",
                provider="fmp",
            )
        super().__init__(
            name="fmp",
            base_url=base_url,
            runtime=runtime,
            headers={"Accept": "application/json", _KEY_HEADER: key},
            client=client,
            profile=profile,
        )

    async def get_quote(self, symbol: str) -> StockQuote:
        ticker = _require_symbol(symbol, endpoint=_QUOTE_PATH)
        response = await self._get(_QUOTE_PATH, CacheTTL.REALTIME, {"symbol": ticker})
        return _parse_quote(response, symbol=ticker)

    async def get_profile(self, symbol: str) -> StockProfile:
        ticker = _require_symbol(symbol, endpoint=_PROFILE_PATH)
        response = await self._get(_PROFILE_PATH, CacheTTL.PROFILE, {"symbol": ticker})
        return _parse_profile(response, symbol=ticker)

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        ticker = _require_symbol(symbol, endpoint=_HISTORY_PATH)
        if days < 1 or days > _MAX_HISTORY_DAYS:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                f"历史区间必须在 1–{_MAX_HISTORY_DAYS} 天",
                provider=self.name,
                endpoint=_HISTORY_PATH,
            )
        end = self.runtime.clock.now().date()
        start = end - timedelta(days=days)
        response = await self._get(
            _HISTORY_PATH,
            CacheTTL.HISTORY_TODAY,
            {"symbol": ticker, "from": start.isoformat(), "to": end.isoformat()},
        )
        return _parse_history(response, symbol=ticker, days=days, start=start, end=end)

    async def get_peers(self, symbol: str) -> StockPeers:
        ticker = _require_symbol(symbol, endpoint=_PEERS_PATH)
        response = await self._get(_PEERS_PATH, CacheTTL.PROFILE, {"symbol": ticker})
        return _parse_peers(response, symbol=ticker)

    async def get_ratios_ttm(self, symbol: str) -> ValuationRatios:
        ticker = _require_symbol(symbol, endpoint=_RATIOS_PATH)
        response = await self._get(_RATIOS_PATH, CacheTTL.MARKET, {"symbol": ticker})
        return _parse_ratios(response, symbol=ticker)

    async def _get(
        self,
        endpoint: str,
        ttl: CacheTTL,
        params: dict[str, str | int | float | bool | None],
    ) -> ProviderResponse:
        try:
            return await self.get_json(endpoint, ttl=ttl, params=params)
        except ProviderError as exc:
            raise _map_http_error(exc, endpoint=endpoint) from exc


def _map_http_error(exc: ProviderError, *, endpoint: str) -> ProviderError:
    if exc.status_code == httpx.codes.NOT_FOUND:
        return ProviderError(
            ToolErrorCode.NOT_FOUND,
            "FMP 没有这只股票",
            retryable=False,
            status_code=exc.status_code,
            provider="fmp",
            endpoint=endpoint,
        )
    if exc.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        return ProviderError(
            ToolErrorCode.UPSTREAM_ERROR,
            "FMP API key 无效或未获授权",
            retryable=False,
            status_code=exc.status_code,
            provider="fmp",
            endpoint=endpoint,
        )
    return exc


def _require_symbol(symbol: str, *, endpoint: str) -> str:
    ticker = symbol.strip().upper()
    if not ticker:
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            "股票代码为空",
            provider="fmp",
            endpoint=endpoint,
        )
    return ticker


def _raise_if_error_payload(data: Any, *, endpoint: str) -> None:
    message = _error_message(data)
    if message is None:
        return
    lower = message.lower()
    if "limit reach" in lower or "limit reached" in lower:
        code = ToolErrorCode.QUOTA_EXHAUSTED
        text = "FMP 当日免费额度已用尽"
    elif "invalid api" in lower:
        code = ToolErrorCode.UPSTREAM_ERROR
        text = "FMP API key 无效或未获授权"
    elif "premium" in lower or "upgrade your plan" in lower:
        code = ToolErrorCode.UNSUPPORTED
        text = "FMP 免费档不支持这个接口"
    else:
        code = ToolErrorCode.UPSTREAM_ERROR
        text = f"FMP 返回错误：{message}"
    raise ProviderError(
        code,
        text,
        retryable=False,
        provider="fmp",
        endpoint=endpoint,
    )


def _error_message(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("Error Message") or data.get("error")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _first_object(data: Any, *, endpoint: str, missing: str) -> dict[str, Any]:
    _raise_if_error_payload(data, endpoint=endpoint)
    row: object
    if isinstance(data, list):
        if not data:
            raise ProviderError(
                ToolErrorCode.NOT_FOUND,
                missing,
                retryable=False,
                provider="fmp",
                endpoint=endpoint,
            )
        row = data[0]
    elif isinstance(data, dict):
        row = data
    else:
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "FMP 返回了非预期结构",
            retryable=False,
            provider="fmp",
            endpoint=endpoint,
        )
    if not isinstance(row, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "FMP 返回了非预期结构（条目不是对象）",
            retryable=False,
            provider="fmp",
            endpoint=endpoint,
        )
    return row


def _parse_quote(response: ProviderResponse, *, symbol: str) -> StockQuote:
    row = _first_object(
        response.data,
        endpoint=_QUOTE_PATH,
        missing=f"FMP 没有这只股票：{symbol}",
    )
    price = _first_float(row, "price")
    if price is None:
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "FMP 未给出价格",
            retryable=False,
            provider=response.provider,
            endpoint=_QUOTE_PATH,
        )
    return StockQuote(
        symbol=_optional_str(row.get("symbol")) or symbol,
        name=_optional_str(row.get("name")),
        price=price,
        change=_first_float(row, "change"),
        change_pct=_first_float(row, "changePercentage", "changesPercentage"),
        volume=_first_float(row, "volume"),
        day_low=_first_float(row, "dayLow"),
        day_high=_first_float(row, "dayHigh"),
        year_low=_first_float(row, "yearLow"),
        year_high=_first_float(row, "yearHigh"),
        market_cap=_first_float(row, "marketCap"),
        open=_first_float(row, "open"),
        previous_close=_first_float(row, "previousClose"),
        pe=_first_float(row, "pe"),
        eps=_first_float(row, "eps"),
        exchange=_optional_str(row.get("exchange")),
        as_of=_from_unix(row.get("timestamp")),
        url=stock_page_url(symbol),
        provenance=response.provenance(),
    )


def _parse_profile(response: ProviderResponse, *, symbol: str) -> StockProfile:
    row = _first_object(
        response.data,
        endpoint=_PROFILE_PATH,
        missing=f"FMP 没有这只股票：{symbol}",
    )
    return StockProfile(
        symbol=_optional_str(row.get("symbol")) or symbol,
        name=_optional_str(row.get("companyName") or row.get("name")),
        description=_optional_str(row.get("description")),
        cik=_optional_str(row.get("cik")),
        exchange=_optional_str(row.get("exchangeShortName") or row.get("exchange")),
        industry=_optional_str(row.get("industry")),
        sector=_optional_str(row.get("sector")),
        country=_optional_str(row.get("country")),
        currency=_optional_str(row.get("currency")),
        website=_optional_str(row.get("website")),
        ceo=_optional_str(row.get("ceo")),
        ipo_date=_optional_date(row.get("ipoDate")),
        employees=_optional_int(row.get("fullTimeEmployees")),
        market_cap=_first_float(row, "marketCap", "mktCap"),
        beta=_first_float(row, "beta"),
        is_etf=_optional_bool(row.get("isEtf")),
        is_actively_trading=_optional_bool(row.get("isActivelyTrading")),
        url=stock_page_url(symbol),
        provenance=response.provenance(),
    )


def _parse_history(
    response: ProviderResponse,
    *,
    symbol: str,
    days: int,
    start: date,
    end: date,
) -> StockHistory:
    data = response.data
    _raise_if_error_payload(data, endpoint=_HISTORY_PATH)
    rows: list[Any]
    if isinstance(data, dict) and isinstance(data.get("historical"), list):
        rows = data["historical"]
    elif isinstance(data, list):
        rows = data
    else:
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "FMP 返回了非预期结构（历史价不是列表）",
            retryable=False,
            provider=response.provider,
            endpoint=_HISTORY_PATH,
        )
    if not rows:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"FMP 没有这只股票的历史价：{symbol}",
            retryable=False,
            provider=response.provider,
            endpoint=_HISTORY_PATH,
        )
    bars: list[PriceBar] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        session = _optional_date(item.get("date"))
        close = _first_float(item, "close", "adjClose")
        if session is None or close is None:
            continue
        bars.append(
            PriceBar(
                session=session,
                open=_first_float(item, "open"),
                high=_first_float(item, "high"),
                low=_first_float(item, "low"),
                close=close,
                volume=_first_float(item, "volume"),
            )
        )
    if not bars:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"FMP 没有这只股票的历史价：{symbol}",
            retryable=False,
            provider=response.provider,
            endpoint=_HISTORY_PATH,
        )
    bars.sort(key=lambda bar: bar.session)
    return StockHistory(
        symbol=symbol,
        days=days,
        start=start,
        end=end,
        bars=tuple(bars),
        url=stock_page_url(symbol),
        provenance=response.provenance(),
    )


def _parse_peers(response: ProviderResponse, *, symbol: str) -> StockPeers:
    data = response.data
    _raise_if_error_payload(data, endpoint=_PEERS_PATH)
    raw_peers: list[Any]
    if isinstance(data, dict) and isinstance(data.get("peersList"), list):
        raw_peers = data["peersList"]
    elif isinstance(data, list):
        if not data:
            raise ProviderError(
                ToolErrorCode.NOT_FOUND,
                f"FMP 没有这只股票：{symbol}",
                retryable=False,
                provider=response.provider,
                endpoint=_PEERS_PATH,
            )
        first = data[0]
        if isinstance(first, dict) and isinstance(first.get("peersList"), list):
            raw_peers = first["peersList"]
        else:
            raw_peers = data
    else:
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "FMP 返回了非预期结构（peers 不是列表）",
            retryable=False,
            provider=response.provider,
            endpoint=_PEERS_PATH,
        )
    peers: list[StockPeer] = []
    seen: set[str] = set()
    for item in raw_peers:
        peer_symbol: str | None
        name: str | None = None
        price: float | None = None
        market_cap: float | None = None
        if isinstance(item, str):
            peer_symbol = _optional_str(item)
        elif isinstance(item, dict):
            peer_symbol = _optional_str(item.get("symbol"))
            name = _optional_str(item.get("companyName") or item.get("name"))
            price = _first_float(item, "price")
            market_cap = _first_float(item, "marketCap", "mktCap")
        else:
            continue
        if peer_symbol is None:
            continue
        ticker = peer_symbol.upper()
        if ticker == symbol or ticker in seen:
            continue
        seen.add(ticker)
        peers.append(
            StockPeer(
                symbol=ticker,
                name=name,
                price=price,
                market_cap=market_cap,
                url=stock_page_url(ticker),
            )
        )
    return StockPeers(
        symbol=symbol,
        peers=tuple(peers),
        url=stock_page_url(symbol),
        provenance=response.provenance(),
    )


def _parse_ratios(response: ProviderResponse, *, symbol: str) -> ValuationRatios:
    row = _first_object(
        response.data,
        endpoint=_RATIOS_PATH,
        missing=f"FMP 没有这只股票的估值比率：{symbol}",
    )
    return ValuationRatios(
        symbol=_optional_str(row.get("symbol")) or symbol,
        pe=_first_float(row, "priceToEarningsRatioTTM", "peRatioTTM"),
        pb=_first_float(row, "priceToBookRatioTTM", "pbRatioTTM"),
        ps=_first_float(row, "priceToSalesRatioTTM", "priceSalesRatioTTM"),
        ev_ebitda=_first_float(
            row,
            "enterpriseValueMultipleTTM",
            "enterpriseValueOverEBITDATTM",
        ),
        dividend_yield=_first_float(
            row,
            "dividendYieldTTM",
            "dividendYielTTM",
            "dividendYieldPercentageTTM",
        ),
        url=stock_page_url(symbol),
        provenance=response.provenance(),
    )


def _first_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_float(row.get(key))
        if value is not None:
            return value
    return None


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
            return int(stripped)
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _optional_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _from_unix(value: object) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return None

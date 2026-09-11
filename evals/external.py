"""冻结的外部数据（P7-7）。

Eval 默认不打 CoinGecko / DefiLlama / FMP / SEC。数字和时间戳来自
`evals/fixtures/external.json`，同一数据集重复跑分数不变。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from agent_service.providers.crypto import CoinMarket, CoinPrice, CoinSearchPage, MarketChart
from agent_service.providers.defi import (
    ChainOverview,
    ChainTvl,
    DexVolume,
    FeesRevenue,
    ProtocolTvl,
    TvlPoint,
)
from agent_service.providers.equity import (
    BalanceSheets,
    CashFlowStatements,
    IncomeStatementRow,
    IncomeStatements,
    StockHistory,
    StockPeers,
    StockProfile,
    StockQuote,
    ValuationRatioHistory,
    ValuationRatios,
)
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import Clock
from agent_service.providers.sec import (
    CompanyFacts,
    CompanySubmissions,
    FilingDocument,
    TickerDirectory,
    TickerEntry,
    company_page_url,
)
from agent_service.schemas.tools import DataProvenance, ToolErrorCode, ToolResult
from agent_service.tools.crypto.models import CryptoMarketData, CryptoPriceData
from agent_service.tools.defi.models import DexVolumeData, FeesRevenueData, TvlData
from agent_service.tools.deps import ToolDeps
from agent_service.tools.financials.models import (
    GrowthMetricsData,
    IncomeStatementData,
    ValuationMetricsData,
)
from agent_service.tools.registry import invoke_tool
from agent_service.tools.stocks.models import StockQuoteData
from evals.paths import FIXTURES_DIR

_CATALOG_PATH = FIXTURES_DIR / "external.json"
_MISSING = "eval fixture 未收录，拒绝打实时 API"


def _parse_as_of(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


@lru_cache(maxsize=4)
def _read_catalog(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"fixture catalog 必须是对象：{path}"
        raise TypeError(msg)
    return payload


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    return _read_catalog(str(path or _CATALOG_PATH))


def frozen_clock(catalog: dict[str, Any] | None = None) -> Clock:
    blob = catalog or load_catalog()
    return Clock.frozen(_parse_as_of(str(blob.get("as_of") or "2026-09-11T00:00:00Z")))


def _as_of(catalog: dict[str, Any]) -> datetime:
    return frozen_clock(catalog).now()


def _prov(
    catalog: dict[str, Any], *, provider: str, endpoint: str, url: str = ""
) -> DataProvenance:
    return DataProvenance(
        provider=provider,
        endpoint=endpoint,
        source_url=url or None,
        retrieved_at=_as_of(catalog),
        is_cached=True,
        cache_age_s=0,
    )


def _missing(provider: str, endpoint: str, name: str) -> ProviderError:
    return ProviderError(
        ToolErrorCode.NOT_FOUND,
        f"{_MISSING}：{provider}:{name}",
        provider=provider,
        endpoint=endpoint,
    )


def page_for(url: str, catalog: dict[str, Any] | None = None) -> dict[str, Any] | None:
    pages = (catalog or load_catalog()).get("pages")
    if not isinstance(pages, dict):
        return None
    item = pages.get(url)
    return item if isinstance(item, dict) else None


def catalog_pages(catalog: dict[str, Any] | None = None) -> dict[str, str]:
    pages = (catalog or load_catalog()).get("pages")
    if not isinstance(pages, dict):
        return {}
    out: dict[str, str] = {}
    for url, item in pages.items():
        if isinstance(item, dict) and item.get("text"):
            out[str(url)] = str(item["text"])
    return out


class _CatalogCoinGecko:
    def __init__(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        coins = catalog.get("coins")
        self._coins: dict[str, Any] = coins if isinstance(coins, dict) else {}

    def _coin(self, coin_id: str) -> dict[str, Any]:
        row = self._coins.get(coin_id)
        if not isinstance(row, dict):
            raise _missing("coingecko", f"/coins/{coin_id}", coin_id)
        return row

    async def search_coins(self, query: str) -> CoinSearchPage:
        del query
        raise _missing("coingecko", "/search", "search")

    async def get_price(self, coin_id: str, *, vs_currency: str = "usd") -> CoinPrice:
        row = self._coin(coin_id)
        url = f"https://www.coingecko.com/en/coins/{coin_id}"
        return CoinPrice(
            coin_id=coin_id,
            vs_currency=vs_currency,
            price=float(row["price"]),
            market_cap=_opt_float(row.get("market_cap")),
            volume_24h=_opt_float(row.get("volume_24h")),
            change_24h_pct=_opt_float(row.get("change_24h_pct")),
            as_of=_as_of(self._catalog),
            url=url,
            provenance=_prov(
                self._catalog, provider="coingecko", endpoint="/simple/price", url=url
            ),
        )

    async def get_market(self, coin_id: str, *, vs_currency: str = "usd") -> CoinMarket:
        row = self._coin(coin_id)
        url = f"https://www.coingecko.com/en/coins/{coin_id}"
        return CoinMarket(
            coin_id=coin_id,
            symbol=str(row.get("symbol") or coin_id),
            name=str(row.get("name") or coin_id),
            vs_currency=vs_currency,
            current_price=_opt_float(row.get("price")),
            market_cap=_opt_float(row.get("market_cap")),
            fully_diluted_valuation=None,
            total_volume=None,
            circulating_supply=_opt_float(row.get("circulating_supply")),
            total_supply=None,
            max_supply=None,
            ath=None,
            ath_date=None,
            atl=None,
            atl_date=None,
            high_24h=None,
            low_24h=None,
            change_24h_pct=None,
            last_updated=_as_of(self._catalog),
            url=url,
            provenance=_prov(
                self._catalog,
                provider="coingecko",
                endpoint=f"/coins/{coin_id}",
                url=url,
            ),
        )

    async def get_market_chart(
        self, coin_id: str, *, days: int = 30, vs_currency: str = "usd"
    ) -> MarketChart:
        del days, vs_currency
        raise _missing("coingecko", f"/coins/{coin_id}/market_chart", coin_id)


class _CatalogDefiLlama:
    def __init__(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        protocols = catalog.get("protocols")
        self._protocols: dict[str, Any] = protocols if isinstance(protocols, dict) else {}
        chains = catalog.get("chains")
        chain_rows: dict[str, Any] = chains if isinstance(chains, dict) else {}
        self._chains = {str(name).casefold(): (str(name), row) for name, row in chain_rows.items()}

    def _protocol(self, slug: str) -> dict[str, Any]:
        row = self._protocols.get(slug)
        if not isinstance(row, dict):
            raise _missing("defillama", f"/protocol/{slug}", slug)
        return row

    def _chain(self, chain: str) -> tuple[str, dict[str, Any]]:
        hit = self._chains.get(chain.casefold())
        if hit is None or not isinstance(hit[1], dict):
            raise _missing("defillama", f"/v2/historicalChainTvl/{chain}", chain)
        return hit[0], hit[1]

    async def get_protocol_tvl(self, slug: str, *, days: int = 30) -> ProtocolTvl:
        del days
        row = self._protocol(slug)
        url = f"https://defillama.com/protocol/{slug}"
        tvl = float(row["tvl_usd"])
        as_of = _as_of(self._catalog)
        return ProtocolTvl(
            slug=slug,
            name=str(row.get("name") or slug),
            symbol=str(row["symbol"]) if row.get("symbol") else None,
            category=str(row["category"]) if row.get("category") else None,
            chains=("Hyperliquid",),
            tvl_usd=tvl,
            chain_tvls=(("Hyperliquid", tvl),),
            series=(TvlPoint(timestamp=as_of, tvl_usd=tvl),),
            url=url,
            provenance=_prov(
                self._catalog, provider="defillama", endpoint=f"/protocol/{slug}", url=url
            ),
        )

    async def get_chain_tvl(self, chain: str, *, days: int = 30) -> ChainTvl:
        del days
        name, row = self._chain(chain)
        url = f"https://defillama.com/chain/{name.lower()}"
        tvl = float(row["tvl_usd"])
        as_of = _as_of(self._catalog)
        return ChainTvl(
            chain=name,
            tvl_usd=tvl,
            series=(TvlPoint(timestamp=as_of, tvl_usd=tvl),),
            url=url,
            provenance=_prov(
                self._catalog,
                provider="defillama",
                endpoint=f"/v2/historicalChainTvl/{name}",
                url=url,
            ),
        )

    async def get_fees_revenue(self, slug: str) -> FeesRevenue:
        row = self._protocol(slug)
        url = f"https://defillama.com/protocol/{slug}"
        fees_30d = _opt_float(row.get("fees_30d"))
        return FeesRevenue(
            slug=slug,
            name=str(row.get("name") or slug),
            fees_24h=None,
            fees_7d=None,
            fees_30d=fees_30d,
            revenue_24h=None,
            revenue_7d=None,
            revenue_30d=None,
            url=url,
            provenance=_prov(
                self._catalog,
                provider="defillama",
                endpoint=f"/summary/fees/{slug}",
                url=url,
            ),
        )

    async def get_dex_volume(self, slug: str) -> DexVolume:
        row = self._protocol(slug)
        url = f"https://defillama.com/protocol/{slug}"
        return DexVolume(
            slug=slug,
            name=str(row.get("name") or slug),
            volume_24h=_opt_float(row.get("volume_24h")),
            volume_7d=None,
            volume_30d=_opt_float(row.get("volume_30d")),
            volume_all_time=None,
            change_1d=None,
            chains=("Hyperliquid",),
            url=url,
            provenance=_prov(
                self._catalog,
                provider="defillama",
                endpoint=f"/summary/dexs/{slug}",
                url=url,
            ),
        )

    async def get_chain_overview(self, chain: str) -> ChainOverview:
        name, row = self._chain(chain)
        url = f"https://defillama.com/chain/{name.lower()}"
        return ChainOverview(
            name=name,
            tvl_usd=_opt_float(row.get("tvl_usd")),
            token_symbol=None,
            gecko_id=None,
            chain_id=None,
            url=url,
            provenance=_prov(self._catalog, provider="defillama", endpoint="/v2/chains", url=url),
        )


class _CatalogSec:
    def __init__(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        stocks = catalog.get("stocks")
        stock_rows: dict[str, Any] = stocks if isinstance(stocks, dict) else {}
        entries: list[TickerEntry] = []
        for ticker, row in stock_rows.items():
            if not isinstance(row, dict):
                continue
            cik = str(row.get("cik") or "")
            url = company_page_url(cik) if cik else ""
            entries.append(
                TickerEntry(
                    cik=cik,
                    ticker=str(ticker),
                    title=str(row.get("title") or ticker),
                    url=url,
                )
            )
        self._directory = TickerDirectory(
            entries=tuple(entries),
            url="https://www.sec.gov/files/company_tickers.json",
            provenance=_prov(
                catalog,
                provider="sec_edgar",
                endpoint="/files/company_tickers.json",
                url="https://www.sec.gov/files/company_tickers.json",
            ),
        )

    async def get_ticker_directory(self) -> TickerDirectory:
        return self._directory

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        url = company_page_url(cik)
        return CompanyFacts(
            cik=cik,
            name=None,
            concepts=MappingProxyType({}),
            url=url,
            provenance=_prov(
                self._catalog,
                provider="sec_edgar",
                endpoint="/api/xbrl/companyfacts",
                url=url,
            ),
        )

    async def get_submissions(self, cik: str) -> CompanySubmissions:
        raise _missing("sec_edgar", "/submissions", cik)

    async def get_filing_document(
        self, *, cik: str, accession: str, primary_document: str
    ) -> FilingDocument:
        del accession, primary_document
        raise _missing("sec_edgar", "/Archives", cik)


class _CatalogFmp:
    def __init__(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        stocks = catalog.get("stocks")
        self._stocks: dict[str, Any] = stocks if isinstance(stocks, dict) else {}

    def _stock(self, symbol: str) -> dict[str, Any]:
        row = self._stocks.get(symbol.upper())
        if not isinstance(row, dict):
            raise _missing("fmp", f"/quote/{symbol}", symbol)
        return row

    async def get_quote(self, symbol: str) -> StockQuote:
        row = self._stock(symbol)
        url = f"https://financialmodelingprep.com/financial-summary/{symbol.upper()}"
        return StockQuote(
            symbol=symbol.upper(),
            name=str(row.get("title") or symbol),
            price=float(row.get("price") or 0.0),
            change=None,
            change_pct=None,
            volume=None,
            day_low=None,
            day_high=None,
            year_low=None,
            year_high=None,
            market_cap=_opt_float(row.get("market_cap")),
            open=None,
            previous_close=None,
            pe=_opt_float(row.get("pe")),
            eps=None,
            exchange=None,
            as_of=_as_of(self._catalog),
            url=url,
            provenance=_prov(self._catalog, provider="fmp", endpoint="/quote", url=url),
        )

    async def get_profile(self, symbol: str) -> StockProfile:
        raise _missing("fmp", "/profile", symbol)

    async def get_historical_prices(self, symbol: str, *, days: int = 30) -> StockHistory:
        del days
        raise _missing("fmp", "/historical-price-eod/full", symbol)

    async def get_peers(self, symbol: str) -> StockPeers:
        raise _missing("fmp", "/stock-peers", symbol)

    async def get_income_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> IncomeStatements:
        row = self._stock(symbol)
        key = "income_annual" if period == "annual" else "income_quarterly"
        raw = row.get(key)
        items = raw if isinstance(raw, list) else []
        rows = tuple(_income_row(item) for item in items[:limit] if isinstance(item, dict))
        url = f"https://financialmodelingprep.com/financial-summary/{symbol.upper()}"
        return IncomeStatements(
            symbol=symbol.upper(),
            period=period,
            rows=rows,
            url=url,
            provenance=_prov(self._catalog, provider="fmp", endpoint="/income-statement", url=url),
        )

    async def get_balance_sheets(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> BalanceSheets:
        del period, limit
        raise _missing("fmp", "/balance-sheet-statement", symbol)

    async def get_cash_flow_statements(
        self, symbol: str, *, period: str = "annual", limit: int = 4
    ) -> CashFlowStatements:
        del period, limit
        raise _missing("fmp", "/cash-flow-statement", symbol)

    async def get_ratios_ttm(self, symbol: str) -> ValuationRatios:
        row = self._stock(symbol)
        url = f"https://financialmodelingprep.com/financial-summary/{symbol.upper()}"
        return ValuationRatios(
            symbol=symbol.upper(),
            pe=_opt_float(row.get("pe")),
            pb=_opt_float(row.get("pb")),
            ps=_opt_float(row.get("ps")),
            ev_ebitda=None,
            dividend_yield=None,
            url=url,
            provenance=_prov(self._catalog, provider="fmp", endpoint="/ratios-ttm", url=url),
        )

    async def get_ratios(
        self, symbol: str, *, period: str = "quarterly", limit: int = 20
    ) -> ValuationRatioHistory:
        del period, limit
        raise _missing("fmp", "/ratios", symbol)


def _income_row(item: dict[str, Any]) -> IncomeStatementRow:
    period_end = date.fromisoformat(str(item["period_end"]))
    return IncomeStatementRow(
        period_end=period_end,
        fiscal_year=int(item["fiscal_year"]) if item.get("fiscal_year") is not None else None,
        fiscal_period=str(item["fiscal_period"]) if item.get("fiscal_period") else None,
        revenue=_opt_float(item.get("revenue")),
        cost_of_revenue=_opt_float(item.get("cost_of_revenue")),
        gross_profit=_opt_float(item.get("gross_profit")),
        operating_income=_opt_float(item.get("operating_income")),
        net_income=_opt_float(item.get("net_income")),
        eps_basic=_opt_float(item.get("eps_basic")),
        eps_diluted=_opt_float(item.get("eps_diluted")),
        research_and_development=None,
        operating_expenses=None,
        income_tax=None,
        shares_diluted=None,
    )


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def fixture_deps(catalog: dict[str, Any] | None = None) -> ToolDeps:
    blob = catalog or load_catalog()
    return ToolDeps(
        coingecko=_CatalogCoinGecko(blob),
        defillama=_CatalogDefiLlama(blob),
        sec_edgar=_CatalogSec(blob),
        fmp=_CatalogFmp(blob),
        clock=frozen_clock(blob),
    )


def metrics_from_tool(name: str, data: object, *, entity: str = "") -> list[dict[str, Any]]:
    points: list[tuple[str, float | None]] = []
    if isinstance(data, TvlData):
        key = "tvl_usd" if data.scope == "protocol" else "chain_tvl_usd"
        points.append((key, data.tvl_usd))
    elif isinstance(data, CryptoPriceData):
        points.append(("price_usd", data.price))
        points.append(("market_cap_usd", data.market_cap))
    elif isinstance(data, CryptoMarketData):
        points.append(("market_cap_usd", data.market_cap))
        points.append(("circulating_supply", data.circulating_supply))
    elif isinstance(data, ValuationMetricsData):
        points.append(("pe_ttm", data.pe))
        points.append(("ps_ttm", data.ps))
    elif isinstance(data, StockQuoteData):
        points.append(("market_cap_usd", data.market_cap))
        points.append(("pe_ttm", data.pe))
    elif isinstance(data, IncomeStatementData) and data.rows:
        row = data.rows[0]
        points.extend(
            [
                ("revenue_usd", row.revenue),
                ("gross_profit_usd", row.gross_profit),
                ("eps_usd", row.eps_diluted),
                ("operating_income_usd", row.operating_income),
            ]
        )
    elif isinstance(data, FeesRevenueData) and data.fees_30d is not None:
        points.append(("fees_annualized_usd", data.fees_30d * 12.0))
    elif isinstance(data, DexVolumeData):
        points.append(("dex_volume_30d_usd", data.volume_30d))
        points.append(("dex_volume_24h_usd", data.volume_24h))
    elif isinstance(data, GrowthMetricsData):
        points.extend(
            [
                ("gross_margin", data.gross_margin),
                ("operating_margin", data.operating_margin),
                ("revenue_yoy", data.revenue_yoy),
            ]
        )
    del name
    out: list[dict[str, Any]] = []
    for metric_name, value in points:
        if value is None:
            continue
        item: dict[str, Any] = {"name": metric_name, "value": float(value)}
        if entity:
            item["entity"] = entity
        out.append(item)
    return out


async def replay_calls(
    calls: list[dict[str, Any]],
    *,
    deps: ToolDeps | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    runtime = deps or fixture_deps()
    metrics: list[dict[str, Any]] = []
    names: list[str] = []
    for call in calls:
        name = str(call.get("name") or "")
        raw_args = call.get("arguments")
        arguments: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        entity = str(call.get("entity") or "")
        result = await invoke_tool(name, arguments, runtime)
        if not isinstance(result, ToolResult) or not result.ok:
            message = result.error.message if result.error is not None else "tool 失败"
            raise RuntimeError(f"{name}: {message}")
        names.append(name)
        metrics.extend(metrics_from_tool(name, result.data, entity=entity))
    return metrics, names


def attach_pages(
    observation: dict[str, Any], catalog: dict[str, Any] | None = None
) -> dict[str, Any]:
    blob = catalog or load_catalog()
    pages = catalog_pages(blob)
    payload = dict(observation)
    sources = []
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            continue
        item = dict(source)
        url = str(item.get("url") or "")
        recorded = page_for(url, blob)
        if recorded:
            if not item.get("page_text"):
                item["page_text"] = recorded.get("text")
            if item.get("http_status") is None:
                item["http_status"] = recorded.get("http_status", 200)
        sources.append(item)
    if sources:
        payload["sources"] = sources
    payload["pages"] = pages
    return payload

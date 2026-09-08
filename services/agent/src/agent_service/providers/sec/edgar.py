"""SEC EDGAR 客户端（P4-2）。

无需 API key，但 Fair Access 强制 `User-Agent` 声明身份（应用名 + 联系邮箱），
否则 403 封 IP（§17.2）。限流/缓存/429 走 `BaseProvider`；profile 已按 10 req/s
钉死。本文件只负责路径、JSON 形状，以及「没有这个 CIK」的错误映射。

三个方法是给后面任务用的原语，不要在 tool 层再包一遍 HTTP：

- `get_ticker_directory` → **P4-3** `resolve_ticker`（本地缓存 `company_tickers.json`）
- `get_submissions` → **P4-8** `list_sec_filings`（以及归档 URL）
- `get_company_facts` → **P4-5** 三表 XBRL / **P4-8** `get_xbrl_facts`
- `get_filing_document` → **P4-8** `get_filing_section`（HTML 按 accession 永久缓存）

`get_submissions` / `get_company_facts` / `get_filing_document` 只收 CIK。
HTML 按 accession 永久缓存；注入侧分节截取在 tool 层。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any

import httpx

from agent_service.providers.base import BaseProvider, ProviderRequest, ProviderResponse
from agent_service.providers.errors import ProviderError
from agent_service.providers.runtime import ProviderRuntime
from agent_service.providers.ttl import CacheTTL
from agent_service.schemas.tools import DataProvenance, ToolErrorCode

_SEC_BASE = "https://data.sec.gov"
_PROVIDER = "sec_edgar"
_CIK_WIDTH = 10
_TICKERS_ENDPOINT = "/files/company_tickers.json"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEARCH_PAGE = "https://www.sec.gov/search-filings"


def padded_cik(cik: str, *, endpoint: str = "/submissions") -> str:
    """SEC 路径要求 10 位数字，左侧补零。不接受股票代码。"""
    raw = cik.strip().upper()
    if raw.startswith("CIK"):
        raw = raw[3:]
    raw = raw.strip().lstrip("0") or "0"
    if not raw.isdigit() or raw == "0":
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            "CIK 必须是数字（不要传股票代码；ticker→CIK 是 P4-3）",
            provider=_PROVIDER,
            endpoint=endpoint,
        )
    if len(raw) > _CIK_WIDTH:
        raise ProviderError(
            ToolErrorCode.INVALID_INPUT,
            "CIK 超过 10 位",
            provider=_PROVIDER,
            endpoint=endpoint,
        )
    return raw.zfill(_CIK_WIDTH)


def company_page_url(cik: str) -> str:
    """给人点的公司页面，不是 JSON API。"""
    return f"https://www.sec.gov/edgar/browse/?CIK={padded_cik(cik)}"


def filing_document_url(*, cik: str, accession: str, primary_document: str | None) -> str:
    """EDGAR 归档上的主文档。accession 在路径里要去掉连字符。"""
    numeric = str(int(padded_cik(cik, endpoint="/archives")))
    accn = accession.replace("-", "").strip()
    base = f"https://www.sec.gov/Archives/edgar/data/{numeric}/{accn}"
    if primary_document and primary_document.strip():
        return f"{base}/{primary_document.strip()}"
    return f"{base}/"


def _submissions_path(cik10: str) -> str:
    return f"/submissions/CIK{cik10}.json"


def _facts_path(cik10: str) -> str:
    return f"/api/xbrl/companyfacts/CIK{cik10}.json"


@dataclass(frozen=True, slots=True)
class FilingRef:
    accession: str
    form: str
    filed: date | None
    report_date: date | None
    accepted: datetime | None
    primary_document: str | None
    description: str | None
    is_xbrl: bool | None
    url: str


@dataclass(frozen=True, slots=True)
class CompanySubmissions:
    cik: str
    name: str | None
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    sic: str | None
    sic_description: str | None
    fiscal_year_end: str | None
    state_of_incorporation: str | None
    filings: tuple[FilingRef, ...]
    url: str
    provenance: DataProvenance

    def filings_of(self, *forms: str) -> tuple[FilingRef, ...]:
        wanted = {item.strip().upper() for item in forms if item.strip()}
        if not wanted:
            return self.filings
        return tuple(item for item in self.filings if item.form.upper() in wanted)


@dataclass(frozen=True, slots=True)
class FactPoint:
    value: float
    unit: str
    end: date | None
    start: date | None
    filed: date | None
    form: str | None
    fy: int | None
    fp: str | None
    accession: str | None
    frame: str | None


@dataclass(frozen=True, slots=True)
class FactConcept:
    taxonomy: str
    tag: str
    label: str | None
    description: str | None
    points: tuple[FactPoint, ...]


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    cik: str
    name: str | None
    concepts: Mapping[tuple[str, str], FactConcept]
    url: str
    provenance: DataProvenance

    def concept(self, tag: str, *, taxonomy: str = "us-gaap") -> FactConcept | None:
        return self.concepts.get((taxonomy, tag))


@dataclass(frozen=True, slots=True)
class FilingDocument:
    cik: str
    accession: str
    primary_document: str
    html: str
    url: str
    provenance: DataProvenance


@dataclass(frozen=True, slots=True)
class TickerEntry:
    cik: str
    ticker: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class TickerDirectory:
    entries: tuple[TickerEntry, ...]
    url: str
    provenance: DataProvenance


class SecEdgarProvider(BaseProvider):
    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        user_agent: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = _SEC_BASE,
    ) -> None:
        ua = user_agent.strip()
        if not ua or "@" not in ua:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                "SEC User-Agent 必须包含应用名和联系邮箱",
                provider=_PROVIDER,
            )
        super().__init__(
            name=_PROVIDER,
            base_url=base_url,
            runtime=runtime,
            headers={"Accept": "application/json", "User-Agent": ua},
            client=client,
        )

    async def get_submissions(self, cik: str) -> CompanySubmissions:
        cik10 = padded_cik(cik, endpoint="/submissions")
        path = _submissions_path(cik10)
        response = await self._get(path, CacheTTL.PROFILE)
        return _parse_submissions(response, cik10=cik10)

    async def get_company_facts(self, cik: str) -> CompanyFacts:
        cik10 = padded_cik(cik, endpoint="/api/xbrl/companyfacts")
        path = _facts_path(cik10)
        response = await self._get(path, CacheTTL.PROFILE)
        return _parse_facts(response, cik10=cik10)

    async def get_filing_document(
        self, *, cik: str, accession: str, primary_document: str
    ) -> FilingDocument:
        cik10 = padded_cik(cik, endpoint="/archives")
        doc = primary_document.strip()
        accn = accession.strip()
        if not accn or not doc:
            raise ProviderError(
                ToolErrorCode.INVALID_INPUT,
                "accession 与 primary_document 不能为空",
                provider=_PROVIDER,
                endpoint="/archives",
            )
        url = filing_document_url(cik=cik10, accession=accn, primary_document=doc)
        endpoint = f"/archives/{accn.replace('-', '')}/{doc}"
        try:
            response = await self.request(
                ProviderRequest(
                    endpoint=endpoint,
                    path=url,
                    ttl=CacheTTL.PERMANENT,
                    as_text=True,
                    headers={"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"},
                )
            )
        except ProviderError as exc:
            raise _map_http_error(exc, endpoint=endpoint) from exc
        html = response.data if isinstance(response.data, str) else ""
        if not html.strip():
            raise ProviderError(
                ToolErrorCode.NOT_FOUND,
                f"SEC 没有这份文档：{accn}",
                retryable=False,
                provider=_PROVIDER,
                endpoint=endpoint,
            )
        return FilingDocument(
            cik=cik10,
            accession=accn,
            primary_document=doc,
            html=html,
            url=url,
            provenance=response.provenance(),
        )

    async def get_ticker_directory(self) -> TickerDirectory:
        response = await self._get(_TICKERS_ENDPOINT, CacheTTL.PROFILE, path=_TICKERS_URL)
        return _parse_tickers(response)

    async def _get(
        self, endpoint: str, ttl: CacheTTL, *, path: str | None = None
    ) -> ProviderResponse:
        try:
            if path is None:
                return await self.get_json(endpoint, ttl=ttl)
            return await self.request(ProviderRequest(endpoint=endpoint, path=path, ttl=ttl))
        except ProviderError as exc:
            raise _map_http_error(exc, endpoint=endpoint) from exc


def _map_http_error(exc: ProviderError, *, endpoint: str) -> ProviderError:
    if exc.status_code == httpx.codes.NOT_FOUND:
        missing = "SEC 没有这份文档" if endpoint.startswith("/archives") else "SEC 没有这个 CIK"
        return ProviderError(
            ToolErrorCode.NOT_FOUND,
            missing,
            retryable=False,
            status_code=exc.status_code,
            provider=_PROVIDER,
            endpoint=endpoint,
        )
    if exc.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        return ProviderError(
            ToolErrorCode.UPSTREAM_ERROR,
            "SEC 拒绝了请求（检查 User-Agent 是否含联系邮箱）",
            retryable=False,
            status_code=exc.status_code,
            provider=_PROVIDER,
            endpoint=endpoint,
        )
    return exc


def _parse_submissions(response: ProviderResponse, *, cik10: str) -> CompanySubmissions:
    data = response.data
    if not isinstance(data, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "SEC submissions 返回了非对象",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    cik = _cik_str(data.get("cik")) or cik10
    name = _optional_str(data.get("name"))
    if name is None and data.get("tickers") is None and data.get("filings") is None:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"SEC 没有这个 CIK：{cik10}",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    filings = _parse_recent_filings(data.get("filings"), cik=cik)
    return CompanySubmissions(
        cik=cik,
        name=name,
        tickers=_str_tuple(data.get("tickers")),
        exchanges=_str_tuple(data.get("exchanges")),
        sic=_optional_str(data.get("sic")),
        sic_description=_optional_str(data.get("sicDescription")),
        fiscal_year_end=_optional_str(data.get("fiscalYearEnd")),
        state_of_incorporation=_optional_str(data.get("stateOfIncorporation")),
        filings=filings,
        url=company_page_url(cik),
        provenance=response.provenance(),
    )


def _parse_recent_filings(filings: object, *, cik: str) -> tuple[FilingRef, ...]:
    if not isinstance(filings, dict):
        return ()
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return ()
    accessions = recent.get("accessionNumber")
    if not isinstance(accessions, list) or not accessions:
        return ()
    n = len(accessions)
    forms = _col(recent, "form", n)
    filed = _col(recent, "filingDate", n)
    report = _col(recent, "reportDate", n)
    accepted = _col(recent, "acceptanceDateTime", n)
    docs = _col(recent, "primaryDocument", n)
    desc = _col(recent, "primaryDocDescription", n)
    xbrl = _col(recent, "isXBRL", n)
    out: list[FilingRef] = []
    for i, raw_accn in enumerate(accessions):
        accession = _optional_str(raw_accn)
        if accession is None:
            continue
        primary = _optional_str(docs[i])
        form = _optional_str(forms[i]) or ""
        out.append(
            FilingRef(
                accession=accession,
                form=form,
                filed=_optional_date(filed[i]),
                report_date=_optional_date(report[i]),
                accepted=_optional_datetime(accepted[i]),
                primary_document=primary,
                description=_optional_str(desc[i]),
                is_xbrl=_optional_bool(xbrl[i]),
                url=filing_document_url(cik=cik, accession=accession, primary_document=primary),
            )
        )
    return tuple(out)


def _parse_facts(response: ProviderResponse, *, cik10: str) -> CompanyFacts:
    data = response.data
    if not isinstance(data, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "SEC companyfacts 返回了非对象",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    raw_facts = data.get("facts")
    if raw_facts is None:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            f"SEC 没有这个 CIK 的 XBRL：{cik10}",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    if not isinstance(raw_facts, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "SEC companyfacts 缺少 facts 对象",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    concepts: dict[tuple[str, str], FactConcept] = {}
    for taxonomy, tags in raw_facts.items():
        if not isinstance(taxonomy, str) or not isinstance(tags, dict):
            continue
        for tag, body in tags.items():
            if not isinstance(tag, str) or not isinstance(body, dict):
                continue
            points = _parse_points(body.get("units"))
            concepts[(taxonomy, tag)] = FactConcept(
                taxonomy=taxonomy,
                tag=tag,
                label=_optional_str(body.get("label")),
                description=_optional_str(body.get("description")),
                points=points,
            )
    cik = _cik_str(data.get("cik")) or cik10
    return CompanyFacts(
        cik=cik,
        name=_optional_str(data.get("entityName")),
        concepts=MappingProxyType(concepts),
        url=company_page_url(cik),
        provenance=response.provenance(),
    )


def _parse_tickers(response: ProviderResponse) -> TickerDirectory:
    data = response.data
    if not isinstance(data, dict):
        raise ProviderError(
            ToolErrorCode.PARSE_ERROR,
            "SEC company_tickers 返回了非对象",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    entries: list[TickerEntry] = []
    seen: set[str] = set()
    for item in data.values():
        if not isinstance(item, dict):
            continue
        cik = _cik_str(item.get("cik_str") if item.get("cik_str") is not None else item.get("cik"))
        ticker = _optional_str(item.get("ticker"))
        title = _optional_str(item.get("title") or item.get("name"))
        if cik is None or ticker is None:
            continue
        key = ticker.upper()
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            TickerEntry(
                cik=cik,
                ticker=ticker.upper(),
                title=title or ticker.upper(),
                url=company_page_url(cik),
            )
        )
    if not entries:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            "SEC 没有公司代码表",
            retryable=False,
            provider=response.provider,
            endpoint=response.endpoint,
        )
    return TickerDirectory(
        entries=tuple(entries),
        url=_SEARCH_PAGE,
        provenance=response.provenance(),
    )


def _parse_points(units: object) -> tuple[FactPoint, ...]:
    if not isinstance(units, dict):
        return ()
    points: list[FactPoint] = []
    for unit, rows in units.items():
        if not isinstance(unit, str) or not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = _optional_float(row.get("val"))
            if value is None:
                continue
            points.append(
                FactPoint(
                    value=value,
                    unit=unit,
                    end=_optional_date(row.get("end")),
                    start=_optional_date(row.get("start")),
                    filed=_optional_date(row.get("filed")),
                    form=_optional_str(row.get("form")),
                    fy=_optional_int(row.get("fy")),
                    fp=_optional_str(row.get("fp")),
                    accession=_optional_str(row.get("accn")),
                    frame=_optional_str(row.get("frame")),
                )
            )
    return tuple(points)


def _col(recent: dict[str, Any], key: str, n: int) -> list[Any]:
    raw = recent.get(key)
    if isinstance(raw, list) and len(raw) == n:
        return raw
    return [None] * n


def _cik_str(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        if value <= 0:
            return None
        return str(value).zfill(_CIK_WIDTH)
    if isinstance(value, str) and value.strip().lstrip("0").isdigit():
        digits = value.strip().lstrip("0") or "0"
        if digits == "0":
            return None
        return digits.zfill(_CIK_WIDTH)
    return None


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        text = _optional_str(item)
        if text is not None:
            out.append(text)
    return tuple(out)


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
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
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


def _optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed

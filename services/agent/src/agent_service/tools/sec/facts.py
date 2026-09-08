"""get_xbrl_facts：按 tag 过滤 companyfacts。缺 tag 保持 None，不要当成 0。"""

from __future__ import annotations

from datetime import date

from agent_service.providers.errors import ProviderError
from agent_service.providers.sec import FactConcept, FactPoint
from agent_service.schemas.tools import DataQuality, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider
from agent_service.tools.deps import ToolDeps
from agent_service.tools.sec.filings import _client, _page_provenance, resolve_company
from agent_service.tools.sec.models import XbrlConceptData, XbrlFactPointData, XbrlFactsData
from agent_service.tools.stocks.resolve import normalize_ticker

_TOOL = "get_xbrl_facts"
_BLANK = "ticker 为空，请先用 resolve_ticker 拿到代号"
_EMPTY = "concepts 不能为空，例如 us-gaap:Revenues"
_MAX_CONCEPTS = 16
_MAX_POINTS = 8
_DEFAULT_TAXONOMY = "us-gaap"


async def run_get_xbrl_facts(
    deps: ToolDeps, *, ticker: str, concepts: list[str]
) -> ToolResult[XbrlFactsData]:
    symbol = normalize_ticker(ticker)
    if not symbol:
        return fail_invalid(_TOOL, _BLANK)
    wanted = _concepts(concepts)
    if isinstance(wanted, ToolResult):
        return wanted
    sec = _client(deps, _TOOL)
    if isinstance(sec, ToolResult):
        return sec
    identity = await resolve_company(deps, symbol, _TOOL)
    if isinstance(identity, ToolResult):
        return identity
    try:
        facts = await sec.get_company_facts(identity.cik)
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    rows: list[XbrlConceptData] = []
    missing: list[str] = []
    for taxonomy, tag in wanted:
        concept = facts.concepts.get((taxonomy, tag))
        row = _row(taxonomy, tag, concept)
        rows.append(row)
        if row.latest is None:
            missing.append(f"{taxonomy}:{tag}")
    return ToolResult.success(
        XbrlFactsData(
            ticker=identity.ticker,
            cik=identity.cik,
            concepts=rows,
            url=facts.url,
        ),
        _page_provenance(facts.provenance, facts.url),
        quality=_quality(missing),
    )


def _concepts[T](raw: list[str]) -> tuple[tuple[str, str], ...] | ToolResult[T]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for item in raw:
        parsed = _parse_concept(item)
        if parsed is None or parsed in seen:
            continue
        seen.add(parsed)
        out.append(parsed)
        if len(out) >= _MAX_CONCEPTS:
            break
    if not out:
        return fail_invalid(_TOOL, _EMPTY)
    return tuple(out)


def _parse_concept(raw: str) -> tuple[str, str] | None:
    text = raw.strip()
    if not text:
        return None
    if ":" in text:
        taxonomy, tag = text.split(":", 1)
        taxonomy = taxonomy.strip().lower()
        tag = tag.strip()
        if not taxonomy or not tag:
            return None
        return taxonomy, tag
    return _DEFAULT_TAXONOMY, text


def _row(taxonomy: str, tag: str, concept: FactConcept | None) -> XbrlConceptData:
    if concept is None:
        return XbrlConceptData(taxonomy=taxonomy, tag=tag)
    points = _pick(concept.points)
    latest = _point(points[0]) if points else None
    history = [_point(item) for item in points[1:]]
    return XbrlConceptData(
        taxonomy=taxonomy,
        tag=tag,
        label=concept.label,
        latest=latest,
        history=history,
    )


def _pick(points: tuple[FactPoint, ...]) -> tuple[FactPoint, ...]:
    ordered = sorted(points, key=_sort_key, reverse=True)
    seen: set[tuple[date | None, str | None, str | None, float]] = set()
    unique: list[FactPoint] = []
    for point in ordered:
        key = (point.end, point.form, point.fp, point.value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(point)
        if len(unique) >= _MAX_POINTS:
            break
    return tuple(unique)


def _sort_key(point: FactPoint) -> tuple[date, date]:
    return (point.filed or date.min, point.end or date.min)


def _point(point: FactPoint) -> XbrlFactPointData:
    return XbrlFactPointData(
        value=point.value,
        unit=point.unit,
        end=point.end,
        start=point.start,
        filed=point.filed,
        form=point.form,
        fiscal_year=point.fy,
        fiscal_period=point.fp,
        accession=point.accession,
    )


def _quality(missing: list[str]) -> DataQuality | None:
    if not missing:
        return None
    return DataQuality(completeness="partial", missing_fields=missing)

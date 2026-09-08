"""从 10-K / 10-Q HTML 抽出指定 Item。完整分节是 P4-9。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from agent_service.providers.errors import ProviderError
from agent_service.providers.sec import FilingDocument, FilingRef
from agent_service.schemas.tools import DataQuality, ToolError, ToolErrorCode, ToolResult
from agent_service.tools._result import fail_invalid, fail_provider, fail_unavailable
from agent_service.tools.deps import ToolDeps
from agent_service.tools.sec.filings import _page_provenance
from agent_service.tools.sec.models import FilingSectionData

_TOOL = "get_filing_section"
_MAX_SECTION = 12_000
_MIN_BODY = 40
_SECTION_HELP = "section 只支持 Item 1 / 1A / 2 / 7 / 7A / 8（或 mda / risk_factors）"
_TRUNCATE = "正文已截断到 12000 字符，完整分节是后续任务"
_ACCESSION = re.compile(r"^(\d{10})-(\d{2})-(\d{6})$")
_COMPACT = re.compile(r"^\d{18}$")
_BLOCK = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BR = re.compile(r"(?i)<br\s*/?>")
_CLOSE = re.compile(r"(?i)</(?:p|div|tr|h[1-6]|li|table|section|article|header)>")
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[^\S\n]+")
_NL = re.compile(r" *\n *")
_BLANK_LINES = re.compile(r"\n{3,}")
_NEXT_ITEM = re.compile(r"(?im)(?:^|\n)\s*item\s+\d+[a-z]?\b\s*[.\-—:]?")
_ALIASES = {
    "1a": "1A",
    "risk": "1A",
    "riskfactors": "1A",
    "7": "7",
    "mda": "7",
    "7a": "7A",
    "2": "2",
    "1": "1",
    "8": "8",
}


@dataclass(frozen=True, slots=True)
class ExtractedItem:
    code: str
    heading: str
    text: str


def normalize_section(raw: str) -> str | None:
    key = raw.strip().casefold()
    for ch in " ._-/":
        key = key.replace(ch, "")
    key = key.replace("&", "")
    if key.startswith("item"):
        key = key[4:]
    return _ALIASES.get(key)


def html_to_text(raw: str) -> str:
    text = _BLOCK.sub("\n", raw)
    text = _BR.sub("\n", text)
    text = _CLOSE.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACES.sub(" ", text)
    text = _NL.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def extract_item(text: str, code: str) -> ExtractedItem | None:
    pattern = _item_pattern(code)
    best: ExtractedItem | None = None
    for match in pattern.finditer(text):
        nxt = _NEXT_ITEM.search(text, match.end())
        chunk = text[match.start() : nxt.start() if nxt else len(text)].strip()
        if not chunk:
            continue
        heading = chunk.split("\n", 1)[0].strip()
        item = ExtractedItem(code=code, heading=heading, text=chunk)
        if len(chunk) >= _MIN_BODY:
            return item
        if best is None or len(chunk) > len(best.text):
            best = item
    return best


async def run_get_filing_section(
    deps: ToolDeps, *, accession: str, section: str
) -> ToolResult[FilingSectionData]:
    parsed = _parse_args(accession, section)
    if isinstance(parsed, ToolResult):
        return parsed
    accn, code = parsed
    loaded = await _load_document(deps, accn)
    if isinstance(loaded, ToolResult):
        return loaded
    filing, document = loaded
    extracted = extract_item(html_to_text(document.html), code)
    if extracted is None:
        return _not_found(f"没有抽出 Item {code}")
    truncated = len(extracted.text) > _MAX_SECTION
    body = extracted.text[:_MAX_SECTION]
    caveats = [_TRUNCATE] if truncated else []
    return ToolResult.success(
        FilingSectionData(
            accession=accn,
            form=filing.form or None,
            section=code,
            heading=extracted.heading or None,
            text=body,
            truncated=truncated,
            url=document.url,
        ),
        _page_provenance(document.provenance, document.url),
        quality=_quality(caveats),
    )


def _parse_args(accession: str, section: str) -> tuple[str, str] | ToolResult[FilingSectionData]:
    accn = _accession(accession)
    if accn is None:
        return fail_invalid(_TOOL, "accession 格式应为 0001045810-25-000031")
    code = normalize_section(section)
    if code is None:
        return fail_invalid(_TOOL, _SECTION_HELP)
    return accn, code


async def _load_document(
    deps: ToolDeps, accn: str
) -> tuple[FilingRef, FilingDocument] | ToolResult[FilingSectionData]:
    if deps.sec_edgar is None:
        return fail_unavailable(tool=_TOOL, provider="sec_edgar", message="SEC EDGAR 未初始化")
    cik = accn[:10]
    try:
        page = await deps.sec_edgar.get_submissions(cik)
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    filing = _find(page.filings, accn)
    if filing is None:
        return _not_found(f"recent filings 里没有 {accn}，请先用 list_sec_filings")
    primary = (filing.primary_document or "").strip()
    if not primary:
        return _not_found(f"这份 filing 没有主文档：{accn}")
    try:
        document = await deps.sec_edgar.get_filing_document(
            cik=cik, accession=accn, primary_document=primary
        )
    except ProviderError as exc:
        return fail_provider(_TOOL, exc)
    return filing, document


def _not_found(message: str) -> ToolResult[FilingSectionData]:
    return ToolResult.failure(
        ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message=message,
            tool=_TOOL,
            provider="sec_edgar",
            retryable=False,
        )
    )


def _accession(raw: str) -> str | None:
    value = raw.strip()
    if _ACCESSION.fullmatch(value):
        return value
    if _COMPACT.fullmatch(value):
        return f"{value[:10]}-{value[10:12]}-{value[12:]}"
    return None


def _find(filings: tuple[FilingRef, ...], accession: str) -> FilingRef | None:
    for row in filings:
        if row.accession == accession:
            return row
    return None


def _item_pattern(code: str) -> re.Pattern[str]:
    token = re.escape(code) if code[-1].isalpha() else rf"{re.escape(code)}(?![a-z])"
    return re.compile(rf"(?im)(?:^|\n)\s*item\s+{token}\b\s*[.\-—:]?")


def _quality(caveats: list[str]) -> DataQuality | None:
    if not caveats:
        return None
    return DataQuality(completeness="full", caveats=caveats)

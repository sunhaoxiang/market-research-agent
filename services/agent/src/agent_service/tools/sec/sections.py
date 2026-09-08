"""从 10-K / 10-Q HTML 抽出指定 Item。全文先分节，再按 offset 截取，避免爆上下文。"""

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
from agent_service.tools.sec.models import FilingItemOutline, FilingSectionData

_TOOL = "get_filing_section"
_DEFAULT_CHARS = 6_000
_MAX_CHARS = 8_000
_OUTLINE = "outline"
_SECTION_HELP = "section 只支持 Item 编号（1A / 7 / 9A）或 mda / risk_factors / outline"
_ACCESSION = re.compile(r"^(\d{10})-(\d{2})-(\d{6})$")
_COMPACT = re.compile(r"^\d{18}$")
_ITEM_CODE = re.compile(r"^(\d{1,2})([a-z])?$")
_ANY_ITEM = re.compile(r"(?im)(?:^|\n)\s*item\s+(\d{1,2}[a-z]?)\b\s*[.\-—:]?")
_BLOCK = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_BR = re.compile(r"(?i)<br\s*/?>")
_CLOSE = re.compile(r"(?i)</(?:p|div|tr|h[1-6]|li|table|section|article|header)>")
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[^\S\n]+")
_NL = re.compile(r" *\n *")
_BLANK_LINES = re.compile(r"\n{3,}")
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
    "outline": _OUTLINE,
    "toc": _OUTLINE,
    "index": _OUTLINE,
    "catalog": _OUTLINE,
}


@dataclass(frozen=True, slots=True)
class ExtractedItem:
    code: str
    heading: str
    text: str
    start: int = 0


def normalize_section(raw: str) -> str | None:
    key = raw.strip().casefold()
    for ch in " ._-/":
        key = key.replace(ch, "")
    key = key.replace("&", "")
    if key.startswith("item"):
        key = key[4:]
    alias = _ALIASES.get(key)
    if alias is not None:
        return alias
    match = _ITEM_CODE.fullmatch(key)
    if match is None:
        return None
    number, letter = match.group(1), match.group(2)
    return f"{number}{letter.upper()}" if letter else number


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


def split_items(text: str) -> tuple[ExtractedItem, ...]:
    """按 Item 切开全文。同一编号出现两次时取更长的那段（跳过目录）。"""
    matches = list(_ANY_ITEM.finditer(text))
    if not matches:
        return ()
    spans: list[ExtractedItem] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.start() : end].strip()
        if not chunk:
            continue
        heading = chunk.split("\n", 1)[0].strip()
        spans.append(
            ExtractedItem(
                code=_format_code(match.group(1)),
                heading=heading,
                text=chunk,
                start=match.start(),
            )
        )
    chosen: dict[str, ExtractedItem] = {}
    for item in spans:
        current = chosen.get(item.code)
        if current is None or _prefer(item, current):
            chosen[item.code] = item
    return tuple(sorted(chosen.values(), key=lambda item: item.start))


def extract_item(text: str, code: str) -> ExtractedItem | None:
    wanted = normalize_section(code) or code
    for item in split_items(text):
        if item.code == wanted:
            return item
    return None


async def run_get_filing_section(
    deps: ToolDeps,
    *,
    accession: str,
    section: str,
    offset: int = 0,
    max_chars: int = _DEFAULT_CHARS,
) -> ToolResult[FilingSectionData]:
    parsed = _parse_args(accession, section, offset, max_chars)
    if isinstance(parsed, ToolResult):
        return parsed
    accn, code, offset_n, limit_n = parsed
    loaded = await _load_document(deps, accn)
    if isinstance(loaded, ToolResult):
        return loaded
    filing, document = loaded
    items = split_items(html_to_text(document.html))
    if not items:
        return _not_found("没有抽出任何 Item")
    catalog = _catalog(items)
    if code == _OUTLINE:
        text = _outline_text(items)
        return _success(
            FilingSectionData(
                accession=accn,
                form=filing.form or None,
                section=code,
                heading="Item outline",
                text=text,
                offset=0,
                total_chars=len(text),
                truncated=False,
                next_offset=None,
                url=document.url,
                items=catalog,
            ),
            document,
            [],
        )
    extracted = next((item for item in items if item.code == code), None)
    if extracted is None:
        return _not_found(f"没有抽出 Item {code}")
    body, truncated, next_offset = _slice(extracted.text, offset=offset_n, max_chars=limit_n)
    return _success(
        FilingSectionData(
            accession=accn,
            form=filing.form or None,
            section=code,
            heading=extracted.heading or None,
            text=body,
            offset=offset_n,
            total_chars=len(extracted.text),
            truncated=truncated,
            next_offset=next_offset,
            url=document.url,
            items=catalog,
        ),
        document,
        _caveats(body, offset_n, len(extracted.text), truncated, next_offset),
    )


def _success(
    data: FilingSectionData, document: FilingDocument, caveats: list[str]
) -> ToolResult[FilingSectionData]:
    return ToolResult.success(
        data,
        _page_provenance(document.provenance, document.url),
        quality=_quality(caveats),
    )


def _parse_args(
    accession: str, section: str, offset: int, max_chars: int
) -> tuple[str, str, int, int] | ToolResult[FilingSectionData]:
    accn = _accession(accession)
    if accn is None:
        return fail_invalid(_TOOL, "accession 格式应为 0001045810-25-000031")
    code = normalize_section(section)
    if code is None:
        return fail_invalid(_TOOL, _SECTION_HELP)
    if offset < 0:
        return fail_invalid(_TOOL, "offset 不能为负")
    if max_chars < 1:
        return fail_invalid(_TOOL, "max_chars 必须 ≥ 1")
    return accn, code, offset, min(max_chars, _MAX_CHARS)


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


def _slice(text: str, *, offset: int, max_chars: int) -> tuple[str, bool, int | None]:
    if offset >= len(text):
        return "", False, None
    end = min(offset + max_chars, len(text))
    truncated = end < len(text)
    return text[offset:end], truncated, end if truncated else None


def _catalog(items: tuple[ExtractedItem, ...]) -> list[FilingItemOutline]:
    return [
        FilingItemOutline(code=item.code, heading=item.heading, chars=len(item.text))
        for item in items
    ]


def _outline_text(items: tuple[ExtractedItem, ...]) -> str:
    return "\n".join(
        f"ITEM {item.code}  {item.heading}  ({len(item.text)} chars)" for item in items
    )


def _caveats(
    body: str, offset: int, total: int, truncated: bool, next_offset: int | None
) -> list[str]:
    if truncated and next_offset is not None:
        return [f"已返回 {len(body)}/{total} 字符。用 offset={next_offset} 继续取同一章节"]
    if offset > 0 and not body:
        return [f"offset={offset} 已超出章节长度 {total}"]
    return []


def _prefer(left: ExtractedItem, right: ExtractedItem) -> bool:
    if len(left.text) != len(right.text):
        return len(left.text) > len(right.text)
    return left.start > right.start


def _format_code(raw: str) -> str:
    if raw[-1].isalpha():
        return raw[:-1] + raw[-1].upper()
    return raw


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


def _quality(caveats: list[str]) -> DataQuality | None:
    if not caveats:
        return None
    return DataQuality(completeness="partial" if caveats else "full", caveats=caveats)

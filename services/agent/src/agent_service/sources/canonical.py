"""URL 归一化，供跨 Agent 按 `url_canonical` 去重（§15.1）。

只剥跟踪参数和无关的表面差异，不改路径大小写、不把 http 升成 https——
那两件事会把不同页面合成一条。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "dclid",
        "msclkid",
        "twclid",
        "yclid",
        "ttclid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "mkt_tok",
        "_hsenc",
        "_hsmi",
        "srsltid",
        "gad_source",
        "gad_campaignid",
        "ncid",
    }
)
_TRACKING_PREFIXES = ("utm_", "pk_", "mt_")
_DEFAULT_PORTS = {("http", 80), ("https", 443)}


def canonicalize_url(url: str) -> str:
    """得到用于去重的 canonical 形式。解析不了就返回 strip 后的原文。"""
    raw = url.strip()
    if not raw:
        return raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return raw

    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return raw

    netloc = _netloc(host, parsed.port, scheme)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking(key)
    ]
    kept.sort(key=lambda item: item[0].lower())
    return urlunsplit((scheme, netloc, path, urlencode(kept, doseq=True), ""))


def _netloc(host: str, port: int | None, scheme: str) -> str:
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is None or (scheme, port) in _DEFAULT_PORTS:
        return host
    return f"{host}:{port}"


def _is_tracking(name: str) -> bool:
    lower = name.lower()
    return lower in _TRACKING_PARAMS or lower.startswith(_TRACKING_PREFIXES)

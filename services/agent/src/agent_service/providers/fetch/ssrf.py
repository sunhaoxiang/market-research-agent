"""SSRF 判定（P2-3）。

LLM 和搜索结果都会把 URL 喂给 `web_fetch`。一旦跟随重定向或信任
Content-Length，攻击面就是本机上的一切：loopback、云 metadata、
内网管理端口。所以每跳都要独立过这一关——解析 URL、看主机名、
DNS 成 IP、再判 IP——缺一环就能绕。

不在这里发请求。Fetcher 负责 DNS 与 HTTP；本模块只回答「能不能碰」。
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from agent_service.providers.errors import ProviderError
from agent_service.schemas.tools import ToolErrorCode

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata.goog",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")

# 标准库 is_private 覆盖不完整的网段，显式补上
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",  # CGNAT
        "127.0.0.0/8",
        "169.254.0.0/16",  # 含云 metadata 169.254.169.254
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)


def blocked(message: str) -> ProviderError:
    return ProviderError(
        ToolErrorCode.BLOCKED,
        message,
        retryable=False,
        provider="web_fetch",
    )


def parse_fetch_url(raw: str) -> str:
    """语法检查并去掉 fragment。返回可拿去发请求的 URL 字符串。"""
    url = raw.strip()
    if not url or any(ch in url for ch in "\r\n\t"):
        raise blocked("URL 不合法")
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise blocked(f"不允许的协议：{parsed.scheme or '（空）'}")
    if parsed.username is not None or parsed.password is not None:
        raise blocked("拒绝带用户名或密码的 URL")
    if not parsed.hostname:
        raise blocked("URL 缺少主机名")
    # 丢掉 fragment，避免同一页因 # 不同而打两次
    return parsed._replace(fragment="").geturl()


def hostname_of(url: str) -> str:
    host = urlparse(url).hostname
    if host is None:
        raise blocked("URL 缺少主机名")
    return host.rstrip(".").lower()


def host_is_blocked(host: str) -> bool:
    name = host.rstrip(".").lower()
    if name in _BLOCKED_HOSTS:
        return True
    return any(name.endswith(suffix) for suffix in _BLOCKED_SUFFIXES)


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            return ip_is_blocked(mapped)
        sixtofour = ip.sixtofour
        if sixtofour is not None:
            return ip_is_blocked(sixtofour)
        teredo = ip.teredo
        if teredo is not None:
            return ip_is_blocked(teredo[1])
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


_IPV4_MAX = 0xFFFFFFFF


def literal_ips(host: str) -> list[str] | None:
    """主机名本身就是 IP（含十进制整数形式的 IPv4）时直接返回，不再走 DNS。"""
    cleaned = host.strip().removeprefix("[").removesuffix("]")
    try:
        ip = ipaddress.ip_address(cleaned)
    except ValueError:
        ip = None
    if ip is None and cleaned.isdigit():
        value = int(cleaned)
        if 0 <= value <= _IPV4_MAX:
            ip = ipaddress.IPv4Address(value)
    if ip is None:
        return None
    return [str(ip)]


def assert_public_ips(host: str, ips: list[str]) -> None:
    if host_is_blocked(host):
        raise blocked("主机名被拦截")
    if not ips:
        raise ProviderError(
            ToolErrorCode.NOT_FOUND,
            "无法解析主机名",
            retryable=False,
            provider="web_fetch",
        )
    for raw in ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise blocked("DNS 返回了无法识别的地址") from exc
        if ip_is_blocked(ip):
            raise blocked("目标解析到内网或保留地址")

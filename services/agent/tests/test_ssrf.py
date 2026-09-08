"""SSRF 判定：不发 HTTP，专门钉死「这一类地址碰不得」。"""

from __future__ import annotations

import ipaddress

import pytest

from agent_service.providers.errors import ProviderError
from agent_service.providers.fetch.ssrf import (
    assert_public_ips,
    host_is_blocked,
    hostname_of,
    ip_is_blocked,
    literal_ips,
    parse_fetch_url,
)
from agent_service.schemas.tools import ToolErrorCode


def _blocked(url: str) -> None:
    with pytest.raises(ProviderError) as exc:
        parse_fetch_url(url)
        host = hostname_of(url)
        ips = literal_ips(host)
        if ips is not None:
            assert_public_ips(host, ips)
        elif host_is_blocked(host):
            raise ProviderError(ToolErrorCode.BLOCKED, "主机名被拦截", provider="web_fetch")
    assert exc.value.code is ToolErrorCode.BLOCKED


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/1",
        "javascript:alert(1)",
        "data:text/html,hi",
        "http://user:pass@example.com/",
        "https://user@example.com/",
        "http://127.0.0.1/",
        "http://127.0.0.1:8000/v1/health",
        "http://localhost/",
        "http://[::1]/",
        "http://192.168.1.1/",
        "http://10.0.0.5/admin",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://2130706433/",  # 127.0.0.1 的十进制写法
        "http://0.0.0.0/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_private_and_odd_urls_are_blocked(url: str) -> None:
    _blocked(url)


@pytest.mark.parametrize(
    "host",
    ["localhost", "metadata.google.internal", "foo.local", "db.internal"],
)
def test_blocked_hostnames(host: str) -> None:
    assert host_is_blocked(host)


def test_public_https_url_is_accepted() -> None:
    assert parse_fetch_url("https://www.example.com/a#frag") == "https://www.example.com/a"


def test_loopback_and_metadata_ips_are_blocked() -> None:
    for raw in ("127.0.0.1", "::1", "169.254.169.254", "10.1.2.3", "192.168.0.1"):
        assert ip_is_blocked(ipaddress.ip_address(raw))


def test_public_ip_is_allowed() -> None:
    assert not ip_is_blocked(ipaddress.ip_address("93.184.216.34"))


def test_dns_to_loopback_is_blocked() -> None:
    with pytest.raises(ProviderError) as exc:
        assert_public_ips("evil.test", ["127.0.0.1"])
    assert exc.value.code is ToolErrorCode.BLOCKED
    assert "内网" in exc.value.message

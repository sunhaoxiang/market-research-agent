"""URL 归一化（P2-7 / §15.1）。"""

from agent_service.sources.canonical import canonicalize_url


def test_strips_tracking_params_and_www() -> None:
    messy = "https://www.theblock.co/post/1?utm_source=twitter&fbclid=abc&b=2&a=1#section"
    assert canonicalize_url(messy) == "https://theblock.co/post/1?a=1&b=2"


def test_same_page_with_different_tracking_collapses() -> None:
    left = "https://example.com/a?utm_campaign=x"
    right = "https://example.com/a/"
    assert canonicalize_url(left) == canonicalize_url(right) == "https://example.com/a"


def test_keeps_content_query_and_non_default_port() -> None:
    assert (
        canonicalize_url("http://News.Example.com:8080/Path/?id=9")
        == "http://news.example.com:8080/Path?id=9"
    )


def test_drops_default_https_port() -> None:
    assert canonicalize_url("https://sec.gov:443/cgi-bin/browse-edgar") == (
        "https://sec.gov/cgi-bin/browse-edgar"
    )


def test_invalid_or_empty_passthrough() -> None:
    assert canonicalize_url("  ") == ""
    assert canonicalize_url("not a url") == "not a url"
    assert canonicalize_url("ftp://files.example.com/a") == "ftp://files.example.com/a"

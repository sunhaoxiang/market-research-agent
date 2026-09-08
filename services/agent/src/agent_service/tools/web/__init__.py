"""web_search / web_fetch / news_search。"""

from agent_service.tools.web.bindings import WEB_TOOLS, news_search, web_fetch, web_search
from agent_service.tools.web.fetch import run_web_fetch
from agent_service.tools.web.models import WebPageData, WebSearchData, WebSearchHit
from agent_service.tools.web.search import run_news_search, run_web_search

__all__ = [
    "WEB_TOOLS",
    "WebPageData",
    "WebSearchData",
    "WebSearchHit",
    "news_search",
    "run_news_search",
    "run_web_fetch",
    "run_web_search",
    "web_fetch",
    "web_search",
]

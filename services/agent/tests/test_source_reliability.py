"""来源可靠性分级（P2-7 / §15.2）。"""

from agent_service.schemas.common import SourceReliability, SourceType
from agent_service.sources.reliability import classify_reliability


def test_primary_for_sec_and_gov() -> None:
    assert (
        classify_reliability("https://www.sec.gov/Archives/edgar/data/1/a.htm")
        is SourceReliability.PRIMARY
    )
    assert (
        classify_reliability("https://www.federalreserve.gov/newsevents.htm")
        is SourceReliability.PRIMARY
    )
    assert (
        classify_reliability("https://example.com/x", source_type=SourceType.SEC)
        is SourceReliability.PRIMARY
    )


def test_secondary_for_reputable_media() -> None:
    assert (
        classify_reliability("https://www.theblock.co/post/hyperliquid")
        is SourceReliability.SECONDARY
    )
    assert classify_reliability("https://www.reuters.com/markets/") is SourceReliability.SECONDARY


def test_aggregator_for_market_data_sites() -> None:
    assert classify_reliability("https://www.coingecko.com/en/coins/hype") is (
        SourceReliability.AGGREGATOR
    )
    assert (
        classify_reliability("https://api.example.com/v1", source_type=SourceType.API)
        is SourceReliability.AGGREGATOR
    )


def test_unknown_for_social_and_blogs() -> None:
    assert classify_reliability("https://x.com/someone/status/1") is SourceReliability.UNKNOWN
    assert (
        classify_reliability("https://reuters.com/a", source_type=SourceType.SOCIAL)
        is SourceReliability.UNKNOWN
    )
    assert classify_reliability("https://random-blog.example/post") is SourceReliability.UNKNOWN

from agent_service.schemas.common import Schema


class ChainActivityData(Schema):
    chain: str
    days: int
    n_markets: int | None = None
    volume_24h_usd: float | None = None
    open_interest_usd: float | None = None
    active_addresses: int | None = None
    tx_count: int | None = None
    url: str

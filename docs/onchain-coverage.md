# 链上数据覆盖度（P3-8）

调研日期：2026-09-08。样例标的仍是 **HYPE / Hyperliquid**。拿不到的字段进 `data_gaps`，绝不让 LLM 编。

相关：`[DP §3.6]` 数据源选型、`[DP §8.4]` onchain tools、`[DP §23 R4]`。

## 结论一览

| 指标 | 源 | 结论 |
| --- | --- | --- |
| 24h 永续名义成交量、持仓（USD） | Hyperliquid 官方 `POST https://api.hyperliquid.xyz/info`，`{"type":"metaAndAssetCtxs"}`，**无 key** | **可做。** 响应是二元组 `[meta, ctxs]`；`universe[i]` 对齐 `ctxs[i]`；`dayNtlVlm` 已是 USD；OI USD = `markPx * openInterest`（OI 是币数量） |
| 活跃地址 / 交易数时间序列 | Hyperliquid Info **没有**全站 DAU；DefiLlama `active-users` 是 **Pro**；growthepie / L2Beat **不含** Hyperliquid L1 | **不做。** 空字段 + `missing_fields`，不要用 K 线去拼、不要爬 DefiLlama 付费页 |
| 代币持仓分布 | Nansen / Arkham / Dune | **无免费源。** `get_token_holders` 立刻 `UNSUPPORTED` |
| 巨鲸转账 | Nansen / Arkham / Whale Alert | **无免费源。** `get_whale_activity` 立刻 `UNSUPPORTED` |
| 交易所净流入 | CryptoQuant / Glassnode | **无免费源。** `get_exchange_flow` 立刻 `UNSUPPORTED` |
| 其它链的 chain activity | — | **UNSUPPORTED。** 不要发明 Ethereum-only 实现却假装覆盖 Hyperliquid |

TVL / fees / DEX volume 已在 **P3-6** 由 DefiLlama 覆盖，本项不再重复。

## 可行子集

`get_chain_activity(chain, days)`：

- 只认 Hyperliquid（大小写不敏感；去掉非字母数字后 ∈ `{hyperliquid, hyperliquidl1}`，覆盖 `Hyperliquid` / `hyperliquid-l1`）。
- 其它链在 tool 层返回 `UNSUPPORTED`，**不打 API**。
- 成功永远是 `DataQuality.partial`：`active_addresses` / `tx_count` 始终进 `missing_fields`。空字段不是 0 个地址。
- Hyperliquid 只有 24h 快照。`days` 默认 1、校验 1–365；`days != 1` 仍返回快照，并加 caveat「days 未应用，不要当成历史序列」。
- `provenance.source_url` 是给人点的 `https://app.hyperliquid.xyz`，不是 API。
- Provider：`HyperliquidProvider`，`name="hyperliquid"`。tool 层只调 `get_perp_snapshot()`，不再解析 JSON。
- 礼貌限速 `rate_per_second=1.0`（官方约 1200 weight/min、info 查询约 20 weight）。TTL 用 `CacheTTL.MARKET`（5 min）。无 key，health 报 `configured=True`。

`isDelisted is True` 的市场跳过。`universe` / `ctxs` 长度不等用 `zip(..., strict=False)`。全空 → `NOT_FOUND`；形状不对 → `PARSE_ERROR`。HTTP 400/404 → `NOT_FOUND`。

## 受限项（立刻 `UNSUPPORTED`）

`get_token_holders` / `get_whale_activity` / `get_exchange_flow` **不接 HTTP**。空 `asset` 才 `INVALID_INPUT`，否则 `UNSUPPORTED` + `ToolResult[None]`。工具存在是为了让 Agent 有结构化缺口，而不是工具不存在就编数字。P3-9 的 Crypto Agent 必须把这些抄进 `data_gaps`。

## 明确不做

- 接 DefiLlama Pro、Tokenomist、TokenUnlocks
- 刮网页或对每个币打 `candleSnapshot` 拼历史
- 把 holders / whale / flow 假装成功或返回 0
- 为 Ethereum 单独实现再假装覆盖 Hyperliquid
- 付费源（Dune / Nansen / Artemis / CryptoQuant / Glassnode）留作后续开关

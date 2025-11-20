# Connector Reference: Public WebSocket Endpoints

Before wiring real connectors into the unified broker, we document the publicly exposed WebSocket endpoints and subscription flows for the exchanges targeted in the MVP. Each section cites the official documentation so we can track breaking changes later.

## Binance (Spot / Futures)

- **Docs**: https://developers.binance.com/docs/binance-spot-api-docs#websocket-market-streams  
- **Base WS**: `wss://stream.binance.com:9443/stream` (combined stream) or `wss://stream.binance.com:9443/ws/{symbol}@trade` for single stream.  
- **Trade stream**: subscribe with `{"method":"SUBSCRIBE","params":["btcusdt@trade"],"id":1}`; returns `trade` events with `p` (price), `q` (qty), `T`/`T` etc.  
- **Orderbook**: use `@depth20@100ms` or `@depth@100ms` to receive incremental orderbook updates plus full snapshots; snapshot stream includes `bids`/`asks` arrays and `u`, `U`, `pu` fields for sequencing.  
- **Heartbeats**: clients should respond to ping frames, and Binance sends `ping` requests (send `pong`).  
- **Notes**: rely on `u`/`U`/`pu` for integrity; treat `E` as `ts_exchange`. `@aggTrade` gives aggregated trades if needed for dedupe.

## Bybit (V5 / Realtime Public)

- **Docs**: https://bybit-exchange.github.io/docs/v5/websocket/public/  
- **Endpoint**: `wss://stream.bybit.com/v5/public/spot` (V5) or `wss://stream.bybit.com/realtime_public` for the legacy REST-like interface.  
- **Subscription**: send `{"op":"subscribe","args":["publicTrade.BTCUSDT","orderBookL2_25.BTCUSDT"]}`.  
- **Trade payload**: includes `symbol`, `price`, `size`, `side`, `trade_time_ms`, and `tick_direction`.  
- **Orderbook**: `orderBookL2_25` feed sends `diff` updates plus `snapshot` arrays; each update includes `seqNum`/`prevSeqNum`.  
- **Resilience**: track `snapshot`+`diff` and use `seqNum` for book replay; respond to `ping` messages with `pong`.

## Hyperliquid

- **Docs**: https://hyperliquid.gitbook.io/hyperliquid-docs/ (see “Reference → Websocket API”)  
- **Endpoint**: `wss://api.hyperliquid.xyz/ws` (public, no auth).  
- **Payload**: subscribe with `{"type":"subscribe","channels":["trades","orderbook"],"symbols":["BTC/USDT"]}`; responses include normalized `type`, `price`, `size`, `side`, `seq`, `timestamp`.  
- **Orderbook handling**: Hyperliquid provides `snapshot` + delta updates with `seq`/`prev_seq`; their book updates already include aggregated levels and best bid/ask.  
- **Raw mode**: the feed surfaces the original `data` block so we can attach it to `raw` if requested.

## Extended Exchange

- **Docs**: https://docs.extended.exchange/  
- **Endpoint**: `wss://api.extended.exchange/ws/public` (per docs, the `public` namespace covers market data).  
- **Subscription**: send `{"type":"subscribe","channels":["trades","orderbook"],"symbols":["BTC/USDC"]}`; responses include `topic`, `type`, `data`.  
- **Trade format**: custom fields `price`, `quantity`, `side`, `timestamp`, `id`; includes venue `exchange`.  
- **Orderbook**: includes full `snapshot` messages plus incremental events with `levels` and `sequence`.  
- **Notes**: Extended already tags each message with `exchange`, making normalization easier; track `sequence`/`prevSequence` for book resets.

## OKX (Public WS)

- **Docs**: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-ws-trades-channel  
- **Endpoint**: `wss://ws.okx.com:8443/ws/v5/public` (JSON public feed)  
- **Trade stream**: subscribe with `{"op":"subscribe","args":[{"channel":"trades","instId":"BTC-USDT"}]}`; each payload contains `data` array with `tradeId`, `px`, `sz`, `side`, and `ts`. Normalize prices/size from `px`/`sz`, map `instId` to normalized `symbol`, and convert `ts` to milliseconds.  
- **Order book**: use `books-l2-tbt` (or `books50-l2-tbt`/`books`) channel; subscribe via `{"op":"subscribe","args":[{"channel":"books-l2-tbt","instId":"BTC-USDT","sz":"20"}]}`. Update payloads include `asks`/`bids`, `seqNum`/`preSeqNum`, and `action` (`snapshot`/`update`) to distinguish book states.  
- **Heartbeats**: send WebSocket `pong` frames in response to server `ping` frames (server pings every ~20s).  
- **Notes**: Use the JSON feed instead of SBE for compatibility; treat snapshots as `BookUpdateType.SNAPSHOT` and subsequent events as deltas. The `tickers` channel is optional because best bid/ask can be derived from `books-l2-tbt`.

## Lighter (Mainnet)

- **Docs**: https://apidocs.lighter.xyz/docs/websocket-reference  
- **Endpoint**: `wss://mainnet.zklighter.elliot.ai/stream` (public)  
- **Market mapping**: query `https://mainnet.zklighter.elliot.ai/api/v1/orderBooks` (public REST) to resolve `symbol` → `market_id` before subscribing.  
- **Subscriptions**: subscribe to trades with `{"type":"subscribe","channel":"trade/{MARKET_INDEX}"}` and order book updates with `{"type":"subscribe","channel":"order_book/{MARKET_INDEX}"}`; responses include `trades` array or `order_book` object with `asks`/`bids` and an `offset` sequence.  
- **Normalization**: convert `market_id` back to human-readable symbol (decode tokens such as `USDJPY`, append `/USDC` when quote is implied). Normalize timestamps (seconds → ms) and treat the first order book push per market as a `SNAPSHOT`, subsequent pushes as `DELTA`.  
- **Heartbeats**: respond to WebSocket ping frames automatically (the server expects a WebSocket-level `pong`).  
- **Notes**: Because real-time symbols are identified by numeric indexes, refresh the REST mapping periodically when the market list changes (new markets get new `market_id` entries).

> **Tip**: Keep a copy of these URLs and subscription payloads inside the broker tests so the connectors can be mocked against replayed traffic if the real endpoints change.

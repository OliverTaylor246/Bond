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

> **Tip**: Keep a copy of these URLs and subscription payloads inside the broker tests so the connectors can be mocked against replayed traffic if the real endpoints change.

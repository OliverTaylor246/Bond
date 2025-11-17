# 3kk0 Unified WebSocket Architecture

This document captures the architecture, data flows, and component responsibilities for the 3kk0 MVP (single `wss://api.3kk0.com/stream` feed) while preserving the existing Bond/NL control plane for backwards compatibility.

## Goals

1. One WebSocket endpoint accepts a subscription payload with universal filters (`channels`, `symbols`, `exchanges`, `depth`, `raw`).
2. Every exchange connector speaks the new normalized schema (`engine/schemas.py`) and emits trades/orderbooks with `ts_event`, `ts_exchange`, `side`, `bids/asks`, etc.
3. The broker merges/deduplicates multi-exchange events through `engine/aggregation.py`, applies per-connection filters, and fans results to clients.
4. Heartbeats/backoff/metrics live in the broker so clients do not need to manage connector state.
5. A new Python SDK (`sdk/python/kk0`) consumes this stream via `Stream(...)` and exposes sync/async, reconnects, and hooks.

## Component Overview

| Component | Responsibility |
| --- | --- |
| `engine/connectors/base.py` | Defines `ExchangeConnector` interface (connect/disconnect/subscribe/iterate/normalize). |
| Exchange connectors | Subclass the base, maintain WS loops with resilient reconnect/backoff, hydrate normalized `Trade` and `OrderBook`. |
| `engine/aggregation.py` | Heap-based merge/dedupe engine honoring `max_lag_ms`, dedupe keys, wildcard/subscription filters, and flush logic. |
| Unified broker | FastAPI WebSocket (e.g., `/stream`) that registers subscriptions, orchestrates connectors, pushes normalized events/heartbeats, handles raw mode, and exposes `/metrics`. |
| Metrics | Tracks active subscriptions, per-exchange connection status, messages/sec, uptime, reconnect counts, etc. |
| SDK (`sdk/python/kk0`) | Wraps the WS endpoint with `Stream(...)`, auto ping/heartbeats/reconnect/backfill raw/normalized toggles, and exposes hooks. |

## WebSocket Protocol

Request:

```json
{
  "type": "subscribe",
  "channels": ["trades", "orderbook"],
  "symbols": ["BTC/USDT", "SOL/USDT"],
  "exchanges": ["binance", "okx", "bybit"],
  "depth": 20,
  "raw": false
}
```

Response stream emits normalized events (`Trade`, `OrderBook`, `Heartbeat`, `RawMessage`). Heartbeats are always emitted every 30s and include status info.

## Metrics Endpoint

`GET /metrics`

```json
{
  "active_subscriptions": 42,
  "messages_per_second": 9280,
  "uptime": 744000,
  "exchange_status": {
    "binance": "connected",
    "okx": "connected",
    "bybit": "reconnecting"
  }
}
```

## Control Plane Impact

- Existing Bond control plane (stream creation, NL compiler, Redis fans) continues in parallel for users who rely on it.  
- The new broker is a separate FastAPI app (or second router) that does not depend on stream IDs or Supabase.  
- Token auth can be shared via `apps/api/crypto` if desired, but the broker can also operate with short-lived API keys.

## Next Steps

1. Implement the broker skeleton (`apps/broker/unified.py`) that wires connectors → aggregator → clients and exposes `/metrics`.
2. Migrate connectors (start with Binance, OKX, Bybit) to the new interface and normalization helpers.
3. Build the `kk0` Python SDK plus documentation/README updates describing the new onboarding (install + `Stream` usage).
4. Add optional raw passthrough mode and update the README/architecture to mention the parallel NL stack.

## Testing Considerations

- Simulate a connector by feeding pre-recorded normalized events into the aggregation engine and ensure subscriptions only receive matching filters.
- Run end-to-end test covering `Stream(...)` connecting to `/stream`, receiving heartbeats, and handling reconnect/backoff.

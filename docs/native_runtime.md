# Native StreamRuntime (v2)

This document summarizes the canonical architecture for exchange-native streams.

## StreamConfig

The only accepted spec shape:

```json
{
  "stream_id": "uuid",
  "exchange": "binance",
  "symbols": ["BTC-USDT", "ETH-USDT"],
  "channels": {
    "orderbook": {"enabled": true, "depth": 20, "l3": false},
    "trades": true,
    "ticker": true,
    "ohlcv": {"enabled": true, "interval": "1s", "mode": "internal"},
    "funding": true,
    "open_interest": false
  },
  "heartbeat_ms": 2000,
  "flush_interval_ms": 100,
  "ttl": 10
}
```

Validated by `StreamConfig.validate_against_capabilities()` against `engine/venues.py`.

## Runtime

- `apps/runtime/NativeStreamRuntime`
  - validate config
  - resolve connector via `apps/runtime/exchange_registry.py`
  - run `run_native_pipeline` (batch → Redis XADD)
  - write metadata/heartbeat/plan in Redis

## Connectors

- One module per venue in `apps/runtime/connectors/{binance,bybit,okx,kucoin,mexc,hyperliquid,lighter}.py`
- Each exposes `async def stream_exchange(config: StreamConfig) -> AsyncIterator[dict]`
- Registry wires them by exchange name (currently skeletons)

## Pipeline

- `engine/pipeline_native.py`: simple batching + XADD; hooks for microdeltas/LOB reconstruction and timestamp clamping.
- Legacy `engine/pipeline_three.py` is kept for backward compatibility but should be removed once consumers migrate.

## Delivery

- Redis stream `stream:{stream_id}` → broker `/ws/{stream_id}` → SDK.
- Broker/SDK unchanged; they just forward normalized per-channel events.

## Next steps

- Implement per-venue connectors with sequencing/checksum/L2+L3 handling and channel wiring (trades/ticker/funding/ohlcv/oi).
- Add microdelta + LOB reconstructor in `pipeline_native`.
- Replace old StreamRuntime usage with NativeStreamRuntime in control plane.

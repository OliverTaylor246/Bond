# kk0 SDK

A minimal Python client for the 3kk0 unified WebSocket stream.

## Quickstart

```python
from kk0 import Stream
import asyncio

async def main():
    async with Stream("wss://api.3kk0.com/stream") as s:
        await s.subscribe(
            channels=["trades"],
            symbols=["SOL/USDT"],
            exchanges=["binance"],
        )
        async for event in s:
            print(event)

asyncio.run(main())
```

The broker exposes a single `/stream` websocket: send a `subscribe` payload with the filters you care about, and the iterator yields every normalized `trade`, `orderbook`, `heartbeat`, or `raw` message that matches. Include `raw=True` in the subscribe call to flow through the raw-connector payload (events arrive with `type == "raw"` and a `payload` that mirrors the upstream exchange data). Adjust `depth` and other filters as needed for your workload.

### Binance quickstart (quotes + trades + funding)

```bash
python sdk/python/examples/binance_quotes.py \
  --url ws://localhost:8080/stream \
  --symbol BTC/USDT \
  --depth 20 \
  --speed-ms 100
```

This example uses the SDK to subscribe to Binance trades, order books, funding marks, and price/ticker updates in one request (the SDK maps `price` → `ticker` automatically). Set `--raw` to also receive the exchange-native payloads. Use `--speed-ms 100` (fast) or `--speed-ms 1000` (slower/lower load) to control Binance depth update cadence.

## Unified stream schema

Each event received over `/stream` follows the dataclasses defined in [`engine/schemas.py`](engine/schemas.py:21-192). Trade frames (e.g., `type == "trade"`) always include `exchange`, `symbol`, `ts_event`, `ts_exchange`, `price`, `size`, `side`, and the optional metadata listed there; order book frames carry `bids`, `asks`, `update_type`, `depth`, `sequence`, and the derived helpers documented in the same file. Heartbeats carry `info`, and enabling `raw=True` adds `type == "raw"` messages whose `payload` is built in [`apps/broker/unified.py`](apps/broker/unified.py:365-533) to mirror the upstream connector data.

Every exchange connector referenced by the broker (for example [`engine/connectors/binance.py`](engine/connectors/binance.py:1-260), [`engine/connectors/bybit.py`](engine/connectors/bybit.py:1-300), and [`engine/connectors/hyperliquid.py`](engine/connectors/hyperliquid.py:1-320)) normalizes its native JSON into these fields before the broker fans them to clients. Because the SDK simply deserializes whatever JSON the broker sends (`Stream.__anext__` just `json.loads(...)`), you always see the same normalized keys regardless of the exchange you subscribe to.

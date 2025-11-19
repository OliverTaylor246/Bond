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

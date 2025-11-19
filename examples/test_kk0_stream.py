"""
Example script that connects to the production 3kk0 broker via `kk0.Stream`.

Copy this file locally and run it while your environment has network access to
`wss://3kk0-broker-production.up.railway.app/stream` to verify that
trades/orderbook updates arrive with the normalized schema.
"""

import asyncio
import sys
from pathlib import Path

_sdk_path: Path | None = None
for parent in Path(__file__).resolve().parents:
    candidate = parent / "sdk" / "python"
    if candidate.exists():
        _sdk_path = candidate
        break

if _sdk_path:
    sys.path.insert(0, str(_sdk_path))
else:
    raise SystemExit("kk0 SDK directory not found in repo tree")

from kk0 import Stream  # noqa: E402

STREAM_URL = "ws://46.62.131.60:8000/stream"
SYMBOLS = ["BTC/USDT"]
CHANNELS = ["trades"]
EXCHANGES = ["binance"]


async def bitcoin_stream():
    """Subscribe to BTC trades via the kk0 unified WebSocket."""
    async with Stream(STREAM_URL) as stream:
        await stream.subscribe(
    channels=["trades"],
    symbols=["*"],
    exchanges=["*"],
    depth=20,
)

        async for event in stream:
            print("event: ",event)


if __name__ == "__main__":
    asyncio.run(bitcoin_stream())

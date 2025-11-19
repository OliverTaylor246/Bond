"""
Walkthrough: log the first normalized event produced by each connector/exchange.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
SDK_PATH = ROOT / "sdk" / "python"
sys.path.insert(0, str(SDK_PATH))

from kk0 import Stream


BROKER_WS = os.environ.get(
    "KK0_BROKER_WS", "wss://3kk0-broker-production.up.railway.app/stream"
)
EXCHANGE_GROUPS = [
    ("binance", ["binance", "binanceus"]),
    ("bybit", ["bybit"]),
    ("hyperliquid", ["hyperliquid"]),
]
SYMBOLS = ["BTC/USDT"]
CHANNELS = ["trades"]


async def log_single_exchange(stream: Stream, exchange: str, aliases: Sequence[str]) -> None:
    print(f"subscribing to {exchange} aliases={aliases}")
    await stream.subscribe(channels=CHANNELS, symbols=SYMBOLS, exchanges=aliases)
    try:
        while True:
            event = await asyncio.wait_for(stream.__anext__(), timeout=5)
            if event.get("exchange") in aliases:
                print(f"{exchange} → {event}")
                return
    except asyncio.TimeoutError:
        print(f"{exchange} → no events after 5s, moving on")


async def main() -> None:
    async with Stream(BROKER_WS) as stream:
        for exchange, aliases in EXCHANGE_GROUPS:
            await log_single_exchange(stream, exchange, aliases)


if __name__ == "__main__":
    asyncio.run(main())

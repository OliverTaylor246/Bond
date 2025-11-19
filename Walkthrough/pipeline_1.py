"""
Walkthrough step 1: hit the raw connectors and print their normalized output before Redis.

This script connects directly to each exchange connector (Binance, Bybit, Hyperliquid)
and shows the first normalized trade they emit to confirm the upstream data source is healthy.
"""

import asyncio
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.connectors.binance import BinanceConnector
from engine.connectors.bybit import BybitConnector
from engine.connectors.hyperliquid import HyperliquidConnector


CONNECTORS = [
    ("binance", BinanceConnector()),
    ("bybit", BybitConnector()),
    ("hyperliquid", HyperliquidConnector()),
]

SYMBOL_VARIANTS: Sequence[str] = [
    "BTC/USDT",
    "BTC:USDT",
    "BTC-USDT",
    "BTCUSDT",
    "BTC.USDT",
    "/BTC",
]
CHANNELS = ["trades"]
DEPTH = 20


async def gather_one(name: str, connector):
    print(f"== {name} connect ==")
    await connector.connect()
    for variant in SYMBOL_VARIANTS:
        print(f"[{name}] subscribing to {variant}")
        await connector.subscribe(symbols=[variant], channels=CHANNELS, depth=DEPTH)
        async for event in connector:
            print(f"[{name}/{variant}] event: {event}")
            break
    await connector.disconnect()


async def main():
    tasks = [gather_one(name, connector) for name, connector in CONNECTORS]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

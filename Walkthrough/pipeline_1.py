"""
Walkthrough step 1: hit the raw connectors and print their normalized output before Redis.

This script connects directly to each exchange connector (Binance, Bybit, Hyperliquid)
and shows the first normalized trade they emit to confirm the upstream data source is healthy.
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.connectors.binance import BinanceConnector
from engine.connectors.bybit import BybitConnector
from engine.connectors.hyperliquid import HyperliquidConnector


CONNECTORS = [
    #("binance", BinanceConnector()),
    ("bybit", BybitConnector()),
    ("hyperliquid", HyperliquidConnector()),
]

SYMBOLS = ["BTC/USDT"]
CHANNELS = ["trades"]
DEPTH = 20


async def gather_one(name: str, connector):
    print(f"== {name} connect ==")
    await connector.connect()
    await connector.subscribe(symbols=SYMBOLS, channels=CHANNELS, depth=DEPTH)
    async for event in connector:
        print(f"{name} event: {event}")
        break
    await connector.disconnect()


async def main():
    tasks = [gather_one(name, connector) for name, connector in CONNECTORS]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

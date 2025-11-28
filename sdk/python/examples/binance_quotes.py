"""
Example: subscribe to Binance quotes via the unified kk0 stream.

Run with:
  python sdk/python/examples/binance_quotes.py --url ws://localhost:8080/stream
or point --url to your deployed broker endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Iterable

from kk0 import Stream


async def consume(url: str, symbols: Iterable[str], depth: int, speed_ms: int, raw: bool) -> None:
    async with Stream(url) as s:
        await s.subscribe(
            channels=["trades", "orderbook", "funding", "price"],
            symbols=list(symbols),
            exchanges=["binance"],
            depth=depth,
            speed_ms=speed_ms,
            raw=raw,
        )
        async for event in s:
            print(event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance quotes via kk0 unified stream")
    parser.add_argument("--url", default="ws://localhost:8080/stream", help="Broker websocket endpoint")
    parser.add_argument(
        "--symbol",
        action="append",
        default=["BTC/USDT"],
        help="Symbol to subscribe to (repeatable)",
    )
    parser.add_argument("--depth", type=int, default=20, help="Orderbook depth to request")
    parser.add_argument("--speed-ms", type=int, default=100, help="Binance orderbook speed (100 or 1000)")
    parser.add_argument("--raw", action="store_true", help="Include raw exchange payloads")
    args = parser.parse_args()

    asyncio.run(consume(args.url, args.symbol, args.depth, args.speed_ms, args.raw))


if __name__ == "__main__":
    main()

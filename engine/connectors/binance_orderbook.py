"""
Standalone Binance market data streamer with configurable latency.

Use this for quick experiments when you need fast order books, trades,
funding, or ticker/price updates without wiring the full broker.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Iterable, List

from .binance import BinanceConnector


def _parse_channels(raw: str) -> List[str]:
    channels: List[str] = []
    for part in raw.split(","):
        cleaned = part.strip().lower()
        if not cleaned:
            continue
        if cleaned in {"price", "prices"}:
            cleaned = "ticker"
        channels.append(cleaned)
    return channels or ["orderbook"]


def _resolve_symbol(default_symbol: str, base: str | None, quote: str | None) -> str:
    if base and quote:
        return f"{base}/{quote}"
    if base or quote:
        raise ValueError("Both base and quote are required if either is provided")
    return default_symbol


async def stream_binance(
    *,
    symbol: str,
    channels: Iterable[str],
    depth: int,
    speed_ms: int,
    raw: bool,
) -> None:
    connector = BinanceConnector()
    await connector.connect()
    await connector.subscribe(
        symbols=[symbol],
        channels=list(channels),
        depth=depth,
        speed_ms=speed_ms,
        raw=raw,
    )
    try:
        async for event in connector:
            print(event)
    except asyncio.CancelledError:
        raise
    except KeyboardInterrupt:
        logging.info("Stopping stream for %s", symbol)
    finally:
        await connector.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Binance WebSocket stream helper")
    parser.add_argument("--symbol", default="BTC/USDT", help="Symbol in BASE/QUOTE format (default: BTC/USDT)")
    parser.add_argument("--base", help="Base currency symbol (e.g., BTC)")
    parser.add_argument("--quote", help="Quote currency symbol (e.g., USDT)")
    parser.add_argument(
        "--channels",
        default="orderbook,trades,funding,price",
        help="Comma separated channels: orderbook,trades,funding,ticker/price",
    )
    parser.add_argument("--depth", type=int, default=20, help="Orderbook depth (5, 10, 20, 50...)")
    parser.add_argument(
        "--speed-ms",
        type=int,
        default=100,
        help="Orderbook update speed in milliseconds (Binance supports 100ms or 1000ms for spot)",
    )
    parser.add_argument("--raw", action="store_true", help="Attach raw exchange payloads to events")
    args = parser.parse_args()

    symbol = _resolve_symbol(args.symbol, args.base, args.quote)
    channels = _parse_channels(args.channels)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(
        stream_binance(
            symbol=symbol,
            channels=channels,
            depth=args.depth,
            speed_ms=args.speed_ms,
            raw=args.raw,
        )
    )


if __name__ == "__main__":
    main()

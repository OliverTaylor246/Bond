"""
Connect to the Lighter order_book websocket and print top-of-book after each
update. This is a lightweight sanity check that the feed is alive.

Usage:
  python3 examples/live_lighter_lob.py

Env vars:
  KK0_LIGHTER_SYMBOL   - normalized symbol (default BTC/USDT)
  KK0_LIGHTER_DEPTH    - depth to keep/print (default 20)
  KK0_LIGHTER_WS_URL   - override WS URL (default wss://mainnet.zklighter.elliot.ai/stream)
  KK0_LIGHTER_REST_URL - override REST to resolve market ids (default https://mainnet.zklighter.elliot.ai/api/v1/orderBooks)
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Dict, List, Optional, Tuple

import aiohttp
import websockets


DEFAULT_WS = "wss://mainnet.zklighter.elliot.ai/stream"
DEFAULT_REST = "https://mainnet.zklighter.elliot.ai/api/v1/orderBooks"

SYMBOL = os.getenv("KK0_LIGHTER_SYMBOL", "SOL/USDT").upper()
DEPTH = int(os.getenv("KK0_LIGHTER_DEPTH") or 20)
WS_URL = os.getenv("KK0_LIGHTER_WS_URL", DEFAULT_WS)
REST_URL = os.getenv("KK0_LIGHTER_REST_URL", DEFAULT_REST)

PriceSize = Tuple[float, float]


async def resolve_market_id(symbol: str) -> Optional[int]:
    async with aiohttp.ClientSession() as session:
        async with session.get(REST_URL, timeout=10) as resp:
            data = await resp.json()
    for entry in data.get("order_books", []):
        sym = str(entry.get("symbol") or "").upper()
        if sym == symbol.replace("/", "") or sym == symbol.upper():
            return entry.get("market_id")
        # also match normalized format
        if sym == symbol.replace("/", "") or sym == symbol.replace("/", ""):
            return entry.get("market_id")
    return None


def parse_levels(raw_levels: List[List[float]], *, reverse: bool) -> List[PriceSize]:
    out: List[PriceSize] = []
    for lvl in raw_levels:
        if not isinstance(lvl, (list, tuple)) or len(lvl) < 2:
            continue
        try:
            price = float(lvl[0])
            size = float(lvl[1])
            out.append((price, size))
        except Exception:
            continue
    out.sort(key=lambda entry: entry[0], reverse=reverse)
    return out


def extract_market_id(channel: str) -> Optional[int]:
    for sep in (":", "/"):
        if sep in channel:
            try:
                return int(channel.split(sep, 1)[1])
            except ValueError:
                return None
    return None


async def run():
    market_id = await resolve_market_id(SYMBOL)
    if market_id is None:
        print(f"Could not resolve market id for symbol {SYMBOL}")
        return
    print(f"Connecting to Lighter WS for {SYMBOL} (market_id={market_id}) depth={DEPTH}")

    last_offset: Optional[int] = None

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        sub = {"type": "subscribe", "channel": f"order_book/{market_id}"}
        await ws.send(json.dumps(sub))
        print(f"Subscribed with payload: {sub}")
        async for raw in ws:
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            channel = payload.get("channel")
            if not channel or not str(channel).startswith("order_book"):
                continue
            if extract_market_id(str(channel)) != market_id:
                continue
            book = payload.get("order_book")
            if not isinstance(book, dict):
                continue
            offset = book.get("offset")
            if offset is None:
                continue

            # enforce monotonic offsets
            if last_offset is not None:
                if offset <= last_offset:
                    print(f"out-of-order offset {offset} (last={last_offset}); dropping")
                    continue
                if offset != last_offset + 1:
                    print(f"offset gap detected (last={last_offset}, got={offset}); dropping")
                    continue

            bids = parse_levels(book.get("bids") or [], reverse=True)
            asks = parse_levels(book.get("asks") or [], reverse=False)

            last_offset = offset
            best_bid = bids[0] if bids else None
            best_ask = asks[0] if asks else None
            print(
                f"offset={offset} best_bid={best_bid} best_ask={best_ask} "
                f"bids={bids[:DEPTH]} asks={asks[:DEPTH]}"
            )


if __name__ == "__main__":
    asyncio.run(run())

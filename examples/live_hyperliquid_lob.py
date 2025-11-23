"""
Fetch a Hyperliquid L2 snapshot, stream live order book updates, and print the
top levels so you can confirm the feed is healthy.

Usage:
  python3 examples/live_hyperliquid_lob.py

Env vars:
  KK0_HL_COIN            - coin to watch (default BTC)
  KK0_HL_WS_URL          - override websocket URL (default wss://api.hyperliquid.xyz/ws)
  KK0_HL_REST_URL        - override REST snapshot URL (default https://api.hyperliquid.xyz/info)
  KK0_HL_PRINT_DEPTH     - levels per side to print (default 3)
  KK0_HL_PRINT_INTERVAL  - seconds between console prints (default 2)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Iterable, List, Tuple

import aiohttp
import websockets

DEFAULT_WS = "wss://api.hyperliquid.xyz/ws"
DEFAULT_REST = "https://api.hyperliquid.xyz/info"

COIN = os.getenv("KK0_HL_COIN", "BTC").upper()
WS_URL = os.getenv("KK0_HL_WS_URL", DEFAULT_WS)
REST_URL = os.getenv("KK0_HL_REST_URL", DEFAULT_REST)
PRINT_DEPTH = int(os.getenv("KK0_HL_PRINT_DEPTH") or 3)
PRINT_INTERVAL = float(os.getenv("KK0_HL_PRINT_INTERVAL") or 2.0)

PriceSize = Tuple[float, float]


def parse_levels(raw_levels: Iterable[object], *, reverse: bool) -> List[PriceSize]:
    out: List[PriceSize] = []
    for lvl in raw_levels:
        if not isinstance(lvl, dict):
            continue
        try:
            price = float(lvl.get("px"))
            size = float(lvl.get("sz"))
        except (TypeError, ValueError):
            continue
        out.append((price, size))
    out.sort(key=lambda entry: entry[0], reverse=reverse)
    return out


async def fetch_snapshot() -> dict:
    payload = {"type": "l2Book", "coin": COIN}
    async with aiohttp.ClientSession() as session:
        async with session.post(REST_URL, json=payload, timeout=10) as resp:
            resp.raise_for_status()
            return await resp.json()


def print_top(bids: List[PriceSize], asks: List[PriceSize], label: str) -> None:
    bid_slice = bids[:PRINT_DEPTH]
    ask_slice = asks[:PRINT_DEPTH]

    def fmt(side: List[PriceSize]) -> str:
        if not side:
            return "-"
        return " | ".join(f"{price:.2f}@{size:.4f}" for price, size in side)

    print(f"[{label}] bids: {fmt(bid_slice)} || asks: {fmt(ask_slice)}")


async def print_loop(bids: List[PriceSize], asks: List[PriceSize]) -> None:
    while True:
        await asyncio.sleep(PRINT_INTERVAL)
        print_top(bids, asks, "periodic")


async def stream_orderbook(bids: List[PriceSize], asks: List[PriceSize]) -> None:
    sub_payload = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": COIN}}
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=None) as ws:
                await ws.send(json.dumps(sub_payload))
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("channel") != "l2Book":
                        continue
                    data = msg.get("data") or {}
                    levels = data.get("levels") or [[], []]
                    new_bids = parse_levels(levels[0] if len(levels) > 0 else [], reverse=True)
                    new_asks = parse_levels(levels[1] if len(levels) > 1 else [], reverse=False)
                    bids[:] = new_bids
                    asks[:] = new_asks
                    print_top(new_bids, new_asks, "update")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[hyperliquid] stream error ({exc}); reconnecting in 1s...")
            await asyncio.sleep(1)


async def main() -> None:
    print(f"Fetching initial snapshot for {COIN} from {REST_URL} ...")
    try:
        snapshot = await fetch_snapshot()
    except Exception as exc:
        print(f"Failed to fetch snapshot: {exc}")
        return
    levels = snapshot.get("levels") or [[], []]
    bids = parse_levels(levels[0] if len(levels) > 0 else [], reverse=True)
    asks = parse_levels(levels[1] if len(levels) > 1 else [], reverse=False)
    print_top(bids, asks, "snapshot")
    print(f"Connecting to {WS_URL} for live updates ...")

    printer = asyncio.create_task(print_loop(bids, asks))
    try:
        await stream_orderbook(bids, asks)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        printer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await printer


if __name__ == "__main__":
    asyncio.run(main())

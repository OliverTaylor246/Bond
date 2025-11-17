#!/usr/bin/env python3
"""
Iterate every CCXT Pro method per exchange and inspect formats.
Also streams raw Hyperliquid + Extended Exchange data for normalization testing.
"""

from __future__ import annotations
import atexit
import asyncio
import json
import sys
from pathlib import Path

import ccxt
import ccxt.pro as ccxtpro
import websockets
from websockets.exceptions import ConnectionClosed

# ------------------------------------------------------------------
# FIX FOR HYPERLIQUID SDK BREAKAGE WITH CCXT v4 / ccxt.pro
# ------------------------------------------------------------------
possible_error_modules = [
    "ccxt.base.errors",
    "ccxt.errors",
    "ccxt.base",
    "ccxt",
]

MarketClosed = None
for modname in possible_error_modules:
    try:
        mod = __import__(modname, fromlist=["MarketClosed"])
        candidate = getattr(mod, "MarketClosed", None)
        if candidate is not None:
            MarketClosed = candidate
            break
    except Exception:
        continue

if MarketClosed is None:
    class MarketClosed(Exception):  # type: ignore[no-redef]
        pass

ccxt.MarketClosed = MarketClosed
try:
    import ccxt.base

    ccxt.base.MarketClosed = MarketClosed
except Exception:
    pass

print("[PATCH] MarketClosed patched for Hyperliquid compatibility.")
# ------------------------------------------------------------------

HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
EXT_HOST = "wss://api.starknet.extended.exchange"
EXT_STREAMS = [
    ("orderbook", "/stream.extended.exchange/v1/orderbooks/BTC-USD?depth=1"),
    ("trades", "/stream.extended.exchange/v1/publicTrades/BTC-USD"),
]

# Ensure every print is persisted for later reference
LOG_PATH = Path(__file__).with_suffix(".log")


class _TeeStream:
    def __init__(self, stream, path: Path):
        self.stream = stream
        self.file = path.open("a", encoding="utf-8")

    def write(self, data: str) -> None:
        self.stream.write(data)
        self.file.write(data)

    def flush(self) -> None:
        self.stream.flush()
        self.file.flush()

    def close(self) -> None:
        try:
            self.file.close()
        except Exception:
            pass


sys.stdout = _TeeStream(sys.stdout, LOG_PATH)
sys.stderr = _TeeStream(sys.stderr, LOG_PATH)


@atexit.register
def _close_streams() -> None:
    if hasattr(sys.stdout, "close"):
        sys.stdout.close()
    if hasattr(sys.stderr, "close"):
        sys.stderr.close()


class HyperliquidWS:
    DEFAULT_SUBS = [
        ("trades", "trades"),
        ("l2Book", "l2"),
        ("ticker", "ticker"),
    ]

    def __init__(self, coin="BTC", subs=None):
        self.coin = coin
        self.subs = subs or self.DEFAULT_SUBS
        self.ws = None
        self.should_run = True

    async def connect(self):
        self.ws = await websockets.connect(HL_WS_URL)
        await self._subscribe_all()

    async def _subscribe_all(self):
        for sub_type, _label in self.subs:
            payload = {
                "method": "subscribe",
                "subscription": {"type": sub_type, "coin": self.coin},
            }
            await self.ws.send(json.dumps(payload))

    async def __aiter__(self):
        while self.should_run:
            try:
                raw = await self.ws.recv()
                msg = json.loads(raw)
                yield msg
            except ConnectionClosed:
                print("[hyperliquid] connection closed, reconnecting...")
                await asyncio.sleep(1)
                await self.connect()

    async def close(self):
        self.should_run = False
        if self.ws:
            await self.ws.close()


SYMBOLS = {
    "binance": "BTC/USDT",
    "bybit": "BTC/USDT",
    "hyperliquid": "BTC-PERP",
}

TIMEFRAME = "1m"

PROXY = None

METHODS = (
    ("watchTrades", ["{symbol}"]),
    ("watchTicker", ["{symbol}"]),
    ("watchOrderBook", ["{symbol}"]),
    ("watchOHLCV", ["{symbol}", TIMEFRAME]),
)


def preview(x):
    try:
        if isinstance(x, list) and x:
            x = x[0]
        return json.dumps(x, default=str)
    except Exception:
        return str(x)


async def run_ccxt_method(client, exchange_id, method, args):
    label = f"[{exchange_id}] [{method}]"
    fn = getattr(client, method, None)
    if not callable(fn):
        print(f"{label} not supported.")
        return
    final_args = [a.format(symbol=SYMBOLS[exchange_id]) for a in args]
    print(f"{label} subscribing with args: {final_args}")
    try:
        result = await asyncio.wait_for(fn(*final_args), timeout=15)
        print(f"{label} received: {preview(result)}")
    except asyncio.TimeoutError:
        print(f"{label} TIMEOUT")
    except Exception as e:
        print(f"{label} ERROR: {e}")


async def run_ccxt_exchange(exchange_id: str):
    if exchange_id == "hyperliquid":
        print("[hyperliquid] CCXT Pro does not support HL market-data; skipping CCXT part.")
        return
    print(f"=== {exchange_id} (ccxt.pro) ===")
    opts: dict[str, str] = {}
    if PROXY:
        opts = {
            "aiohttp_proxy": PROXY,
            "http_proxy": PROXY,
            "https_proxy": PROXY,
            "proxy": PROXY,
        }
    exchange_cls = getattr(ccxtpro, exchange_id)
    client = exchange_cls(opts)
    try:
        print(f"[{exchange_id}] load_markets()")
        await asyncio.wait_for(client.load_markets(), timeout=10)
    except Exception as e:
        print(f"[{exchange_id}] load_markets FAILED: {e}")
        await client.close()
        return
    for method_name, args in METHODS:
        await run_ccxt_method(client, exchange_id, method_name, args)
        await asyncio.sleep(0.2)
    await client.close()
    print(f"[{exchange_id}] closed")
    print(f"=== done {exchange_id} ===\n")


def _extract_coin(symbol: str) -> str:
    base = symbol.split("/", 1)[0]
    return base.split("-", 1)[0]


async def run_hyperliquid_native():
    print("=== hyperliquid (native WS) ===")
    symbol = SYMBOLS["hyperliquid"]
    client = HyperliquidWS(coin=_extract_coin(symbol))
    await client.connect()
    seen: set[str] = set()
    try:
        async for msg in client:
            topic = msg.get("channel") or msg.get("topic") or msg.get("type")
            if not topic:
                continue
            label = topic.split(".")[0]
            if label in seen:
                continue
            print(f"[hyperliquid] [{label}] → {preview(msg)}")
            seen.add(label)
            if len(seen) >= len(client.subs):
                break
    except asyncio.CancelledError:
        pass
    finally:
        await client.close()
    print("=== done hyperliquid native ===\n")


async def _probe_extended_stream(label: str, path: str):
    url = f"{EXT_HOST}{path}"
    print(f"[extended:{label}] connecting to {url}")
    try:
        async with websockets.connect(url, ping_interval=10, ping_timeout=5) as ws:
            for _ in range(2):
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
                print(f"[extended:{label}] → {raw}")
    except Exception as exc:
        print(f"[extended:{label}] ERROR: {exc}")


async def run_extended_native():
    print("=== extended (starknet stream) ===")
    for label, path in EXT_STREAMS:
        await _probe_extended_stream(label, path)
    print("=== done extended ===\n")


async def main():
    for eid in ("binance", "bybit"):
        await run_ccxt_exchange(eid)
    await run_hyperliquid_native()
    await run_extended_native()


if __name__ == "__main__":
    asyncio.run(main())

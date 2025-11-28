from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Dict, Any

import websockets

from engine.schemas import StreamConfig
from engine.exchange_utils import (
    OrderBookL2,
    to_canonical_symbol,
    now_ms,
)

log = logging.getLogger(__name__)

# Official HL WS endpoint
HL_WS = "wss://api.hyperliquid.xyz/ws"


async def stream_exchange(config: StreamConfig) -> AsyncIterator[dict]:
    """
    Unified Hyperliquid connector.
    Channels supported:
        - lob.update (L2 synthetic deltas)
        - trade
        - ticker (mark/index/oracle)
        - funding.update
        - oi.update
        - ohlcv (if config.channels.ohlcv.mode == "internal")
    """

    symbols = config.symbols

    #
    # ----------------------------------------------------------------------
    # Build subscriptions
    # ----------------------------------------------------------------------
    #
    # Hyperliquid protocol:
    # { "method": "subscribe", "subscription": { "type": "...", "coin": "BTC" } }
    #
    subs = []

    for sym in symbols:
        # Hyperliquid uses single coin names (e.g., "BTC" not "BTC-USDT")
        # Normalize to get the base coin
        normalized = sym.upper().strip()
        
        # If already just a coin name, use it
        if "-" in normalized:
            coin = normalized.split("-")[0]
        else:
            # Try to extract base from BTCUSDT format
            found = False
            for q in ("USDT", "USD", "USDC", "PERP"):
                if normalized.endswith(q) and len(normalized) > len(q):
                    coin = normalized[:-len(q)]
                    found = True
                    break
            if not found:
                # Assume it's already just the coin name
                coin = normalized
        
        log.info(f"Hyperliquid: Mapping symbol {sym} -> coin {coin}")

        if config.channels.orderbook.enabled:
            subs.append({"type": "l2Book", "coin": coin})

        if config.channels.trades:
            subs.append({"type": "trades", "coin": coin})

        if config.channels.ticker:
            subs.append({"type": "ticker", "coin": coin})

        if config.channels.funding:
            subs.append({"type": "funding", "coin": coin})

        if config.channels.open_interest:
            subs.append({"type": "openInterest", "coin": coin})

    # OHLCV is internal only — HL has no native candle stream
    ohlcv_enabled = bool(getattr(config.channels, "ohlcv", None)) and (config.channels.ohlcv.mode == "internal")
    def _interval_to_ms(interval: str) -> int:
        # very simple parser: expect seconds with trailing s or integer seconds
        raw = interval.strip().lower()
        if raw.endswith("s"):
            raw = raw[:-1]
        try:
            return max(1, int(float(raw))) * 1000
        except Exception:
            return 1000  # default 1s
    ohlcv_interval_ms = _interval_to_ms(config.channels.ohlcv.interval) if ohlcv_enabled else None
    ohlcv_buckets = {}  # symbol -> current bucket

    # Hyperliquid requires individual subscription messages, not a batch
    subscribe_msgs = [
        {"method": "subscribe", "subscription": sub}
        for sub in subs
    ]

    #
    # ----------------------------------------------------------------------
    # Orderbook cache per symbol
    # HL sends full snapshots, no sequence numbers.
    # We generate deltas by diffing against last state.
    # Map using normalized symbols (coin -> OrderBookL2)
    # Hyperliquid is perp-only, so we'll standardize to {COIN}-USDT format for output
    # ----------------------------------------------------------------------
    #
    books: Dict[str, OrderBookL2] = {}
    coin_to_symbol: Dict[str, str] = {}
    
    for sym in symbols:
        normalized = sym.upper().strip()
        
        # Extract coin name
        if "-" in normalized:
            coin = normalized.split("-")[0]
        else:
            found = False
            for q in ("USDT", "USD", "USDC", "PERP"):
                if normalized.endswith(q) and len(normalized) > len(q):
                    coin = normalized[:-len(q)]
                    found = True
                    break
            if not found:
                coin = normalized
        
        # Standardize output symbol format
        symbol_key = f"{coin}-USDT"
        books[symbol_key] = OrderBookL2(symbol_key)
        coin_to_symbol[coin] = symbol_key
    
    log.info(f"Hyperliquid: Initialized books for {list(coin_to_symbol.values())}")

    #
    # ----------------------------------------------------------------------
    # Reconnect loop
    # ----------------------------------------------------------------------
    #
    while True:
        try:
            async with websockets.connect(HL_WS, max_size=None, ping_interval=20, ping_timeout=10) as ws:
                # Send each subscription message individually
                for sub_msg in subscribe_msgs:
                    await ws.send(json.dumps(sub_msg))
                
                log.info(f"Hyperliquid: Connected and sent {len(subscribe_msgs)} subscriptions")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError as e:
                        log.error(f"Hyperliquid: Failed to parse message: {e}")
                        continue

                    # Check channel field
                    ch = msg.get("channel")
                    if not ch:
                        log.debug(f"Hyperliquid: Message without channel: {msg}")
                        continue
                    
                    # Skip subscription response messages
                    if ch == "subscriptionResponse":
                        log.debug(f"Hyperliquid: Subscription response: {msg}")
                        continue
                    
                    data = msg.get("data")
                    if data is None:
                        log.debug(f"Hyperliquid: Message without data on channel {ch}")
                        continue

                    #
                    # ------------------------------------------------------
                    # TRADES - handle first since data is a list
                    # channel: "trades"
                    # HL format: data is a list of trades
                    #  [{ "px": "...", "sz": "...", "side": "B"/"S", "time": ms, "coin": "BTC" }, ...]
                    # ------------------------------------------------------
                    #
                    if ch == "trades":
                        if not isinstance(data, list):
                            log.warning(f"Hyperliquid: Expected list for trades, got {type(data)}")
                            continue
                        
                        for t in data:
                            if not isinstance(t, dict):
                                continue
                            
                            coin = t.get("coin")
                            if not coin:
                                log.warning(f"Hyperliquid: Trade missing coin: {t}")
                                continue
                            
                            symbol = coin_to_symbol.get(coin, f"{coin}-USDT")
                            
                            try:
                                ts_ex = int(t["time"])
                                px = float(t["px"])
                                sz = float(t["sz"])
                                side = "buy" if t.get("side") == "B" else "sell"

                                yield {
                                    "type": "trade",
                                    "exchange": "hyperliquid",
                                    "symbol": symbol,
                                    "ts_event": now_ms(),
                                    "ts_exchange": ts_ex,
                                    "px": px,
                                    "size": sz,
                                    "side": side,
                                    "trade_id": None,
                                }

                                # OHLCV accumulation (internal)
                                if ohlcv_enabled:
                                    bucket = _accumulate_ohlcv(
                                        ohlcv_buckets,
                                        symbol,
                                        px,
                                        sz,
                                        ts_ex,
                                        ohlcv_interval_ms,
                                    )
                                    if bucket:
                                        yield bucket
                            except (KeyError, ValueError, TypeError) as e:
                                log.error(f"Hyperliquid: Failed to parse trade {t}: {e}")
                        continue

                    #
                    # ------------------------------------------------------
                    # For other channels, extract coin from data dict
                    # ------------------------------------------------------
                    #
                    coin = data.get("coin") if isinstance(data, dict) else None
                    if not coin:
                        log.debug(f"Hyperliquid: Message on channel {ch} without coin field")
                        continue

                    symbol = coin_to_symbol.get(coin, f"{coin}-USDT")

                    #
                    # ------------------------------------------------------
                    # L2 ORDERBOOK
                    # channel: "l2Book"
                    # HL format:
                    # {
                    #   "coin": "BTC",
                    #   "levels": [[{"px": "...", "sz": "...", "n": 1}, ...], [...]]
                    #   OR
                    #   "bids": [{"px": "...", "sz": "..."}],
                    #   "asks": [{"px": "...", "sz": "..."}],
                    #   "time": timestamp_ms
                    # }
                    # ------------------------------------------------------
                    #
                    if ch == "l2Book":
                        ob = books.get(symbol)
                        if not ob:
                            log.warning(f"Hyperliquid: No orderbook for symbol {symbol}")
                            continue

                        try:
                            # Parse bids/asks from data
                            bids_raw = data.get("bids", [])
                            asks_raw = data.get("asks", [])
                            
                            # Handle levels format if present
                            if "levels" in data and not bids_raw and not asks_raw:
                                levels = data.get("levels", [])
                                if len(levels) >= 2:
                                    bids_raw = levels[0] if isinstance(levels[0], list) else []
                                    asks_raw = levels[1] if isinstance(levels[1], list) else []
                            
                            bids = [(float(x["px"]), float(x["sz"])) for x in bids_raw if isinstance(x, dict)]
                            asks = [(float(x["px"]), float(x["sz"])) for x in asks_raw if isinstance(x, dict)]
                            ts_ex = int(data.get("time", time.time() * 1000))

                            # First load OR delta diff
                            if not ob.initialized:
                                ob.load_full(bids=bids, asks=asks)
                                log.info(f"Hyperliquid: Initialized orderbook for {symbol}")
                                continue

                            deltas = ob.apply_full_diff(bids=bids, asks=asks)

                            ts_ev = now_ms()
                            for d in deltas:
                                yield {
                                    "type": "lob.update",
                                    "exchange": "hyperliquid",
                                    "symbol": symbol,
                                    "ts_event": ts_ev,
                                    "ts_exchange": ts_ex,
                                    "side": d["side"],
                                    "px": d["price"],
                                    "qty": d["qty"],
                                }
                        except (KeyError, ValueError, TypeError) as e:
                            log.error(f"Hyperliquid: Failed to parse l2Book data: {e}, data={data}")

                        continue

                    #
                    # ------------------------------------------------------
                    # TICKER (mark/index)
                    # channel: "ticker"
                    # HL format:
                    #  {
                    #    "markPx": "...",
                    #    "indexPx": "...",
                    #    "oraclePx": "...",
                    #    "time": ms,
                    #  }
                    # ------------------------------------------------------
                    #
                    if ch == "ticker":
                        yield {
                            "type": "ticker",
                            "exchange": "hyperliquid",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": int(data["time"]),
                            "last": float(data["markPx"]),
                            "mark": float(data["markPx"]),
                            "index": float(data["indexPx"]),
                            "open_interest": None,
                        }
                        continue

                    #
                    # ------------------------------------------------------
                    # FUNDING
                    # channel: "funding"
                    # HL format:
                    #   {
                    #     "fundingRate": "...",
                    #     "nextFundingTime": ms,
                    #     "markPx": "...",
                    #     "indexPx": "...",
                    #     "time": ms
                    #   }
                    # ------------------------------------------------------
                    #
                    if ch == "funding":
                        yield {
                            "type": "funding.update",
                            "exchange": "hyperliquid",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": int(data["time"]),
                            "rate": float(data["fundingRate"]),
                            "next_funding": int(data["nextFundingTime"]),
                            "mark": float(data["markPx"]),
                            "index": float(data["indexPx"]),
                        }
                        continue

                    #
                    # ------------------------------------------------------
                    # OPEN INTEREST
                    # channel: "openInterest"
                    # HL format:
                    #   { "oi": "...", "time": ms }
                    # ------------------------------------------------------
                    #
                    if ch == "openInterest":
                        yield {
                            "type": "oi.update",
                            "exchange": "hyperliquid",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": int(data["time"]),
                            "oi": float(data["oi"]),
                        }
                        continue

        except Exception as e:
            log.error(f"Hyperliquid connector error: {type(e).__name__}: {e}", exc_info=True)
            await asyncio.sleep(2.0)
            continue


#
# ----------------------------------------------------------------------
# Internal OHLCV accumulator
# ----------------------------------------------------------------------
#
def _accumulate_ohlcv(
    buckets: Dict[str, dict],
    symbol: str,
    px: float,
    size: float,
    ts_ex: int,
    interval_ms: int,
):
    """
    Internal OHLCV builder for HL using trades only.
    Returns a finished OHLCV dict, or None if the bucket isn't done.
    """

    bucket = buckets.get(symbol)
    if bucket is None:
        # Open new bucket
        bucket_start = ts_ex - (ts_ex % interval_ms)
        bucket = {
            "start": bucket_start,
            "open": px,
            "high": px,
            "low": px,
            "close": px,
            "volume": size,
        }
        buckets[symbol] = bucket
        return None

    # Check if we rolled over to next bucket
    if ts_ex >= bucket["start"] + interval_ms:
        finished = {
            "type": "ohlcv",
            "exchange": "hyperliquid",
            "symbol": symbol,
            "ts_event": now_ms(),
            "ts_exchange": ts_ex,
            "ts": bucket["start"],
            "open": bucket["open"],
            "high": bucket["high"],
            "low": bucket["low"],
            "close": bucket["close"],
            "volume": bucket["volume"],
        }

        # Start new bucket
        new_start = ts_ex - (ts_ex % interval_ms)
        buckets[symbol] = {
            "start": new_start,
            "open": px,
            "high": px,
            "low": px,
            "close": px,
            "volume": size,
        }

        return finished

    # Same bucket — update
    bucket["high"] = max(bucket["high"], px)
    bucket["low"] = min(bucket["low"], px)
    bucket["close"] = px
    bucket["volume"] += size

    return None

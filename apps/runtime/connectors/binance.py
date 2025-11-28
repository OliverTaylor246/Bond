from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Dict, Any, List

import aiohttp
import websockets

from engine.schemas import StreamConfig
from engine.exchange_utils import (
    now_ms,
    to_canonical_symbol,
    OrderBookL2,
)

log = logging.getLogger(__name__)

# Binance WS endpoints
BINANCE_WS_SPOT = "wss://stream.binance.com:9443/stream"
BINANCE_WS_FUTURES = "wss://fstream.binance.com/stream"


async def stream_exchange(config: StreamConfig) -> AsyncIterator[dict]:
    """
    Unified Binance connector.
    Produces normalized events:
        - lob.update
        - trade
        - ticker
        - funding.update
        - ohlcv  (if internal proxy mode)
        - oi.update
    """

    #
    # ------------------------------------------------------------------------
    # Normalize symbols to canonical form (BTC-USDT)
    # ------------------------------------------------------------------------
    #
    from engine.exchange_utils import to_canonical_symbol
    symbols = [to_canonical_symbol(s) for s in config.symbols]

    #
    # ------------------------------------------------------------------------
    # Resolve WS URL (spot vs perp). If the symbol ends with PERP or has no dash,
    # assume perp; otherwise default to spot.
    # ------------------------------------------------------------------------
    #

    def _is_perp(sym: str) -> bool:
        """Detect if symbol is a perpetual futures market"""
        s = sym.upper()
        # Binance perps: ends with PERP or common perp suffixes
        return s.endswith("PERP") or s.endswith("_PERP")

    # Allow explicit perp detection or auto-detect
    market_type = "perp" if any(_is_perp(s) for s in symbols) else "spot"
    ws_url = BINANCE_WS_FUTURES if market_type == "perp" else BINANCE_WS_SPOT
    
    log.info(f"Binance connector: market_type={market_type}, symbols={symbols}, ws_url={ws_url}")

    #
    # ------------------------------------------------------------------------
    # Build subscription payload from config.channels
    # ------------------------------------------------------------------------
    #
    streams = []

    for sym in symbols:
        s_raw = sym.replace("-", "").lower()  # BTC-USDT → btcusdt

        if config.channels.orderbook.enabled:
            depth = config.channels.orderbook.depth or 50
            # example: btcusdt@depth5@100ms
            streams.append(f"{s_raw}@depth{depth}@100ms")

        if config.channels.trades:
            streams.append(f"{s_raw}@trade")

        if config.channels.ticker:
            streams.append(f"{s_raw}@ticker")

        # Funding (futures only)
        if config.channels.funding:
            if market_type == "perp":
                streams.append(f"{s_raw}@markPrice")  # Use markPrice stream which includes funding
            else:
                log.warning(f"Binance: funding requested for spot symbol {sym}, skipping (not supported)")

        # OHLCV (only if enabled); pick a valid interval
        if getattr(config.channels, "ohlcv", None) and config.channels.ohlcv.enabled:
            interval = (config.channels.ohlcv.interval or "1m").lower()
            streams.append(f"{s_raw}@kline_{interval}")

        # OI (open interest)
        if config.channels.open_interest:
            if market_type == "perp":
                streams.append(f"{s_raw}@openInterest")
            else:
                log.warning(f"Binance: open interest requested for spot symbol {sym}, skipping (not supported)")

    subscribe_msg = {
        "method": "SUBSCRIBE",
        "params": streams,
        "id": int(time.time()*1000),
    }
    
    if not streams:
        log.warning(f"Binance: no streams to subscribe to for config={config}")
        return
    
    log.info(f"Binance subscribing to streams: {streams}")

    #
    # ------------------------------------------------------------------------
    # Per-symbol L2 books
    # ------------------------------------------------------------------------
    #
    books: Dict[str, OrderBookL2] = {
        sym: OrderBookL2(sym) for sym in symbols
    }

    #
    # ------------------------------------------------------------------------
    # Reconnect loop
    # ------------------------------------------------------------------------
    #
    while True:
        try:
            async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(json.dumps(subscribe_msg))
                log.info(f"Binance: Connected and subscribed to {len(streams)} streams")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError as e:
                        log.error(f"Binance: Failed to parse message: {e}")
                        continue
                    
                    # Skip subscription confirmation messages
                    if "result" in msg or "id" in msg:
                        log.debug(f"Binance: Subscription response: {msg}")
                        continue
                    
                    if "stream" not in msg:
                        log.debug(f"Binance: Message without stream field: {msg}")
                        continue

                    stream = msg["stream"]
                    payload = msg.get("data")
                    if not payload:
                        continue

                    # Determine symbol from stream name
                    # e.g. btcusdt@trade → BTC-USDT
                    try:
                        base = stream.split("@")[0]
                        symbol = to_canonical_symbol(base)
                    except Exception as e:
                        log.error(f"Binance: Failed to parse symbol from stream {stream}: {e}")
                        continue

                    #
                    # --------------------------------------------------------
                    # Dispatch by event type
                    # --------------------------------------------------------
                    #
                    event_type = payload.get("e")

                    # ---------------------
                    #  Orderbook (depth)
                    # ---------------------
                    if event_type == "depthUpdate":
                        ob = books.get(symbol)
                        if not ob:
                            log.warning(f"Binance: Received orderbook update for unknown symbol {symbol}")
                            continue

                        # Sequence checks
                        u = payload.get("u")          # final update id
                        U = payload.get("U")          # first update id
                        
                        if u is None or U is None:
                            log.warning(f"Binance: Missing sequence numbers in orderbook update")
                            continue

                        # First time? We require a snapshot
                        if not ob.initialized:
                            try:
                                await _seed_snapshot(ob, symbol, market_type)
                                log.info(f"Binance: Loaded snapshot for {symbol}, last_update_id={ob.last_update_id}")
                            except Exception as e:
                                log.error(f"Binance: Failed to load snapshot for {symbol}: {e}")
                                continue
                            
                            # Only apply if update follows snapshot
                            if U > ob.last_update_id + 1:
                                log.warning(f"Binance: Update {U} too far ahead of snapshot {ob.last_update_id}, skipping")
                                continue

                        # Relaxed sequence check: allow small gaps or re-init on large gaps
                        if U > ob.last_update_id + 1:
                            log.warning(f"Binance: Sequence gap detected ({ob.last_update_id} -> {U}), resyncing")
                            try:
                                await _seed_snapshot(ob, symbol, market_type)
                            except Exception as e:
                                log.error(f"Binance: Failed to resync snapshot: {e}")
                            continue

                        # Apply deltas
                        try:
                            deltas = ob.apply_delta(
                                payload.get("b", []), payload.get("a", []), update_id=u
                            )
                        except Exception as e:
                            log.error(f"Binance: Failed to apply deltas: {e}")
                            continue

                        # Emit microdeltas
                        ts_ex = payload.get("E")
                        ts_ev = now_ms()
                        for d in deltas:
                            yield {
                                "type": "lob.update",
                                "exchange": "binance",
                                "symbol": symbol,
                                "ts_event": ts_ev,
                                "ts_exchange": ts_ex,
                                "side": d["side"],
                                "px": d["price"],
                                "qty": d["qty"],
                            }
                        continue

                    # ---------------------
                    # Trades
                    # ---------------------
                    if event_type == "trade":
                        ts_ex = payload.get("T")
                        yield {
                            "type": "trade",
                            "exchange": "binance",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": ts_ex,
                            "side": "buy" if payload["m"] is False else "sell",
                            "px": float(payload["p"]),
                            "size": float(payload["q"]),
                            "trade_id": payload.get("t"),
                        }
                        continue

                    # ---------------------
                    # Ticker
                    # ---------------------
                    if event_type == "24hrTicker":
                        ts_ex = payload.get("E")
                        yield {
                            "type": "ticker",
                            "exchange": "binance",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": ts_ex,
                            "last": float(payload["c"]),
                            "mark": None,
                            "index": None,
                            "open_interest": None,
                        }
                        continue

                    # ---------------------
                    # Funding (from markPrice stream)
                    # ---------------------
                    if event_type == "markPriceUpdate":
                        ts_ex = payload.get("E")
                        funding_rate = payload.get("r")
                        if funding_rate is not None:
                            yield {
                                "type": "funding.update",
                                "exchange": "binance",
                                "symbol": symbol,
                                "ts_event": now_ms(),
                                "ts_exchange": ts_ex,
                                "rate": float(funding_rate),
                                "next_funding": payload.get("T"),
                                "mark": float(payload.get("p", 0)),
                                "index": float(payload.get("i", 0)),
                            }
                        continue

                    # ---------------------
                    # Kline (ohlcv)
                    # ---------------------
                    if event_type == "kline":
                        k = payload["k"]
                        yield {
                            "type": "ohlcv",
                            "exchange": "binance",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": payload.get("E"),
                            "ts": k["t"],
                            "open": float(k["o"]),
                            "high": float(k["h"]),
                            "low": float(k["l"]),
                            "close": float(k["c"]),
                            "volume": float(k["v"]),
                        }
                        continue

                    # ---------------------
                    # Open interest (futures)
                    # ---------------------
                    if event_type == "openInterest":
                        ts_ex = payload.get("E")
                        yield {
                            "type": "oi.update",
                            "exchange": "binance",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": ts_ex,
                            "oi": float(payload["oi"]),
                        }
                        continue

        except Exception as e:
            log.error(f"Binance connector error: {type(e).__name__}: {e}", exc_info=True)
            await asyncio.sleep(2.0)
            continue


#
# ------------------------------------------------------------------------
# Helper: fetch initial orderbook snapshot
# ------------------------------------------------------------------------
#
async def _seed_snapshot(ob: OrderBookL2, symbol: str, market_type: str):
    """Fetch orderbook snapshot from REST API"""
    base = symbol.replace("-", "").upper()  # Binance uses uppercase
    
    if market_type == "perp":
        url = f"https://fapi.binance.com/fapi/v1/depth?symbol={base}&limit=1000"
    else:
        url = f"https://api.binance.com/api/v3/depth?symbol={base}&limit=1000"

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    text = await r.text()
                    raise Exception(f"HTTP {r.status}: {text}")
                snapshot = await r.json()
        
        if "lastUpdateId" not in snapshot:
            raise ValueError(f"Invalid snapshot response: missing lastUpdateId")
        
        ob.load_snapshot(
            bids=snapshot.get("bids", []),
            asks=snapshot.get("asks", []),
            last_update_id=snapshot["lastUpdateId"],
        )
        log.info(f"Binance: Loaded snapshot for {symbol} with {len(snapshot.get('bids', []))} bids, {len(snapshot.get('asks', []))} asks")
    except Exception as e:
        log.error(f"Binance: Failed to fetch snapshot for {symbol}: {e}")
        raise

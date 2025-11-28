from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Dict

import websockets
import aiohttp

from engine.schemas import StreamConfig
from engine.exchange_utils import (
    OrderBookL2,
    to_canonical_symbol,
    now_ms,
)

log = logging.getLogger(__name__)

# WS endpoints
BYBIT_SPOT_WS = "wss://stream.bybit.com/v5/public/spot"
BYBIT_PERP_WS = "wss://stream.bybit.com/v5/public/linear"


async def stream_exchange(config: StreamConfig) -> AsyncIterator[dict]:
    """
    Unified Bybit connector.
    Produces normalized events:
        - lob.update
        - trade
        - ticker
        - funding.update
        - ohlcv
        - oi.update
    """

    symbols = config.symbols
    # Infer perp if any symbol endswith PERP; otherwise spot
    is_perp = any(sym.upper().endswith("PERP") or sym.upper().endswith("-PERP") for sym in symbols)
    ws_url = BYBIT_PERP_WS if is_perp else BYBIT_SPOT_WS
    
    log.info(f"Bybit connector: market_type={'perp' if is_perp else 'spot'}, symbols={symbols}, ws_url={ws_url}")

    #
    # ---------------------------------------------------------------------
    # Subscriptions
    # ---------------------------------------------------------------------
    #
    subs = []
    for sym in symbols:
        s_raw = sym.replace("-", "").upper()  # Bybit expects uppercase

        if config.channels.orderbook.enabled:
            depth = config.channels.orderbook.depth or 50
            subs.append({"topic": f"orderbook.{depth}.{s_raw}"})

        if config.channels.trades:
            subs.append({"topic": f"publicTrade.{s_raw}"})

        if config.channels.ticker:
            subs.append({"topic": f"tickers.{s_raw}"})

        if config.channels.funding:
            if is_perp:
                # Use tickers for funding on perp (it includes funding rate)
                pass  # Already subscribed via tickers
            else:
                log.warning(f"Bybit: funding requested for spot symbol {sym}, skipping (not supported)")

        if getattr(config.channels, "ohlcv", None) and config.channels.ohlcv.enabled:
            interval = config.channels.ohlcv.interval or "1"
            subs.append({"topic": f"kline.{interval}.{s_raw}"})

        if config.channels.open_interest:
            if is_perp:
                # Open interest is available in tickers stream for perp
                pass  # Already included in tickers
            else:
                log.warning(f"Bybit: open interest requested for spot symbol {sym}, skipping (not supported)")

    subscribe_msg = {
        "op": "subscribe",
        "args": [sub["topic"] for sub in subs],
    }
    
    if not subs:
        log.warning(f"Bybit: no subscriptions to make for config={config}")
        return
    
    log.info(f"Bybit subscribing to: {[s['topic'] for s in subs]}")

    #
    # ---------------------------------------------------------------------
    # Per-symbol orderbook states
    # ---------------------------------------------------------------------
    #
    books: Dict[str, OrderBookL2] = {
        sym: OrderBookL2(sym) for sym in symbols
    }

    #
    # ---------------------------------------------------------------------
    # Reconnect loop
    # ---------------------------------------------------------------------
    #
    while True:
        try:
            async with websockets.connect(ws_url, max_size=None, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(json.dumps(subscribe_msg))
                log.info(f"Bybit: Connected and subscribed to {len(subs)} topics")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError as e:
                        log.error(f"Bybit: Failed to parse message: {e}")
                        continue

                    # Handle subscription responses
                    if "op" in msg:
                        log.debug(f"Bybit: Operation response: {msg}")
                        continue
                    
                    if "topic" not in msg or "data" not in msg:
                        log.debug(f"Bybit: Message without topic/data: {msg}")
                        continue

                    topic = msg["topic"]
                    data = msg["data"]

                    # Extract symbol (end of topic)
                    try:
                        s_raw = topic.split(".")[-1]
                        symbol = to_canonical_symbol(s_raw)
                    except Exception as e:
                        log.error(f"Bybit: Failed to parse symbol from topic {topic}: {e}")
                        continue

                    #
                    # -------------------------------------------------------
                    # Orderbook
                    # -------------------------------------------------------
                    #
                    if topic.startswith("orderbook"):
                        ob = books.get(symbol)
                        if not ob:
                            log.warning(f"Bybit: Received orderbook update for unknown symbol {symbol}")
                            continue
                        
                        # data may contain:
                        #   "type": "snapshot" or "delta"
                        #   "u": sequence id (updateId)
                        #   "b": bids [[px, qty], ...]
                        #   "a": asks [[px, qty], ...]

                        update_type = data.get("type")
                        u = data.get("u")
                        
                        if u is None:
                            log.warning(f"Bybit: Orderbook update missing sequence 'u'")
                            continue

                        # Initial snapshot
                        if update_type == "snapshot":
                            ob.load_snapshot(
                                bids=data.get("b", []),
                                asks=data.get("a", []),
                                last_update_id=u,
                            )
                            log.info(f"Bybit: Loaded snapshot for {symbol}, last_update_id={u}")
                            continue

                        # Delta
                        if update_type == "delta":
                            # Enforce sequence - initialize if needed
                            if not ob.initialized:
                                try:
                                    await _seed_snapshot(ob, symbol, is_perp)
                                    log.info(f"Bybit: Fetched initial snapshot for {symbol}")
                                except Exception as e:
                                    log.error(f"Bybit: Failed to fetch snapshot for {symbol}: {e}")
                                    continue
                                
                                # Skip if update is stale
                                if u <= ob.last_update_id:
                                    log.debug(f"Bybit: Skipping stale update {u} <= {ob.last_update_id}")
                                    continue

                            # Relaxed sequence check: allow small gaps
                            if u > ob.last_update_id + 1:
                                log.warning(f"Bybit: Sequence gap ({ob.last_update_id} -> {u}), resyncing")
                                try:
                                    await _seed_snapshot(ob, symbol, is_perp)
                                except Exception as e:
                                    log.error(f"Bybit: Failed to resync: {e}")
                                continue

                            try:
                                deltas = ob.apply_delta(
                                    data.get("b", []),
                                    data.get("a", []),
                                    update_id=u,
                                )
                            except Exception as e:
                                log.error(f"Bybit: Failed to apply delta: {e}")
                                continue

                            ts_ex = msg.get("ts")
                            for d in deltas:
                                yield {
                                    "type": "lob.update",
                                    "exchange": "bybit",
                                    "symbol": symbol,
                                    "ts_event": now_ms(),
                                    "ts_exchange": ts_ex,
                                    "side": d["side"],
                                    "px": d["price"],
                                    "qty": d["qty"],
                                }
                            continue

                    #
                    # -------------------------------------------------------
                    # Trades
                    # -------------------------------------------------------
                    #
                    if topic.startswith("publicTrade"):
                        # data is a list
                        for t in data:
                            yield {
                                "type": "trade",
                                "exchange": "bybit",
                                "symbol": symbol,
                                "ts_event": now_ms(),
                                "ts_exchange": t.get("T"),
                                "px": float(t["p"]),
                                "size": float(t["v"]),
                                "side": t["S"].lower(),  # Buy/Sell
                                "trade_id": t.get("i"),
                            }
                        continue

                    #
                    # -------------------------------------------------------
                    # Ticker
                    # -------------------------------------------------------
                    #
                    if topic.startswith("tickers"):
                        try:
                            ticker_event = {
                                "type": "ticker",
                                "exchange": "bybit",
                                "symbol": symbol,
                                "ts_event": now_ms(),
                                "ts_exchange": msg.get("ts"),
                                "last": float(data.get("lastPrice", 0)),
                                "mark": float(data.get("markPrice")) if is_perp and data.get("markPrice") else None,
                                "index": float(data.get("indexPrice")) if is_perp and data.get("indexPrice") else None,
                                "open_interest": None,
                            }
                            yield ticker_event
                            
                            # Emit funding if present (perp only)
                            if is_perp and config.channels.funding:
                                funding_rate = data.get("fundingRate")
                                if funding_rate is not None:
                                    yield {
                                        "type": "funding.update",
                                        "exchange": "bybit",
                                        "symbol": symbol,
                                        "ts_event": now_ms(),
                                        "ts_exchange": msg.get("ts"),
                                        "rate": float(funding_rate),
                                        "next_funding": data.get("nextFundingTime"),
                                        "mark": float(data.get("markPrice", 0)) if data.get("markPrice") else None,
                                        "index": float(data.get("indexPrice", 0)) if data.get("indexPrice") else None,
                                    }
                            
                            # Emit OI if present (perp only)
                            if is_perp and config.channels.open_interest:
                                oi = data.get("openInterest")
                                if oi is not None:
                                    yield {
                                        "type": "oi.update",
                                        "exchange": "bybit",
                                        "symbol": symbol,
                                        "ts_event": now_ms(),
                                        "ts_exchange": msg.get("ts"),
                                        "oi": float(oi),
                                    }
                        except (KeyError, ValueError, TypeError) as e:
                            log.error(f"Bybit: Failed to parse ticker data: {e}")
                        continue

                    #
                    # -------------------------------------------------------
                    # OHLCV
                    # -------------------------------------------------------
                    #
                    if topic.startswith("kline"):
                        k = data
                        yield {
                            "type": "ohlcv",
                            "exchange": "bybit",
                            "symbol": symbol,
                            "ts_event": now_ms(),
                            "ts_exchange": msg.get("ts"),
                            "ts": int(k["start"]),
                            "open": float(k["open"]),
                            "high": float(k["high"]),
                            "low": float(k["low"]),
                            "close": float(k["close"]),
                            "volume": float(k["volume"]),
                        }
                        continue

                    #
                    # -------------------------------------------------------
                    # Open Interest (if separate channel, though usually in tickers)
                    # -------------------------------------------------------
                    #
                    if topic.startswith("openInterest"):
                        try:
                            yield {
                                "type": "oi.update",
                                "exchange": "bybit",
                                "symbol": symbol,
                                "ts_event": now_ms(),
                                "ts_exchange": msg.get("ts"),
                                "oi": float(data["openInterest"]),
                            }
                        except (KeyError, ValueError, TypeError) as e:
                            log.error(f"Bybit: Failed to parse OI data: {e}")
                        continue

        except Exception as e:
            log.error(f"Bybit connector error: {type(e).__name__}: {e}", exc_info=True)
            await asyncio.sleep(2.0)
            continue


#
# ---------------------------------------------------------------------
# Helper: fetch initial snapshot
# ---------------------------------------------------------------------
#
async def _seed_snapshot(ob: OrderBookL2, symbol: str, is_perp: bool):
    """Fetch orderbook snapshot from REST API"""
    base = symbol.replace("-", "").upper()

    if is_perp:
        url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={base}&limit=200"
    else:
        url = f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={base}&limit=200"

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    text = await r.text()
                    raise Exception(f"HTTP {r.status}: {text}")
                js = await r.json()
        
        if "result" not in js:
            raise ValueError(f"Invalid response: {js}")
        
        result = js["result"]
        # Bybit v5 returns data differently
        if "b" in result and "a" in result:
            # Direct format
            bids = result.get("b", [])
            asks = result.get("a", [])
            update_id = int(result.get("u", 0))
        elif "list" in result and len(result["list"]) > 0:
            # List format
            entry = result["list"][0]
            bids = entry.get("b", [])
            asks = entry.get("a", [])
            update_id = int(entry.get("u", 0))
        else:
            raise ValueError(f"Unexpected result format: {result}")
        
        ob.load_snapshot(
            bids=bids,
            asks=asks,
            last_update_id=update_id,
        )
        log.info(f"Bybit: Loaded snapshot for {symbol} with {len(bids)} bids, {len(asks)} asks, u={update_id}")
    except Exception as e:
        log.error(f"Bybit: Failed to fetch snapshot for {symbol}: {e}")
        raise

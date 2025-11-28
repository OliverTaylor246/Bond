"""
Lighter connector implementing the unified stream_exchange API.

This module:
- Resolves Lighter market indices from canonical symbols via REST /api/v1/orderBooks
- Subscribes to public WS channels:
    * order_book/{MARKET_INDEX}  -> lob.update (L2 microdeltas + periodic snapshots)
    * trade/{MARKET_INDEX}       -> trade
    * market_stats/{MARKET_INDEX}-> ticker, funding.update, oi.update
- Maintains a simple in-memory L2 book per market for snapshot emission
- Yields ONLY normalized dict events; no Redis, no aggregation, no heartbeats
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import aiohttp
import websockets
from websockets.client import WebSocketClientProtocol

from engine.schemas import StreamConfig

log = logging.getLogger(__name__)

WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
REST_BASE_URL = "https://mainnet.zklighter.elliot.ai"

# How often to emit full-book snapshots per symbol (ms)
SNAPSHOT_INTERVAL_MS = 5_000


# ---------- Small helpers ----------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_float(val: Any) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _canonical_symbol(symbol: str) -> str:
    """
    Normalize symbols to your canonical format.

    Lighter tends to use 'BTC-USD' style symbols already. If your
    StreamConfig uses the same, this can be a no-op.
    """
    s = symbol.upper().replace("_", "-").replace("/", "-")
    # If no dash, try to split on common quotes
    if "-" not in s:
        quotes = ("USDT", "USD", "USDC")
        for q in quotes:
            if s.endswith(q) and len(s) > len(q):
                base = s[: -len(q)]
                return f"{base}-{q}"
    return s


@dataclass
class L2BookSide:
    # price -> size
    levels: Dict[float, float] = field(default_factory=dict)

    def apply_updates(self, updates: Iterable[Mapping[str, Any]]) -> List[Dict[str, float]]:
        """
        Apply a list of {price,size} changes.

        Lighter WS docs show each ask/bid entry as:
            {"price": "3335.65", "size": "0.1187"}

        We interpret zero / negative size as deletion.
        Returns the list of changed levels as normalized dicts
        (this becomes the microdelta payload).
        """
        changed: List[Dict[str, float]] = []
        for u in updates:
            px = _safe_float(u.get("price"))
            sz = _safe_float(u.get("size"))
            if px is None or sz is None:
                continue

            if sz <= 0:
                # delete level
                if px in self.levels:
                    self.levels.pop(px, None)
                    changed.append({"px": px, "qty": 0.0})
            else:
                self.levels[px] = sz
                changed.append({"px": px, "qty": sz})
        return changed

    def snapshot(self, depth: Optional[int] = None, reverse: bool = False) -> List[Dict[str, float]]:
        prices = sorted(self.levels.keys(), reverse=reverse)
        if depth is not None and depth > 0:
            prices = prices[:depth]
        return [{"px": px, "qty": self.levels[px]} for px in prices]


@dataclass
class L2BookState:
    bids: L2BookSide = field(default_factory=L2BookSide)
    asks: L2BookSide = field(default_factory=L2BookSide)
    last_offset: Optional[int] = None
    last_snapshot_ms: int = 0


# ---------- Market metadata & symbol mapping ----------


async def _fetch_order_books_metadata(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    """
    Fetch /api/v1/orderBooks metadata and return it as a list of dicts.
    """
    url = f"{REST_BASE_URL}/api/v1/orderBooks"
    resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=10))
    resp.raise_for_status()
    data = await resp.json()

    # Lighter returns {"code": 200, "order_books": [...]}
    if isinstance(data, dict) and "order_books" in data:
        return data["order_books"]
    
    # Fallback: try to find a list
    if isinstance(data, list):
        return data
    
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v

    log.warning("Unexpected orderBooks response shape from Lighter: %r", type(data))
    return []


def _build_symbol_maps(
    rows: Iterable[Mapping[str, Any]]
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Build (symbol -> market_id, market_id -> symbol) maps.

    We look for likely key names by convention (symbol, ticker, name; market_id, index, id, market_index).
    """
    sym_to_id: Dict[str, str] = {}
    id_to_sym: Dict[str, str] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            continue

        # Try a few candidates for the symbol field
        symbol = (
            row.get("symbol")
            or row.get("ticker")
            or row.get("name")
            or row.get("pair")
            or row.get("market")
        )
        market_id = (
            row.get("market_id")
            or row.get("market_index")
            or row.get("index")
            or row.get("id")
        )

        if symbol is None or market_id is None:
            continue

        sym = _canonical_symbol(str(symbol))
        mid = str(market_id)

        sym_to_id[sym] = mid
        id_to_sym[mid] = sym

    return sym_to_id, id_to_sym


async def _resolve_markets(
    config: StreamConfig,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Resolve config.symbols -> Lighter market indices.

    Returns (symbol->market_id, market_id->symbol).
    If a symbol is not found in metadata but looks like a numeric ID, we fall back
    to treating it as the market_id directly.
    """
    wanted_symbols = [s for s in getattr(config, "symbols", []) or []]

    if not wanted_symbols:
        log.warning("Lighter stream_exchange called with empty symbols list")
        return {}, {}

    async with aiohttp.ClientSession() as session:
        rows = await _fetch_order_books_metadata(session)

    sym_to_id_all, id_to_sym_all = _build_symbol_maps(rows)

    sym_to_id: Dict[str, str] = {}
    id_to_sym: Dict[str, str] = {}

    for raw_sym in wanted_symbols:
        sym = _canonical_symbol(raw_sym)
        mid = sym_to_id_all.get(sym)
        
        # If not found, try just the base currency (Lighter uses 'BTC' not 'BTC-USDT')
        if mid is None and "-" in sym:
            base = sym.split("-")[0]
            mid = sym_to_id_all.get(base)
            if mid:
                sym = base  # Use the simpler Lighter symbol
        
        if mid is None:
            # Fallback: user may have given a numeric market index directly
            if str(raw_sym).isdigit():
                mid = str(raw_sym)
                sym = id_to_sym_all.get(mid, sym)  # recover symbol if we can
                log.info(
                    "Lighter symbol %s not found in metadata, treating as market_index=%s",
                    raw_sym,
                    mid,
                )
            else:
                log.error("Lighter: unable to resolve symbol %s to a market index", raw_sym)
                continue

        sym_to_id[sym] = mid
        id_to_sym[mid] = sym

    if not sym_to_id:
        log.error("Lighter: no symbols could be resolved from StreamConfig=%r", wanted_symbols)

    return sym_to_id, id_to_sym


# ---------- Normalizers ----------


def _normalize_order_book_event(
    raw: Mapping[str, Any],
    market_id_to_symbol: Mapping[str, str],
    books: MutableMapping[str, L2BookState],
    depth: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Turn a raw 'update/order_book' WS message into:
      - One microdelta lob.update event (only changed levels), and
      - Optionally, a periodic full snapshot lob.update event.
    """
    channel = raw.get("channel")  # e.g. "order_book:0"
    if not isinstance(channel, str) or ":" not in channel:
        return []

    _, market_id = channel.split(":", 1)
    symbol = market_id_to_symbol.get(market_id)
    if symbol is None:
        # Unknown market id; skip
        return []

    ob = raw.get("order_book") or {}
    if not isinstance(ob, Mapping):
        return []

    # Enforce simple monotonic offset (best-effort)
    offset = raw.get("offset") or ob.get("offset")
    timestamp = ob.get("timestamp")  # Lighter docs show this exists
    ts_exchange = int(timestamp) * 1000 if isinstance(timestamp, (int, float)) else None
    ts_event = _now_ms()

    state = books.setdefault(market_id, L2BookState())

    if isinstance(offset, int):
        if state.last_offset is not None and offset <= state.last_offset:
            # stale or duplicate
            return []
        state.last_offset = offset

    asks_raw = ob.get("asks") or []
    bids_raw = ob.get("bids") or []

    # Apply updates to in-memory book and capture the changed levels
    delta_asks = state.asks.apply_updates(asks_raw)
    delta_bids = state.bids.apply_updates(bids_raw)

    events: List[Dict[str, Any]] = []

    if delta_bids or delta_asks:
        events.append(
            {
                "type": "lob.update",
                "exchange": "lighter",
                "symbol": symbol,
                "level": "l2",
                "is_snapshot": False,
                "ts_event": ts_event,
                "ts_exchange": ts_exchange or ts_event,
                "bids": delta_bids,
                "asks": delta_asks,
                "meta": {
                    "market_id": market_id,
                    "offset": offset,
                },
            }
        )

    # Periodic full snapshot
    if ts_event - state.last_snapshot_ms >= SNAPSHOT_INTERVAL_MS:
        state.last_snapshot_ms = ts_event
        snap_bids = state.bids.snapshot(depth=depth, reverse=True)
        snap_asks = state.asks.snapshot(depth=depth, reverse=False)
        events.append(
            {
                "type": "lob.update",
                "exchange": "lighter",
                "symbol": symbol,
                "level": "l2",
                "is_snapshot": True,
                "ts_event": ts_event,
                "ts_exchange": ts_exchange or ts_event,
                "bids": snap_bids,
                "asks": snap_asks,
                "meta": {
                    "market_id": market_id,
                    "offset": offset,
                    "snapshot_depth": depth,
                },
            }
        )

    return events


def _normalize_trade_events(
    raw: Mapping[str, Any],
    market_id_to_symbol: Mapping[str, str],
) -> List[Dict[str, Any]]:
    """
    Turn a raw trade WS message into a list of trade events.
    Lighter format: {"channel": "trade:1", "trades": [...], "nonce": ...}
    """
    channel = raw.get("channel")  # e.g. "trade:1"
    if not isinstance(channel, str) or ":" not in channel:
        return []

    _, market_id = channel.split(":", 1)
    symbol = market_id_to_symbol.get(market_id)
    if symbol is None:
        return []

    trades = raw.get("trades")
    if not trades or not isinstance(trades, list):
        return []

    out: List[Dict[str, Any]] = []
    ts_event = _now_ms()

    for tr in trades:
        if not isinstance(tr, Mapping):
            continue

        price = _safe_float(tr.get("price"))
        size = _safe_float(tr.get("size"))
        if price is None or size is None:
            continue

        # Lighter doesn't provide timestamp per trade, use event time
        # Determine side from trade type or use a default
        trade_type = tr.get("type", "")
        side = "buy"  # Default, Lighter doesn't clearly indicate side in these messages

        out.append(
            {
                "type": "trade",
                "exchange": "lighter",
                "symbol": symbol,
                "ts_event": ts_event,
                "ts_exchange": ts_event,  # Lighter doesn't provide per-trade timestamp
                "px": price,
                "size": size,
                "side": side,
                "trade_id": str(tr.get("trade_id") or ""),
            }
        )

    return out


def _normalize_market_stats_events(
    raw: Mapping[str, Any],
    market_id_to_symbol: Mapping[str, str],
) -> List[Dict[str, Any]]:
    """
    Turn a raw 'update/market_stats' WS message into:
      - ticker event
      - funding.update event
      - oi.update event
    """
    channel = raw.get("channel")  # e.g. "market_stats:0"
    if not isinstance(channel, str) or ":" not in channel:
        return []

    _, market_id = channel.split(":", 1)
    symbol = market_id_to_symbol.get(market_id)
    if symbol is None:
        return []

    ms = raw.get("market_stats") or {}
    if not isinstance(ms, Mapping):
        return []

    ts_event = _now_ms()
    funding_ts = ms.get("funding_timestamp")
    ts_exchange = int(funding_ts) if isinstance(funding_ts, (int, float)) else ts_event

    index_price = _safe_float(ms.get("index_price"))
    mark_price = _safe_float(ms.get("mark_price"))
    last_price = _safe_float(ms.get("last_trade_price"))
    oi = _safe_float(ms.get("open_interest"))
    cur_funding = _safe_float(ms.get("current_funding_rate"))
    next_funding = _safe_float(ms.get("funding_rate"))

    events: List[Dict[str, Any]] = []

    # Ticker-style event
    events.append(
        {
            "type": "ticker",
            "exchange": "lighter",
            "symbol": symbol,
            "ts_event": ts_event,
            "ts_exchange": ts_exchange,
            "index_price": index_price,
            "mark_price": mark_price,
            "last_price": last_price,
            "open_interest": oi,
            "meta": {
                "market_id": market_id,
            },
        }
    )

    # Funding event (if rates present)
    if cur_funding is not None or next_funding is not None:
        events.append(
            {
                "type": "funding.update",
                "exchange": "lighter",
                "symbol": symbol,
                "ts_event": ts_event,
                "ts_exchange": ts_exchange,
                "current_funding_rate": cur_funding,
                "next_funding_rate": next_funding,
                "funding_timestamp": funding_ts,
                "meta": {
                    "market_id": market_id,
                },
            }
        )

    # Open-interest event
    if oi is not None:
        events.append(
            {
                "type": "oi.update",
                "exchange": "lighter",
                "symbol": symbol,
                "ts_event": ts_event,
                "ts_exchange": ts_exchange,
                "open_interest": oi,
                "meta": {
                    "market_id": market_id,
                },
            }
        )

    return events


# ---------- Channel selection from StreamConfig ----------


@dataclass
class EnabledChannels:
    orderbook: bool = False
    trades: bool = False
    ticker: bool = False
    funding: bool = False
    oi: bool = False
    # ohlcv intentionally omitted for now; Lighter OHLCV is REST /candlesticks.


def _get_enabled_channels(config: StreamConfig) -> EnabledChannels:
    """
    Inspect config.channels and figure out which Lighter WS channels to subscribe to.

    This is defensive against minor schema changes: we use getattr with defaults
    instead of assuming exact attribute names.
    """
    ch = getattr(config, "channels", None)
    if ch is None:
        # If channels are omitted entirely, default to trades + orderbook
        return EnabledChannels(orderbook=True, trades=True, ticker=True, funding=True, oi=True)

    def _enabled(obj: Any) -> bool:
        if obj is None:
            return False
        if isinstance(obj, bool):
            return obj
        return bool(getattr(obj, "enabled", False))

    return EnabledChannels(
        orderbook=_enabled(getattr(ch, "orderbook_l2", getattr(ch, "orderbook", None))),
        trades=_enabled(getattr(ch, "trades", None)),
        ticker=_enabled(getattr(ch, "ticker", None)),
        funding=_enabled(getattr(ch, "funding", None)),
        oi=_enabled(getattr(ch, "open_interest", getattr(ch, "oi", None))),
    )


# ---------- Main async generator ----------


async def _subscribe(
    ws: WebSocketClientProtocol,
    market_ids: Iterable[str],
    chan: EnabledChannels,
) -> None:
    """
    Send the appropriate subscription messages for enabled channels.
    """
    subs: List[Dict[str, Any]] = []

    for mid in market_ids:
        if chan.orderbook:
            subs.append({"type": "subscribe", "channel": f"order_book/{mid}"})
        if chan.trades:
            subs.append({"type": "subscribe", "channel": f"trade/{mid}"})
        # market_stats carries ticker, funding, oi
        if chan.ticker or chan.funding or chan.oi:
            subs.append({"type": "subscribe", "channel": f"market_stats/{mid}"})

    for msg in subs:
        await ws.send(json.dumps(msg))


async def stream_exchange(config: StreamConfig) -> AsyncIterator[dict]:
    """
    Unified Lighter exchange stream.

    Responsibilities here:
    - Resolve symbols to market indices (REST)
    - Connect WS, subscribe as per config.channels and config.symbols
    - Route messages to per-channel normalizers
    - Yield normalized dict events
    - Handle reconnects with simple backoff
    """
    sym_to_id, id_to_sym = await _resolve_markets(config)
    if not sym_to_id:
        # No symbols resolved; nothing to stream
        return

    enabled = _get_enabled_channels(config)
    market_ids = list(id_to_sym.keys())

    books: Dict[str, L2BookState] = {}

    backoff = 1.0
    max_backoff = 30.0

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                log.info(
                    "Connected to Lighter WS: %s (markets=%s, channels=%s)",
                    WS_URL,
                    market_ids,
                    enabled,
                )
                backoff = 1.0  # reset backoff on successful connect

                await _subscribe(ws, market_ids, enabled)

                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except Exception:
                        log.exception("Lighter: failed to decode WS message: %r", raw_msg)
                        continue

                    # Lighter uses channel field to identify message type
                    channel = msg.get("channel", "")
                    msg_type = msg.get("type")
                    
                    # Skip connection messages
                    if msg_type == "connected":
                        continue

                    events: List[Dict[str, Any]] = []

                    # Route based on channel prefix
                    if channel.startswith("order_book:") and enabled.orderbook:
                        events.extend(
                            _normalize_order_book_event(
                                msg,
                                market_id_to_symbol=id_to_sym,
                                books=books,
                                depth=getattr(
                                    getattr(getattr(config, "channels", None), "orderbook_l2", None),
                                    "depth",
                                    None,
                                ),
                            )
                        )
                    elif channel.startswith("trade:") and enabled.trades:
                        events.extend(_normalize_trade_events(msg, market_id_to_symbol=id_to_sym))
                    elif channel.startswith("market_stats:") and (
                        enabled.ticker or enabled.funding or enabled.oi
                    ):
                        events.extend(
                            _normalize_market_stats_events(msg, market_id_to_symbol=id_to_sym)
                        )
                    else:
                        # Ignore other message types (account, liquidations, etc.)
                        continue

                    for ev in events:
                        yield ev

        except Exception as e:
            log.warning("Lighter WS connection error: %s, reconnecting in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)

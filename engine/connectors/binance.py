from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Set

import websockets
from websockets.exceptions import ConnectionClosed

from .base import ConnectorError, ExchangeConnector
from ..schemas import (
    BaseEvent,
    BookUpdateType,
    MarketType,
    OrderBook,
    PriceSize,
    Side,
    Ticker,
    Trade,
    normalize_symbol,
)

logger = logging.getLogger(__name__)


class _BookState:
    def __init__(self, depth: int):
        self.depth = depth
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.sequence: Optional[int] = None
        self.prev_sequence: Optional[int] = None
        self.is_reset: bool = False

    def _apply_side(self, side: str, levels: List[PriceSize]) -> None:
        book = self.bids if side == "bids" else self.asks
        for price, size in levels:
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size

    def snapshot(self, bids: List[PriceSize], asks: List[PriceSize], sequence: Optional[int], prev_seq: Optional[int]) -> None:
        self.bids = {p: s for p, s in bids}
        self.asks = {p: s for p, s in asks}
        self.sequence = sequence
        self.prev_sequence = prev_seq
        self.is_reset = False

    def apply_delta(self, bids: List[PriceSize], asks: List[PriceSize], sequence: Optional[int], prev_seq: Optional[int]) -> bool:
        if self.sequence is not None and prev_seq is not None and self.sequence != prev_seq:
            return False
        self._apply_side("bids", bids)
        self._apply_side("asks", asks)
        self.prev_sequence = prev_seq
        self.sequence = sequence
        self.is_reset = False
        return True

    def top(self) -> tuple[List[PriceSize], List[PriceSize]]:
        bid_list = sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)
        ask_list = sorted(self.asks.items(), key=lambda kv: kv[0])
        depth = self.depth
        if depth:
            bid_list = bid_list[:depth]
            ask_list = ask_list[:depth]
        return bid_list, ask_list


class BinanceConnector(ExchangeConnector):
    exchange = "binance"
    _WS_URL = "wss://stream.binance.com:9443/stream"
    _PING_INTERVAL_SEC = 20
    _INITIAL_BACKOFF_SEC = 1.0
    _MAX_BACKOFF_SEC = 30.0

    def __init__(self):
        super().__init__()
        self._ws_url = self._WS_URL
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._depth: int = 20
        self._raw_mode = False
        self._params: Set[str] = set()
        self._current_params: Set[str] = set()
        self._subscription_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._run_task: Optional[asyncio.Task[None]] = None
        self._ping_task: Optional[asyncio.Task[None]] = None
        self._books: Dict[str, "_BookState"] = {}

    async def connect(self) -> None:
        if self._run_task and not self._run_task.done():
            return
        self._running = True
        self._run_task = asyncio.create_task(self._run_loop())

    async def disconnect(self) -> None:
        self._running = False
        if self._run_task:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
            self._run_task = None
        await self._cleanup_connection()

    async def subscribe(
        self,
        *,
        symbols: Sequence[str],
        channels: Sequence[str],
        depth: int | None = None,
        raw: bool = False,
    ) -> None:
        normalized_symbols: List[str] = []
        missing: List[str] = []
        for raw_symbol in symbols:
            normalized = normalize_symbol(self.exchange, raw_symbol)
            if not normalized or "/" not in normalized:
                missing.append(str(raw_symbol))
                continue
            normalized_symbols.append(normalized)
        if missing and not normalized_symbols:
            raise ConnectorError(f"Binance cannot resolve symbols: {', '.join(missing)}")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by Binance")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError("Binance requires at least one channel to subscribe to")
        depth = depth or self._depth
        resolved = list(dict.fromkeys(normalized_symbols)) or []
        if not resolved:
            raise ConnectorError("Binance requires at least one symbol to subscribe to")
        params: Set[str] = set()
        for normalized in resolved:
            venue = self.to_venue_symbol(normalized).lower()
            if "trades" in channel_set:
                params.add(f"{venue}@trade")
            if "orderbook" in channel_set:
                params.add(f"{venue}@depth{depth}@100ms")
            if "ticker" in channel_set:
                params.add(f"{venue}@ticker")
        async with self._subscription_lock:
            self._symbols = resolved
            self._channels = channel_set
            self._depth = depth
            self._raw_mode = bool(raw)
            self._params = params
        await self._maybe_send_subscribe()

    async def __aiter__(self) -> AsyncIterator[BaseEvent]:
        while True:
            if not self._running and self._event_queue.empty():
                break
            event = await self._event_queue.get()
            yield event

    async def _run_loop(self) -> None:
        backoff = self._INITIAL_BACKOFF_SEC
        while self._running:
            try:
                async with websockets.connect(self._ws_url, ping_interval=None) as ws:
                    self._ws = ws
                    self._current_params.clear()
                    await self._maybe_send_subscribe()
                    backoff = self._INITIAL_BACKOFF_SEC
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    async for raw_message in ws:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.info("Binance websocket closed (%s), reconnecting", exc)
            except Exception as exc:
                logger.exception("Binance websocket loop error", exc_info=exc)
            finally:
                await self._cleanup_connection()
                if not self._running:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._MAX_BACKOFF_SEC)

    async def _cleanup_connection(self) -> None:
        if self._ping_task:
            self._ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ping_task
            self._ping_task = None
        if self._ws and not self._ws.closed:
            with contextlib.suppress(Exception):
                await self._ws.close()
        self._ws = None

    async def _ping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._PING_INTERVAL_SEC)
                if not self._ws or self._ws.closed:
                    break
                try:
                    await self._ws.ping()
                except Exception as exc:
                    logger.debug("Binance ping failed, reconnecting: %s", exc)
                    break
        except asyncio.CancelledError:
            return

    async def _maybe_send_subscribe(self) -> None:
        async with self._subscription_lock:
            if not self._params or not self._ws or self._ws.closed:
                return
            params = sorted(self._params)
        if set(params) == self._current_params:
            return
        async with self._send_lock:
            request = {"method": "SUBSCRIBE", "params": params, "id": int(time.time() * 1000)}
            try:
                await self._ws.send(json.dumps(request))
            except Exception as exc:
                logger.warning("Failed to send Binance subscribe request: %s", exc)
                return
            self._current_params = set(params)

    async def _handle_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("Binance message could not be decoded: %s", raw_message)
            return
        if "ping" in payload and self._ws:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"pong": payload["ping"]}))
            return
        data = payload.get("data", payload)
        event_type = data.get("e")
        if event_type == "trade":
            normalized = self._normalize_trade(data, payload.get("stream"))
            if normalized:
                await self._event_queue.put(normalized)
        elif event_type == "depthUpdate":
            book = self._normalize_orderbook(data, payload.get("stream"))
            if book:
                await self._event_queue.put(book)
        elif event_type == "24hrTicker":
            ticker = self._normalize_ticker(data, payload.get("stream"))
            if ticker:
                await self._event_queue.put(ticker)

    def _normalize_trade(self, data: Dict[str, Any], stream: Optional[str]) -> Trade | None:
        price = self._to_float(data.get("p"))
        size = self._to_float(data.get("q"))
        ts_exchange = self._to_millis(data.get("E") or data.get("T"))
        ts_event = self._now_ms()
        if price is None or size is None:
            return None
        symbol = data.get("s") or (stream or "").split("@")[0].upper()
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        raw_payload = {"channel": "trade", "stream": stream, "data": data} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=Side.SELL if data.get("m") else Side.BUY,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=MarketType.SPOT,
            trade_id=str(data.get("t")) if data.get("t") is not None else None,
            raw=raw_payload,
        )

    def _normalize_orderbook(self, data: Dict[str, Any], stream: Optional[str]) -> OrderBook | None:
        ts_exchange = self._to_millis(data.get("E"))
        ts_event = self._now_ms()
        bids = self._parse_levels(data.get("b", []))
        asks = self._parse_levels(data.get("a", []))
        symbol = data.get("s") or (stream or "").split("@")[0].upper()
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        seq = self._to_int(data.get("u"))
        prev_seq = self._to_int(data.get("pu") or data.get("U"))
        state = self._book_state(normalized_symbol)
        update_type = BookUpdateType.DELTA
        is_reset = False
        if state.sequence is None:
            state.snapshot(bids, asks, seq, prev_seq)
            update_type = BookUpdateType.SNAPSHOT
        else:
            applied = state.apply_delta(bids, asks, seq, prev_seq)
            if not applied:
                state.snapshot(bids, asks, seq, prev_seq)
                is_reset = True
        bids_out, asks_out = state.top()
        raw_payload = {"channel": "depth", "stream": stream, "data": data} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids_out,
            asks=asks_out,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=MarketType.SPOT,
            depth=self._depth,
            update_type=update_type,
            sequence=state.sequence,
            prev_sequence=state.prev_sequence,
            is_reset=is_reset,
            raw=raw_payload,
        )

    def _normalize_ticker(self, data: Dict[str, Any], stream: Optional[str]) -> Ticker | None:
        ts_exchange = self._to_millis(data.get("E"))
        ts_event = self._now_ms()
        symbol = data.get("s") or (stream or "").split("@")[0].upper()
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        last = self._to_float(data.get("c"))
        raw_payload = {"channel": "ticker", "stream": stream, "data": data} if self._raw_mode else None
        return Ticker(
            exchange=self.exchange,
            symbol=normalized_symbol,
            last=last,
            mark=None,
            index=None,
            open_interest=None,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=MarketType.SPOT,
            raw=raw_payload,
        )

    def _parse_levels(self, levels: Iterable[Iterable[Any]]) -> List[PriceSize]:
        prices: List[PriceSize] = []
        for level in levels:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            price = self._to_float(level[0])
            size = self._to_float(level[1])
            if price is None or size is None:
                continue
            prices.append((price, size))
        return prices

    def _book_state(self, symbol: str) -> _BookState:
        return self._books.setdefault(symbol, _BookState(self._depth))

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_millis(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

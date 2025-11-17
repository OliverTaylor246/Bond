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
    OrderBook,
    PriceSize,
    Trade,
    normalize_symbol,
    unify_side,
)

logger = logging.getLogger(__name__)


class BybitConnector(ExchangeConnector):
    exchange = "bybit"
    _WS_URL = "wss://stream.bybit.com/v5/public/spot"
    _CHANNEL_MAP = {"trades": "publicTrade", "orderbook": "orderBookL2_25"}
    _PING_INTERVAL_SEC = 20
    _INITIAL_BACKOFF_SEC = 1.0
    _MAX_BACKOFF_SEC = 30.0

    def __init__(self):
        super().__init__()
        self._ws_url = self._WS_URL
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._args: Set[str] = set()
        self._current_args: Set[str] = set()
        self._raw_mode = False
        self._depth: Optional[int] = None
        self._subscription_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._run_task: Optional[asyncio.Task[None]] = None
        self._ping_task: Optional[asyncio.Task[None]] = None

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
        for raw_symbol in symbols:
            normalized = normalize_symbol(self.exchange, raw_symbol)
            if not normalized or "/" not in normalized:
                continue
            normalized_symbols.append(normalized)
        if not normalized_symbols:
            raise ConnectorError("Bybit requires at least one valid symbol")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by Bybit")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError("Bybit requires at least one channel")
        args: Set[str] = set()
        for normalized in dict.fromkeys(normalized_symbols):
            venue = self.to_venue_symbol(normalized)
            for channel in channel_set:
                prefix = self._CHANNEL_MAP[channel]
                args.add(f"{prefix}.{venue}")
        async with self._subscription_lock:
            self._symbols = normalized_symbols
            self._channels = channel_set
            self._raw_mode = bool(raw)
            self._depth = depth
            self._args = args
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
                    self._current_args.clear()
                    await self._maybe_send_subscribe()
                    backoff = self._INITIAL_BACKOFF_SEC
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    async for raw_message in ws:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.info("Bybit websocket closed (%s), reconnecting", exc)
            except Exception as exc:
                logger.exception("Bybit websocket loop error", exc_info=exc)
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
                    await self._ws.send(json.dumps({"op": "ping"}))
                except Exception as exc:
                    logger.debug("Bybit ping failed: %s", exc)
                    break
        except asyncio.CancelledError:
            return

    async def _maybe_send_subscribe(self) -> None:
        async with self._subscription_lock:
            if not self._args or not self._ws or self._ws.closed:
                return
            builder = sorted(self._args)
        if set(builder) == self._current_args:
            return
        async with self._send_lock:
            request = {"op": "subscribe", "args": builder}
            try:
                await self._ws.send(json.dumps(request))
            except Exception as exc:
                logger.warning("Failed to send Bybit subscribe request: %s", exc)
                return
            self._current_args = set(builder)

    async def _handle_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("Bybit message was not JSON: %s", raw_message)
            return
        if payload.get("op") == "ping":
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"op": "pong"}))
            return
        topic = payload.get("topic", "")
        if topic.startswith("publicTrade"):
            trades = payload.get("data")
            if isinstance(trades, list):
                for entry in trades:
                    trade = self._normalize_trade(entry, payload)
                    if trade:
                        await self._event_queue.put(trade)
        elif topic.startswith("orderBookL2_25"):
            book = self._normalize_orderbook(payload)
            if book:
                await self._event_queue.put(book)

    def _normalize_trade(self, data: Dict[str, Any], payload: Dict[str, Any]) -> Trade | None:
        price = self._to_float(data.get("price"))
        size = self._to_float(data.get("size") or data.get("qty"))
        ts = self._to_millis(data.get("trade_time_ms") or payload.get("ts"))
        if price is None or size is None or ts is None:
            return None
        symbol = data.get("symbol") or payload.get("topic", "").split(".", 1)[-1]
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        side = unify_side(data.get("side") or data.get("direction"))
        raw_payload = {"channel": "trades", "payload": payload} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=side,
            ts_event=ts,
            ts_exchange=ts,
            trade_id=str(data.get("trade_id") or data.get("id")) if data.get("trade_id") or data.get("id") else None,
            raw=raw_payload,
        )

    def _normalize_orderbook(self, payload: Dict[str, Any]) -> OrderBook | None:
        data = payload.get("data")
        if not isinstance(data, list):
            return None
        symbol = payload.get("topic", "").split(".", 1)[-1]
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        ts = self._to_millis(payload.get("ts"))
        levels = payload.get("data", [])
        bids: List[PriceSize] = []
        asks: List[PriceSize] = []
        for entry in levels:
            price = self._to_float(entry.get("price") or entry.get("px"))
            size = self._to_float(entry.get("size") or entry.get("qty"))
            if price is None or size is None:
                continue
            side = entry.get("side") or entry.get("direction")
            if isinstance(side, str) and side.lower().startswith("b"):
                bids.append((price, size))
            else:
                asks.append((price, size))
        bids.sort(key=lambda entry: entry[0], reverse=True)
        asks.sort(key=lambda entry: entry[0])
        depth = self._depth or len(bids or asks)
        if depth:
            bids = bids[:depth]
            asks = asks[:depth]
        book_type = payload.get("type") or payload.get("event")
        raw_payload = {"channel": "orderbook", "payload": payload} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids,
            asks=asks,
            ts_event=ts or self._now_ms(),
            ts_exchange=ts or self._now_ms(),
            depth=depth,
            sequence=self._to_int(payload.get("seqNum")),
            prev_sequence=self._to_int(payload.get("prevSeqNum")),
            update_type=BookUpdateType.SNAPSHOT if book_type == "snapshot" else BookUpdateType.DELTA,
            raw=raw_payload,
        )

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

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

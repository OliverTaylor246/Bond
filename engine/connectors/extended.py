from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Set

import websockets
from websockets.exceptions import ConnectionClosed

from .base import ConnectorError, ExchangeConnector
from ..schemas import (
    BaseEvent,
    BookUpdateType,
    MarketType,
    OrderBook,
    PriceSize,
    Trade,
    normalize_symbol,
    unify_side,
)

logger = logging.getLogger(__name__)


class ExtendedExchangeConnector(ExchangeConnector):
    exchange = "extended"
    _WS_URL = "https://api.starknet.extended.exchange/"
    _PING_INTERVAL_SEC = 30
    _INITIAL_BACKOFF_SEC = 1.0
    _MAX_BACKOFF_SEC = 30.0

    def __init__(self):
        super().__init__()
        self._ws_url = self._WS_URL
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._depth: Optional[int] = None
        self._raw_mode = False
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
            if not normalized:
                continue
            normalized_symbols.append(normalized)
        if not normalized_symbols:
            raise ConnectorError("Extended Exchange requires at least one valid symbol")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by Extended Exchange")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError("Extended Exchange requires at least one channel")
        async with self._subscription_lock:
            self._symbols = list(dict.fromkeys(normalized_symbols))
            self._channels = channel_set
            self._depth = depth
            self._raw_mode = bool(raw)
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
                    await self._maybe_send_subscribe()
                    backoff = self._INITIAL_BACKOFF_SEC
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    async for raw_message in ws:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.info("Extended Exchange websocket closed (%s), reconnecting", exc)
            except Exception as exc:
                logger.exception("Extended Exchange websocket loop error", exc_info=exc)
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
                    await self._ws.send(json.dumps({"type": "ping"}))
                except Exception as exc:
                    logger.debug("Extended Exchange ping failed: %s", exc)
                    break
        except asyncio.CancelledError:
            return

    async def _maybe_send_subscribe(self) -> None:
        async with self._subscription_lock:
            if not self._symbols or not self._channels or not self._ws or self._ws.closed:
                return
            payload = {
                "type": "subscribe",
                "channels": sorted(self._channels),
                "symbols": self._symbols,
            }
        async with self._send_lock:
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.warning("Extended Exchange subscribe failed: %s", exc)

    async def _handle_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("Extended Exchange message is not JSON: %s", raw_message)
            return
        if payload.get("type") == "ping":
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "pong"}))
            return
        topic = payload.get("topic", "")
        data = payload.get("data")
        if not data:
            return
        if topic.startswith("trades"):
            trade = self._normalize_trade(data, topic)
            if trade:
                await self._event_queue.put(trade)
        elif topic.startswith("orderbook"):
            book = self._normalize_orderbook(data, topic, payload)
            if book:
                await self._event_queue.put(book)

    def _normalize_trade(self, data: Dict[str, Any], topic: str) -> Trade | None:
        price = self._to_float(data.get("price"))
        size = self._to_float(data.get("quantity"))
        ts_exchange = self._to_millis(data.get("timestamp"))
        ts_event = self._now_ms()
        if price is None or size is None:
            return None
        symbol = data.get("symbol") or topic.split(".", 1)[-1]
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        side = unify_side(data.get("side"))
        raw_payload = {"channel": "trades", "data": data} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=side,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=MarketType.SPOT,
            trade_id=str(data.get("id")) if data.get("id") is not None else None,
            raw=raw_payload,
        )

    def _normalize_orderbook(
        self, data: Dict[str, Any], topic: str, payload: Dict[str, Any]
    ) -> OrderBook | None:
        levels = data.get("levels", [])
        if not isinstance(levels, list):
            return None
        bids: List[PriceSize] = []
        asks: List[PriceSize] = []
        for level in levels:
            price = self._to_float(level.get("price"))
            size = self._to_float(level.get("quantity"))
            if price is None or size is None:
                continue
            side = level.get("side")
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
        symbol = data.get("symbol") or topic.split(".", 1)[-1]
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        ts_exchange = self._to_millis(data.get("timestamp") or payload.get("timestamp"))
        ts_event = self._now_ms()
        raw_payload = {"channel": "orderbook", "payload": payload} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids,
            asks=asks,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=MarketType.SPOT,
            depth=depth,
            sequence=self._to_int(data.get("sequence")),
            prev_sequence=self._to_int(data.get("prevSequence")),
            update_type=BookUpdateType.SNAPSHOT if payload.get("type") == "snapshot" else BookUpdateType.DELTA,
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

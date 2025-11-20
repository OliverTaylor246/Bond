from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import websockets
from websockets.exceptions import ConnectionClosed

from .base import ConnectorError, ExchangeConnector
from ..schemas import (
    BaseEvent,
    BookUpdateType,
    OrderBook,
    PriceSize,
    Side,
    Trade,
    normalize_symbol,
    unify_side,
)

logger = logging.getLogger(__name__)


class OkxConnector(ExchangeConnector):
    exchange = "okx"
    _WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
    _PING_INTERVAL_SEC = 20
    _INITIAL_BACKOFF_SEC = 1.0
    _MAX_BACKOFF_SEC = 30.0
    _CHANNEL_MAP = {
        "trades": "trades",
        "orderbook": "books-l2-tbt",
    }

    def __init__(self) -> None:
        super().__init__()
        self._ws_url = self._WS_URL
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._depth: int = 20
        self._raw_mode = False
        self._args: Set[Tuple[str, str, int]] = set()
        self._current_args: Set[Tuple[str, str, int]] = set()
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
            raise ConnectorError("OkX requires at least one valid symbol")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by OkX")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError("OkX requires at least one channel")
        depth_value = depth or self._depth
        args: Set[Tuple[str, str, int]] = set()
        for normalized in dict.fromkeys(normalized_symbols):
            venue = normalized.replace("/", "-")
            for channel in channel_set:
                channel_name = self._CHANNEL_MAP[channel]
                args.add((channel_name, venue, depth_value))
        async with self._subscription_lock:
            self._symbols = normalized_symbols
            self._channels = channel_set
            self._depth = depth_value
            self._raw_mode = bool(raw)
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
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    backoff = self._INITIAL_BACKOFF_SEC
                    async for raw_message in ws:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.info("OkX websocket closed (%s), reconnecting", exc)
            except Exception as exc:
                logger.exception("OkX websocket loop error", exc_info=exc)
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
                    logger.debug("OkX ping failed: %s", exc)
                    break
        except asyncio.CancelledError:
            return

    async def _maybe_send_subscribe(self) -> None:
        async with self._subscription_lock:
            if not self._args or not self._ws or self._ws.closed:
                return
            pending = self._args
        if pending == self._current_args:
            return
        async with self._send_lock:
            payload = {
                "op": "subscribe",
                "args": [
                    {
                        "channel": channel,
                        "instId": inst,
                        "sz": str(depth),
                    }
                    for channel, inst, depth in sorted(pending)
                ],
            }
            try:
                await self._ws.send(json.dumps(payload))
            except Exception as exc:
                logger.warning("Failed to send OkX subscribe request: %s", exc)
                return
            self._current_args = set(pending)

    async def _handle_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("OkX message could not be decoded: %s", raw_message)
            return
        event = payload.get("event")
        if event == "subscribe":
            return
        if event == "pong":
            return
        arg = payload.get("arg", {}) or {}
        channel = arg.get("channel")
        data = payload.get("data")
        if not channel or not data:
            return
        if channel == "trades":
            for entry in data:
                trade = self._normalize_trade(entry, arg)
                if trade:
                    await self._event_queue.put(trade)
        elif channel in {"books", "books-l2-tbt", "books50-l2-tbt"}:
            for entry in data:
                book = self._normalize_orderbook(entry, arg)
                if book:
                    await self._event_queue.put(book)

    def _normalize_trade(self, entry: Dict[str, Any], arg: Dict[str, Any]) -> Trade | None:
        price = self._to_float(entry.get("px"))
        size = self._to_float(entry.get("sz"))
        ts = self._to_millis(entry.get("ts"))
        if price is None or size is None:
            return None
        ts = ts or self._now_ms()
        symbol = entry.get("instId") or arg.get("instId") or ""
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        side = unify_side(entry.get("side"))
        raw_payload = {"channel": "trades", "data": entry} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=side,
            ts_event=ts,
            ts_exchange=ts,
            trade_id=str(entry.get("tradeId")) if entry.get("tradeId") is not None else None,
            raw=raw_payload,
        )

    def _normalize_orderbook(self, entry: Dict[str, Any], arg: Dict[str, Any]) -> OrderBook | None:
        symbol = arg.get("instId") or entry.get("instId") or ""
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        ts = self._to_millis(entry.get("ts")) or self._now_ms()
        bids = self._parse_levels(entry.get("bids", []))
        asks = self._parse_levels(entry.get("asks", []))
        if self._depth:
            bids = bids[: self._depth]
            asks = asks[: self._depth]
        update_type = (
            BookUpdateType.SNAPSHOT
            if entry.get("action") == "snapshot"
            else BookUpdateType.DELTA
        )
        raw_payload = {"channel": arg.get("channel"), "data": entry} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids,
            asks=asks,
            ts_event=ts,
            ts_exchange=ts,
            depth=self._depth,
            sequence=self._to_int(entry.get("seqNum")),
            prev_sequence=self._to_int(entry.get("preSeqNum")),
            update_type=update_type,
            raw=raw_payload,
        )

    @staticmethod
    def _parse_levels(levels: Iterable[Any]) -> List[PriceSize]:
        parsed: List[PriceSize] = []
        for level in levels:
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                price = OkxConnector._to_float(level[0])
                size = OkxConnector._to_float(level[1])
                if price is None or size is None:
                    continue
                parsed.append((price, size))
        return parsed

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
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

    @staticmethod
    def _to_millis(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            if isinstance(value, str) and "." in value:
                value = value.split(".", 1)[0]
            millis = int(value)
            if millis < 1_000_000_000_000:
                millis *= 1000
            return millis
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    async def handle_message(self, raw_message: str) -> None:
        await self._handle_message(raw_message)

    def normalize_trade(self, entry: Dict[str, Any], arg: Dict[str, Any]) -> Trade | None:
        return self._normalize_trade(entry, arg)

    def normalize_l2_update(self, entry: Dict[str, Any], arg: Dict[str, Any]) -> OrderBook | None:
        return self._normalize_orderbook(entry, arg)

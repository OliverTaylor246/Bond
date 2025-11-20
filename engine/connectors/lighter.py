from __future__ import annotations

import asyncio
import aiohttp
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


class LighterConnector(ExchangeConnector):
    exchange = "lighter"
    _WS_URL = "wss://mainnet.zklighter.elliot.ai/stream"
    _REST_ORDERBOOKS = "https://mainnet.zklighter.elliot.ai/api/v1/orderBooks"
    _PING_INTERVAL_SEC = 20
    _INITIAL_BACKOFF_SEC = 1.0
    _MAX_BACKOFF_SEC = 30.0
    _COMMON_QUOTES = ("USDT", "USDC", "USD", "EUR", "JPY", "BTC", "ETH")
    _CHANNEL_MAP = {"trades": "trade", "orderbook": "order_book"}

    def __init__(self) -> None:
        super().__init__()
        self._ws_url = self._WS_URL
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._depth: int = 20
        self._raw_mode = False
        self._market_ids: Set[int] = set()
        self._subscription_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._run_task: Optional[asyncio.Task[None]] = None
        self._ping_task: Optional[asyncio.Task[None]] = None
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._symbol_lookup: Dict[str, int] = {}
        self._market_map: Dict[int, str] = {}
        self._book_initialized: Set[int] = set()
        self._last_offset: Dict[int, int] = {}
        self._current_subscriptions: Set[str] = set()

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
            raise ConnectorError("Lighter requires at least one valid symbol")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by Lighter")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError("Lighter requires at least one channel")
        depth_value = depth or self._depth
        await self._ensure_market_map()
        market_ids: Set[int] = set()
        missing: List[str] = []
        for normalized in dict.fromkeys(normalized_symbols):
            market_id = self._symbol_to_market_id(normalized)
            if market_id is None:
                missing.append(normalized)
                continue
            market_ids.add(market_id)
        if not market_ids:
            raise ConnectorError(
                f"Lighter cannot resolve symbols: {', '.join(missing)}"
            )
        async with self._subscription_lock:
            self._symbols = normalized_symbols
            self._channels = channel_set
            self._depth = depth_value
            self._raw_mode = bool(raw)
            self._market_ids = market_ids
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
                    self._current_subscriptions.clear()
                    await self._maybe_send_subscribe()
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    backoff = self._INITIAL_BACKOFF_SEC
                    async for raw_message in ws:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.info("Lighter websocket closed (%s), reconnecting", exc)
            except Exception as exc:
                logger.exception("Lighter websocket loop error", exc_info=exc)
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
        if self._http_session:
            with contextlib.suppress(Exception):
                await self._http_session.close()
            self._http_session = None

    async def _ping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._PING_INTERVAL_SEC)
                if not self._ws or self._ws.closed:
                    break
                try:
                    await self._ws.ping()
                except Exception as exc:
                    logger.debug("Lighter ping failed: %s", exc)
                    break
        except asyncio.CancelledError:
            return

    async def _maybe_send_subscribe(self) -> None:
        async with self._subscription_lock:
            if not self._market_ids or not self._channels or not self._ws or self._ws.closed:
                return
            targets = {
                f"{self._CHANNEL_MAP[channel]}/{market_id}"
                for channel in self._channels
                for market_id in self._market_ids
            }
        if targets == self._current_subscriptions or not targets:
            return
        async with self._send_lock:
            for channel_name in sorted(targets):
                request = {"type": "subscribe", "channel": channel_name}
                try:
                    await self._ws.send(json.dumps(request))
                except Exception as exc:
                    logger.warning("Failed to send Lighter subscribe request: %s", exc)
                    continue
            self._current_subscriptions = targets

    async def _handle_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("Lighter message could not be decoded: %s", raw_message)
            return
        channel = payload.get("channel")
        if not channel:
            return
        market_id = self._extract_market_id(channel)
        if market_id is None:
            return
        if channel.startswith("trade:"):
            for entry in payload.get("trades", []):
                trade = self._normalize_trade(entry, market_id)
                if trade:
                    await self._event_queue.put(trade)
        elif channel.startswith("order_book:"):
            book_payload = payload.get("order_book")
            if book_payload:
                book = self._normalize_orderbook(book_payload, market_id)
                if book:
                    await self._event_queue.put(book)

    def _normalize_trade(self, entry: Dict[str, Any], market_id: int) -> Trade | None:
        price = self._to_float(entry.get("price"))
        size = self._to_float(entry.get("size"))
        timestamp = self._to_millis(entry.get("timestamp"))
        if price is None or size is None:
            return None
        ts = timestamp or self._now_ms()
        symbol = self._market_map.get(market_id) or str(market_id)
        normalized_symbol = self._resolve_symbol(symbol)
        side = unify_side(entry.get("side"))
        raw_payload = {"channel": "trade", "market_id": market_id, "data": entry} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=side,
            ts_event=ts,
            ts_exchange=ts,
            trade_id=str(entry.get("trade_id")) if entry.get("trade_id") is not None else None,
            raw=raw_payload,
        )

    def _normalize_orderbook(self, book: Dict[str, Any], market_id: int) -> OrderBook | None:
        symbol = self._market_map.get(market_id) or str(market_id)
        normalized_symbol = self._resolve_symbol(symbol)
        asks = self._parse_levels(book.get("asks", []))
        bids = self._parse_levels(book.get("bids", []))
        if self._depth:
            bids = bids[: self._depth]
            asks = asks[: self._depth]
        ts = self._now_ms()
        offset = self._to_int(book.get("offset"))
        update_type = (
            BookUpdateType.SNAPSHOT
            if market_id not in self._book_initialized
            else BookUpdateType.DELTA
        )
        self._book_initialized.add(market_id)
        prev_sequence = self._last_offset.get(market_id)
        if offset is not None:
            self._last_offset[market_id] = offset
        raw_payload = {"channel": "order_book", "market_id": market_id, "data": book} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids,
            asks=asks,
            ts_event=ts,
            ts_exchange=ts,
            depth=self._depth,
            sequence=offset,
            prev_sequence=prev_sequence,
            update_type=update_type,
            raw=raw_payload,
        )

    @staticmethod
    def _parse_levels(levels: Iterable[Any]) -> List[PriceSize]:
        parsed: List[PriceSize] = []
        for level in levels:
            if isinstance(level, dict):
                price_value = level.get("price")
                size_value = level.get("size")
            elif isinstance(level, (list, tuple)) and len(level) >= 2:
                price_value = level[0]
                size_value = level[1]
            else:
                continue
            price = LighterConnector._to_float(price_value)
            size = LighterConnector._to_float(size_value)
            if price is None or size is None:
                continue
            parsed.append((price, size))
        return parsed

    async def _ensure_market_map(self) -> None:
        if self._symbol_lookup:
            return
        if not self._http_session:
            self._http_session = aiohttp.ClientSession()
        assert self._http_session
        try:
            async with self._http_session.get(self._REST_ORDERBOOKS, timeout=10) as response:
                data = await response.json()
        except Exception as exc:
            raise ConnectorError(f"Failed to load Lighter markets: {exc}")
        order_books = data.get("order_books") or []
        for entry in order_books:
            symbol = str(entry.get("symbol", "")).upper()
            market_id = entry.get("market_id")
            if market_id is None or not symbol:
                continue
            self._symbol_lookup[symbol] = market_id
            self._market_map[market_id] = symbol

    def _symbol_to_market_id(self, normalized: str) -> Optional[int]:
        candidate = normalized.replace("/", "").upper()
        if candidate in self._symbol_lookup:
            return self._symbol_lookup[candidate]
        for quote in self._COMMON_QUOTES:
            if candidate.endswith(quote):
                base = candidate[: -len(quote)]
                if base and base in self._symbol_lookup:
                    return self._symbol_lookup[base]
        base_only = normalized.split("/", 1)[0].upper()
        return self._symbol_lookup.get(base_only)

    def _resolve_symbol(self, raw: str) -> str:
        candidate = raw.upper()
        if "/" in raw:
            return raw
        for quote in self._COMMON_QUOTES:
            if candidate.endswith(quote) and len(candidate) > len(quote):
                base = candidate[: -len(quote)]
                return f"{base}/{quote}"
        return f"{candidate}/USDC"

    @staticmethod
    def _extract_market_id(channel: str) -> Optional[int]:
        parts = channel.split(":", 1)
        if len(parts) != 2:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

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

    def normalize_trade(self, entry: Dict[str, Any], market_id: int) -> Trade | None:
        return self._normalize_trade(entry, market_id)

    def normalize_l2_update(
        self, entry: Dict[str, Any], market_id: int
    ) -> OrderBook | None:
        return self._normalize_orderbook(entry, market_id)

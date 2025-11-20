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
)

logger = logging.getLogger(__name__)


class HyperliquidConnector(ExchangeConnector):
    exchange = "hyperliquid"
    _WS_URL = "wss://api.hyperliquid.xyz/ws"
    _PING_INTERVAL_SEC = 25
    _INITIAL_BACKOFF_SEC = 1.0
    _MAX_BACKOFF_SEC = 30.0
    _DEFAULT_COIN_MAP = {
        "BTC/USDT": "BTC",
        "SOL/USDT": "SOL",
        "ETH/USDT": "ETH",
    }

    def __init__(self, base_url: Optional[str] = None):
        super().__init__()
        self._base_url = base_url or self._WS_URL
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._depth: Optional[int] = None
        self._raw_mode = False
        self._subscription_payloads: Set[Tuple[str, str]] = set()
        self._current_subscriptions: Set[Tuple[str, str]] = set()
        self._subscription_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()
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
        normalized_symbols = []
        for raw_symbol in symbols:
            normalized = normalize_symbol(self.exchange, raw_symbol)
            if not normalized or "/" not in normalized:
                continue
            normalized_symbols.append(normalized)
        if not normalized_symbols:
            raise ConnectorError("Hyperliquid requires at least one valid symbol")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by Hyperliquid")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError("Hyperliquid requires at least one channel")
        payloads: Set[Tuple[str, str]] = set()
        for normalized in dict.fromkeys(normalized_symbols):
            coin = self._symbol_to_coin(normalized)
            for channel in channel_set:
                payloads.add((self._map_channel(channel), coin))
        async with self._subscription_lock:
            self._symbols = normalized_symbols
            self._channels = channel_set
            self._depth = depth
            self._raw_mode = bool(raw)
            self._subscription_payloads = payloads
        await self._maybe_send_pending_subscriptions()

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
                async with websockets.connect(self._base_url, ping_interval=None) as ws:
                    self._ws = ws
                    self._current_subscriptions.clear()
                    await self._maybe_send_pending_subscriptions()
                    backoff = self._INITIAL_BACKOFF_SEC
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    async for raw_message in ws:
                        await self._handle_message(raw_message)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                logger.info("Hyperliquid websocket closed (%s), reconnecting", exc)
            except Exception as exc:
                logger.exception("Hyperliquid websocket loop error", exc_info=exc)
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
                await self._ws.send(json.dumps({"method": "ping"}))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("Hyperliquid ping failed, reconnecting: %s", exc)

    def _map_channel(self, channel: str) -> str:
        if channel == "orderbook":
            return "l2Book"
        return channel

    def _symbol_to_coin(self, symbol: str) -> str:
        normalized = symbol.upper().lstrip("/-_")
        if normalized in self._DEFAULT_COIN_MAP:
            return self._DEFAULT_COIN_MAP[normalized]
        base = normalized.split("/", 1)[0]
        return base.split("-", 1)[0]

    def _pair_symbol(self, normalized: str, coin: str) -> str:
        candidate = (normalized or "").upper().lstrip("/-_")
        if "/" in candidate:
            base, _, quote = candidate.partition("/")
            if base and quote:
                return f"{base}/{quote}"
            if base:
                return f"{base}/USDT"
        for pair, mapped_coin in self._DEFAULT_COIN_MAP.items():
            base = pair.split("/", 1)[0]
            if candidate in {pair, mapped_coin, base}:
                return pair
        fallback_base = candidate or coin.upper()
        return f"{fallback_base}/USDT"

    async def _maybe_send_pending_subscriptions(self) -> None:
        async with self._subscription_lock:
            if not self._subscription_payloads or not self._ws or self._ws.closed:
                return
            pending = self._subscription_payloads - self._current_subscriptions
        if not pending:
            return
        async with self._send_lock:
            for channel, coin in pending:
                request = {
                    "method": "subscribe",
                    "subscription": {"type": channel, "coin": coin},
                }
                try:
                    await self._ws.send(json.dumps(request))
                except Exception as exc:
                    logger.warning(
                        "Failed to send hyperliquid subscription for %s/%s: %s",
                        channel,
                        coin,
                        exc,
                    )
                    continue
                self._current_subscriptions.add((channel, coin))

    async def _handle_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.debug("Hyperliquid message was not JSON: %s", raw_message)
            return
        channel = payload.get("channel")
        if channel == "pong":
            return
        if channel == "trades":
            trades = payload.get("data")
            if isinstance(trades, Iterable):
                for trade_payload in trades:
                    event = self._normalize_trade(trade_payload)
                    if event:
                        await self._event_queue.put(event)
        elif channel == "l2Book":
            data = payload.get("data")
            if isinstance(data, dict):
                event = self._normalize_orderbook(data)
                if event:
                    await self._event_queue.put(event)

    def _normalize_trade(self, data: Dict[str, Any]) -> Trade | None:
        coin = data.get("coin")
        if not coin:
            return None
        price = self._to_float(data.get("px"))
        size = self._to_float(data.get("sz"))
        ts = self._to_millis(data.get("time"))
        if price is None or size is None or ts is None:
            return None
        side = self._side_from_payload(data.get("side"))
        normalized_symbol = self._pair_symbol(data.get("symbol") or coin, coin)
        raw_payload = {"channel": "trades", "data": data} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=side,
            ts_event=ts,
            ts_exchange=ts,
            trade_id=str(data.get("tid")) if data.get("tid") is not None else None,
            match_id=data.get("hash"),
            raw=raw_payload,
        )

    def _normalize_orderbook(self, data: Dict[str, Any]) -> OrderBook | None:
        coin = data.get("coin")
        if not coin:
            return None
        ts = self._to_millis(data.get("time"))
        if ts is None:
            return None
        levels = data.get("levels") or [[], []]
        bids = self._parse_levels(levels[0] if len(levels) > 0 else [], reverse=True)
        asks = self._parse_levels(levels[1] if len(levels) > 1 else [], reverse=False)
        if self._depth is not None:
            bids = bids[: self._depth]
            asks = asks[: self._depth]
        normalized_symbol = self._pair_symbol(data.get("symbol") or coin, coin)
        raw_payload = {"channel": "l2Book", "data": data} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids,
            asks=asks,
            ts_event=ts,
            ts_exchange=ts,
            depth=self._depth,
            update_type=BookUpdateType.SNAPSHOT,
            raw=raw_payload,
        )

    def _parse_levels(
        self, levels: Iterable[Dict[str, Any]], *, reverse: bool
    ) -> List[PriceSize]:
        prices: List[PriceSize] = []
        for level in levels:
            price = self._to_float(level.get("px"))
            size = self._to_float(level.get("sz"))
            if price is None or size is None:
                continue
            prices.append((price, size))
        prices.sort(key=lambda entry: entry[0], reverse=reverse)
        return prices

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _to_millis(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _side_from_payload(raw_side: Any) -> Side:
        if raw_side == "B":
            return Side.BUY
        if raw_side == "A":
            return Side.SELL
        return Side.UNKNOWN

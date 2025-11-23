"""CCXT Pro connectors that normalize trades/orderbooks for multiple venues."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Set

import ccxt.pro as ccxtpro

from .base import ConnectorError, ExchangeConnector
from ..schemas import (
    BaseEvent,
    BookUpdateType,
    MarketType,
    OrderBook,
    PriceSize,
    Side,
    Trade,
    normalize_symbol,
    unify_side,
)

logger = logging.getLogger(__name__)


def _normalize_market_type(market_type: Optional[str]) -> Optional[str]:
    if not market_type:
        return None
    normalized = market_type.strip().lower()
    mapping = {
        "spot": "spot",
        "margin": "margin",
        "future": "future",
        "futures": "future",
        "swap": "swap",
        "perp": "swap",
        "perpetual": "swap",
    }
    return mapping.get(normalized, normalized)


class CCXTExchangeConnector(ExchangeConnector):
    exchange: str

    def __init__(self, exchange_id: str, market_type: Optional[str] = None):
        super().__init__()
        self._exchange_id = exchange_id
        self._market_type = _normalize_market_type(market_type)
        self._symbols: List[str] = []
        self._channels: Set[str] = set()
        self._depth: Optional[int] = None
        self._raw_mode: bool = False
        self._client: Optional[Any] = None
        self._subscription_lock = asyncio.Lock()
        self._worker_tasks: List[asyncio.Task[Any]] = []
        self._worker_lock = asyncio.Lock()
        self._event_queue: asyncio.Queue[BaseEvent] = asyncio.Queue()

    async def connect(self) -> None:
        if self._running:
            return
        self._running = True
        await self._ensure_client()
        await self._restart_workers()

    async def disconnect(self) -> None:
        self._running = False
        await self._stop_workers()
        if self._client:
            with contextlib.suppress(Exception):
                await self._client.close()
        self._client = None

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
            raise ConnectorError(f"{self.exchange} requires at least one valid symbol")
        channel_set: Set[str] = set()
        for channel in channels:
            lowered = str(channel).strip().lower()
            if lowered not in self.supported_channels:
                raise ConnectorError(f"Channel '{channel}' is not supported by {self.exchange}")
            channel_set.add(lowered)
        if not channel_set:
            raise ConnectorError(f"{self.exchange} requires at least one channel")
        async with self._subscription_lock:
            self._symbols = list(dict.fromkeys(normalized_symbols))
            self._channels = channel_set
            self._depth = depth
            self._raw_mode = bool(raw)
        await self._restart_workers()

    async def __aiter__(self) -> AsyncIterator[BaseEvent]:
        while True:
            if not self._running and self._event_queue.empty():
                break
            event = await self._event_queue.get()
            yield event

    async def _ensure_client(self) -> None:
        if self._client:
            return
        try:
            exchange_class = getattr(ccxtpro, self._exchange_id)
        except AttributeError as exc:
            raise ConnectorError(f"CCXT Pro does not support {self._exchange_id}: {exc}")
        params: Dict[str, Any] = {}
        if self._market_type and self._market_type not in {"spot"}:
            params["options"] = {"defaultType": self._market_type}
        self._client = exchange_class(params)
        try:
            await self._client.load_markets()
        except Exception:
            await self._client.close()
            self._client = None
            raise

    async def _restart_workers(self) -> None:
        async with self._worker_lock:
            await self._stop_workers()
            if not self._running or not self._client or not self._symbols or not self._channels:
                return
            tasks: List[asyncio.Task[Any]] = []
            for symbol in self._symbols:
                if "trades" in self._channels:
                    tasks.append(asyncio.create_task(self._trade_worker(symbol)))
                if "orderbook" in self._channels:
                    tasks.append(asyncio.create_task(self._orderbook_worker(symbol)))
            self._worker_tasks = tasks

    async def _stop_workers(self) -> None:
        async with self._worker_lock:
            tasks = self._worker_tasks
            self._worker_tasks = []
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _trade_worker(self, symbol: str) -> None:
        if not self._client:
            return
        while self._running:
            try:
                trades = await self._client.watch_trades(symbol)
                for trade_payload in trades:
                    event = self._normalize_trade(symbol, trade_payload)
                    if event:
                        await self._event_queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("CCXT %s trade watcher for %s error: %s", self.exchange, symbol, exc)
                await asyncio.sleep(1)

    async def _orderbook_worker(self, symbol: str) -> None:
        if not self._client:
            return
        while self._running:
            try:
                depth_payload = await self._client.watch_order_book(symbol)
                event = self._normalize_orderbook(symbol, depth_payload)
                if event:
                    await self._event_queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("CCXT %s orderbook watcher for %s error: %s", self.exchange, symbol, exc)
                await asyncio.sleep(1)

    def _normalize_trade(self, symbol: str, data: Dict[str, Any]) -> Trade | None:
        price = self._to_float(data.get("price") or data.get("px"))
        size = self._to_float(data.get("amount") or data.get("qty") or data.get("size"))
        ts_exchange = self._to_millis(data.get("timestamp") or data.get("tradeTimestamp"))
        ts_event = self._now_ms()
        if price is None or size is None:
            return None
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        side = unify_side(data.get("side") or data.get("direction"))
        raw_payload = {"channel": "trades", "data": data} if self._raw_mode else None
        return Trade(
            exchange=self.exchange,
            symbol=normalized_symbol,
            price=price,
            size=size,
            side=side,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=self._market_type_enum(),
            trade_id=str(data.get("id") or data.get("tradeId")) if data.get("id") or data.get("tradeId") else None,
            raw=raw_payload,
        )

    def _normalize_orderbook(self, symbol: str, data: Dict[str, Any]) -> OrderBook | None:
        ts_exchange = self._to_millis(data.get("timestamp") or data.get("datetime"))
        ts_event = self._now_ms()
        bids = self._parse_levels(data.get("bids") or [])
        asks = self._parse_levels(data.get("asks") or [])
        if self._depth:
            bids = bids[: self._depth]
            asks = asks[: self._depth]
        normalized_symbol = normalize_symbol(self.exchange, symbol)
        raw_payload = {"channel": "orderbook", "data": data} if self._raw_mode else None
        return OrderBook(
            exchange=self.exchange,
            symbol=normalized_symbol,
            bids=bids,
            asks=asks,
            ts_event=ts_event,
            ts_exchange=ts_exchange,
            market_type=self._market_type_enum(),
            depth=self._depth,
            update_type=BookUpdateType.SNAPSHOT,
            sequence=self._to_int(data.get("nonce") or data.get("sequence")),
            raw=raw_payload,
        )

    @staticmethod
    def _parse_levels(levels: Iterable[Iterable[Any]]) -> List[PriceSize]:
        entries: List[PriceSize] = []
        for level in levels:
            if not isinstance(level, Iterable):
                continue
            pair = list(level)
            if len(pair) < 2:
                continue
            price = CCXTExchangeConnector._to_float(pair[0])
            size = CCXTExchangeConnector._to_float(pair[1])
            if price is None or size is None:
                continue
            entries.append((price, size))
        return entries

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

    def _market_type_enum(self) -> MarketType | None:
        if not self._market_type:
            return None
        mapping = {
            "spot": MarketType.SPOT,
            "margin": MarketType.SPOT,
            "swap": MarketType.PERP,
            "future": MarketType.FUTURE,
        }
        return mapping.get(self._market_type, None)

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class CCXTBinanceConnector(CCXTExchangeConnector):
    exchange = "binance"

    def __init__(self, market_type: Optional[str] = None):
        super().__init__(exchange_id="binance", market_type=market_type)


class CCXTBybitConnector(CCXTExchangeConnector):
    exchange = "bybit"

    def __init__(self, market_type: Optional[str] = None):
        super().__init__(exchange_id="bybit", market_type=market_type)


class CCXTHyperliquidConnector(CCXTExchangeConnector):
    exchange = "hyperliquid"

    def __init__(self, market_type: Optional[str] = None):
        super().__init__(exchange_id="hyperliquid", market_type=market_type)


class CCXTExtendedExchangeConnector(CCXTExchangeConnector):
    exchange = "extended"

    def __init__(self, market_type: Optional[str] = None):
        super().__init__(exchange_id="extended", market_type=market_type)

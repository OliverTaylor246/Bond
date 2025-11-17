from __future__ import annotations

import abc
from typing import Any, AsyncIterator, Dict, Sequence, Set

from ..schemas import BaseEvent, OrderBook, RawMessage, Trade, normalize_symbol, unify_side


class ConnectorError(Exception):
    pass


class ExchangeConnector(abc.ABC):
    exchange: str
    supported_channels: Set[str] = {"trades", "orderbook"}

    def __init__(self) -> None:
        self._subscriptions: Dict[str, Any] = {}
        self._running: bool = False

    @abc.abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    async def shutdown(self) -> None:
        self._running = False
        await self.disconnect()

    @abc.abstractmethod
    async def subscribe(
        self,
        *,
        symbols: Sequence[str],
        channels: Sequence[str],
        depth: int | None = None,
        raw: bool = False,
    ) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    async def __aiter__(self) -> AsyncIterator[BaseEvent]:
        raise NotImplementedError

    @abc.abstractmethod
    def _normalize_trade(self, msg: Dict[str, Any]) -> Trade | None:
        raise NotImplementedError

    @abc.abstractmethod
    def _normalize_orderbook(self, msg: Dict[str, Any]) -> OrderBook | None:
        raise NotImplementedError

    def to_venue_symbol(self, normalized_symbol: str) -> str:
        base, quote = normalized_symbol.split("/")
        return f"{base}{quote}"

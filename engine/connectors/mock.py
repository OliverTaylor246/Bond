from __future__ import annotations

import asyncio
import random
import time
from typing import List, Sequence

from .base import ExchangeConnector
from ..schemas import (
    Millis,
    OrderBook,
    PriceSize,
    Trade,
    normalize_symbol,
    unify_side,
    Side,
)


class MockConnector(ExchangeConnector):
    def __init__(self, exchange: str, symbols: Sequence[str]):
        super().__init__()
        self.exchange = exchange
        self._symbols = list(symbols)
        self._channels: List[str] = []
        self._running: bool = False
        self._depth = 20

    async def connect(self) -> None:
        await asyncio.sleep(0.01)
        self._running = True

    async def disconnect(self) -> None:
        self._running = False

    async def subscribe(
        self,
        *,
        symbols: Sequence[str],
        channels: Sequence[str],
        depth: int | None = None,
        raw: bool = False,
    ) -> None:
        self._symbols = [normalize_symbol(self.exchange, sym) for sym in symbols]
        self._channels = list(channels)
        self._depth = depth or self._depth

    async def __aiter__(self):
        while self._running:
            await asyncio.sleep(random.uniform(0.1, 0.4))
            ts = int(time.time() * 1000)

            if "trades" in self._channels:
                for symbol in self._symbols:
                    trade = Trade(
                        exchange=self.exchange,
                        symbol=symbol,
                        price=round(20_000 + random.random() * 1_000, 2),
                        size=round(random.random() * 2, 5),
                        side=random.choice([Side.BUY, Side.SELL]),
                        ts_event=ts,
                        ts_exchange=ts,
                        raw={"source": "mock"},
                    )
                    yield trade

            if "orderbook" in self._channels:
                for symbol in self._symbols:
                    bids: List[PriceSize] = [
                        (round(20_000 - i * 0.5, 2), round(random.random() * 5, 5))
                        for i in range(self._depth or 5)
                    ]
                    asks: List[PriceSize] = [
                        (round(20_000 + i * 0.5, 2), round(random.random() * 5, 5))
                        for i in range(self._depth or 5)
                    ]
                    book = OrderBook(
                        exchange=self.exchange,
                        symbol=symbol,
                        bids=bids,
                        asks=asks,
                        ts_event=ts,
                        ts_exchange=ts,
                        depth=self._depth,
                        raw={"source": "mock-orderbook"},
                    )
                    yield book

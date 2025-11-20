from __future__ import annotations

import heapq
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Set, Tuple

from .schemas import BaseEvent, EventType, OrderBook, Trade


@dataclass(slots=True)
class AggregationConfig:
    max_lag_ms: int = 50
    dedupe_window: int = 10_000


class AggregationEngine:
    _FUTURE_TOLERANCE_MS = 5_000

    def __init__(self, config: Optional[AggregationConfig] = None) -> None:
        self.config = config or AggregationConfig()
        self._heap: List[Tuple[int, int, BaseEvent]] = []
        self._counter: int = 0
        self._seen_keys: List[str] = []
        self._seen_set: Set[str] = set()

    def merge_streams(self, events: Iterable[BaseEvent]) -> Iterator[BaseEvent]:
        for ev in events:
            self.ingest(ev)
        while self._heap:
            _, _, ev = heapq.heappop(self._heap)
            if not self._is_duplicate(ev):
                self._remember(ev)
                yield ev

    def ingest(self, event: BaseEvent) -> None:
        ts = self._event_sort_key(event)
        now_ms = int(time.time() * 1000)
        if ts > now_ms + self._FUTURE_TOLERANCE_MS:
            ts = now_ms  # clamp far future timestamps so they don't block flush
        self._counter += 1
        heapq.heappush(self._heap, (ts, self._counter, event))

    def flush_ready(self) -> List[BaseEvent]:
        now_ms = int(time.time() * 1000)
        threshold = now_ms - self.config.max_lag_ms
        out: List[BaseEvent] = []
        while self._heap and self._heap[0][0] <= threshold:
            _, _, ev = heapq.heappop(self._heap)
            if not self._is_duplicate(ev):
                self._remember(ev)
                out.append(ev)
        return out

    @staticmethod
    def _event_sort_key(event: BaseEvent) -> int:
        if event.ts_exchange is not None:
            return event.ts_exchange
        return event.ts_event

    def _event_dedupe_key(self, event: BaseEvent) -> str:
        if event.type == EventType.TRADE and isinstance(event, Trade):
            return "|".join(
                [
                    "trade",
                    event.exchange,
                    event.symbol,
                    str(event.trade_id or ""),
                    f"{event.price:.8f}",
                    f"{event.size:.8f}",
                    event.side.value,
                    str(event.ts_exchange or event.ts_event),
                ]
            )
        elif event.type == EventType.ORDERBOOK and isinstance(event, OrderBook):
            seq = event.sequence or -1
            return "|".join(
                [
                    "ob",
                    event.exchange,
                    event.symbol,
                    str(seq),
                    str(event.ts_exchange or event.ts_event),
                ]
            )
        else:
            return "|".join(
                [
                    event.type.value,
                    event.exchange,
                    event.symbol,
                    str(event.ts_exchange or event.ts_event),
                ]
            )

    def _is_duplicate(self, event: BaseEvent) -> bool:
        key = self._event_dedupe_key(event)
        return key in self._seen_set

    def _remember(self, event: BaseEvent) -> None:
        key = self._event_dedupe_key(event)
        if key in self._seen_set:
            return
        self._seen_set.add(key)
        self._seen_keys.append(key)
        if len(self._seen_keys) > self.config.dedupe_window:
            old = self._seen_keys.pop(0)
            self._seen_set.discard(old)


def subscription_filter(
    event: BaseEvent,
    *,
    symbols: Optional[Set[str]] = None,
    exchanges: Optional[Set[str]] = None,
) -> bool:
    if exchanges:
        if "*" not in exchanges and event.exchange not in exchanges:
            return False
    if symbols:
        if "*" not in symbols and event.symbol not in symbols:
            return False
    return True

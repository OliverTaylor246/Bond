from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .schemas import Price, PriceSize


@dataclass
class LocalOrderBook:
    """Lightweight in-memory L2 book used to emit normalized diffs.

    Maintains sorted bids/asks and crops to `depth` on demand.
    """

    depth: int | None = None
    bids: List[PriceSize] = field(default_factory=list)
    asks: List[PriceSize] = field(default_factory=list)
    _price_map: Dict[str, float] = field(default_factory=dict)  # "b:price" -> size

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self._price_map.clear()

    def apply_levels(self, side: str, levels: List[PriceSize]) -> None:
        arr = self.bids if side == "bids" else self.asks
        ascending = side == "asks"
        for price, size in levels:
            key = f"{side}:{price}"
            if size == 0:
                if key in self._price_map:
                    self._remove_level(arr, price)
                    self._price_map.pop(key, None)
                continue
            self._price_map[key] = size
            self._upsert_level(arr, price, size, ascending)
        self._crop(arr, ascending)

    def snapshot(self, side: str, levels: List[PriceSize]) -> None:
        arr = self.bids if side == "bids" else self.asks
        arr.clear()
        for price, size in levels:
            if size <= 0:
                continue
            arr.append((price, size))
        arr.sort(key=lambda x: x[0], reverse=side == "bids")
        self._rebuild_map()
        self._crop(arr, side == "asks")

    def top(self) -> Tuple[List[PriceSize], List[PriceSize]]:
        bids = self.bids if self.depth is None else self.bids[: self.depth]
        asks = self.asks if self.depth is None else self.asks[: self.depth]
        return bids, asks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _upsert_level(self, arr: List[PriceSize], price: Price, size: float, ascending: bool) -> None:
        # Binary insert while keeping sort
        idx = bisect.bisect_left([p for p, _ in arr], price) if ascending else None
        if ascending:
            if idx < len(arr) and arr[idx][0] == price:
                arr[idx] = (price, size)
            else:
                arr.insert(idx, (price, size))
                arr.sort(key=lambda x: x[0])
        else:
            # descending
            for i, (p, _) in enumerate(arr):
                if p == price:
                    arr[i] = (price, size)
                    break
                if p < price:
                    arr.insert(i, (price, size))
                    break
            else:
                arr.append((price, size))
            arr.sort(key=lambda x: x[0], reverse=True)

    def _remove_level(self, arr: List[PriceSize], price: Price) -> None:
        for i, (p, _) in enumerate(arr):
            if p == price:
                arr.pop(i)
                break

    def _crop(self, arr: List[PriceSize], ascending: bool) -> None:
        if self.depth is None:
            return
        if len(arr) > self.depth:
            del arr[self.depth :]

    def _rebuild_map(self) -> None:
        self._price_map.clear()
        for side, arr in (("bids", self.bids), ("asks", self.asks)):
            for price, size in arr:
                self._price_map[f"{side}:{price}"] = size

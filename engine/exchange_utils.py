"""
Minimal shared helpers for connector development.

Note: This is intentionally lightweight to unblock imports and smoke tests.
Full L2/L3 book management and symbol normalization should be implemented
as connectors mature.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


def now_ms() -> int:
  return int(time.time() * 1000)


def to_canonical_symbol(raw: str) -> str:
  """
  Convert common exchange symbol shapes into a canonical DASH-delimited form.
  Examples:
    btcusdt -> BTC-USDT
    BTC/USDT -> BTC-USDT
    BTC-USDT -> BTC-USDT
  """
  s = raw.replace("/", "-").replace("_", "-").upper()
  # If no dash, try to split on common quotes
  if "-" not in s:
    quotes = ("USDT", "USD", "USDC", "PERP")
    for q in quotes:
      if s.endswith(q) and len(s) > len(q):
        base = s[: -len(q)]
        return f"{base}-{q}"
  return s


@dataclass
class OrderBookL2:
  """
  Minimal L2 book holder. This is a stub to allow connectors to import and run
  smoke tests. It does not enforce sequencing or checksums yet.
  """
  symbol: str
  bids: Dict[float, float] = field(default_factory=dict)  # price -> qty
  asks: Dict[float, float] = field(default_factory=dict)  # price -> qty
  last_update_id: int = 0

  @property
  def initialized(self) -> bool:
    return self.last_update_id > 0

  def load_snapshot(self, bids: List[List[float]], asks: List[List[float]], last_update_id: int | None = None) -> None:
    self.snapshot(bids, asks)
    if last_update_id is not None:
      self.last_update_id = int(last_update_id)

  def snapshot(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> None:
    self.bids = {float(px): float(qty) for px, qty in bids if float(qty) > 0}
    self.asks = {float(px): float(qty) for px, qty in asks if float(qty) > 0}

  def update(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> None:
    for px, qty in bids:
      px = float(px)
      qty = float(qty)
      if qty <= 0:
        self.bids.pop(px, None)
      else:
        self.bids[px] = qty
    for px, qty in asks:
      px = float(px)
      qty = float(qty)
      if qty <= 0:
        self.asks.pop(px, None)
      else:
        self.asks[px] = qty

  def apply_delta(self, bids: List[List[float]], asks: List[List[float]], update_id: int | None = None) -> List[Dict[str, float]]:
    """
    Apply deltas and return microdelta records with side/px/qty.
    """
    changes: List[Dict[str, float]] = []
    for px, qty in bids:
      px_f = float(px)
      qty_f = float(qty)
      if qty_f <= 0:
        self.bids.pop(px_f, None)
      else:
        self.bids[px_f] = qty_f
      changes.append({"side": "bid", "px": px_f, "qty": qty_f})

    for px, qty in asks:
      px_f = float(px)
      qty_f = float(qty)
      if qty_f <= 0:
        self.asks.pop(px_f, None)
      else:
        self.asks[px_f] = qty_f
      changes.append({"side": "ask", "px": px_f, "qty": qty_f})

    if update_id is not None:
      self.last_update_id = int(update_id)

    return changes

  def load_full(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> None:
    """
    Load a full snapshot (for exchanges that send snapshots like Hyperliquid).
    """
    self.bids = {float(px): float(qty) for px, qty in bids if float(qty) > 0}
    self.asks = {float(px): float(qty) for px, qty in asks if float(qty) > 0}
    self.last_update_id = 1  # Mark as initialized

  def apply_full_diff(self, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> List[Dict[str, float]]:
    """
    Apply a full snapshot by diffing against current state.
    Returns only the changed levels as microdeltas.
    """
    changes: List[Dict[str, float]] = []
    
    # Convert new state to dicts
    new_bids = {float(px): float(qty) for px, qty in bids if float(qty) > 0}
    new_asks = {float(px): float(qty) for px, qty in asks if float(qty) > 0}
    
    # Find bid changes
    all_bid_prices = set(self.bids.keys()) | set(new_bids.keys())
    for px in all_bid_prices:
      old_qty = self.bids.get(px, 0.0)
      new_qty = new_bids.get(px, 0.0)
      if old_qty != new_qty:
        changes.append({"side": "bid", "price": px, "qty": new_qty})
    
    # Find ask changes
    all_ask_prices = set(self.asks.keys()) | set(new_asks.keys())
    for px in all_ask_prices:
      old_qty = self.asks.get(px, 0.0)
      new_qty = new_asks.get(px, 0.0)
      if old_qty != new_qty:
        changes.append({"side": "ask", "price": px, "qty": new_qty})
    
    # Update state
    self.bids = new_bids
    self.asks = new_asks
    
    return changes

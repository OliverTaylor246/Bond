"""
Per-exchange capability map for StreamConfig validation.

Encodes which channels each venue supports and any constraints (e.g., depth limits).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


FundingMode = Literal["ws", "rest", "none"]
OHLCVProxyMode = Literal["ws", "rest", "none"]


@dataclass(frozen=True)
class VenueCapabilities:
  orderbook_l2: bool
  orderbook_l3: bool
  trades: bool
  ticker: bool
  funding: FundingMode
  ohlcv_proxy: OHLCVProxyMode
  ohlcv_internal: bool
  open_interest: bool
  depth_limit: Optional[int] = None


# Capability matrix derived from the provided table.
VENUE_CAPABILITIES: dict[str, VenueCapabilities] = {
  "binance": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=True,
    trades=True,
    ticker=True,
    funding="rest",
    ohlcv_proxy="ws",
    ohlcv_internal=True,
    open_interest=False,
    depth_limit=None,
  ),
  "bybit": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=True,
    trades=True,
    ticker=True,
    funding="ws",
    ohlcv_proxy="ws",
    ohlcv_internal=True,
    open_interest=True,
    depth_limit=None,
  ),
  "okx": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=False,
    trades=True,
    ticker=True,
    funding="rest",
    ohlcv_proxy="ws",
    ohlcv_internal=True,
    open_interest=True,
    depth_limit=None,
  ),
  "kucoin": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=False,
    trades=True,
    ticker=True,
    funding="rest",
    ohlcv_proxy="rest",
    ohlcv_internal=True,
    open_interest=False,
    depth_limit=None,
  ),
  "mexc": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=False,
    trades=True,
    ticker=True,
    funding="rest",
    ohlcv_proxy="none",
    ohlcv_internal=True,
    open_interest=False,
    depth_limit=None,
  ),
  "hyperliquid": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=True,
    trades=True,
    ticker=True,
    funding="ws",
    ohlcv_proxy="none",  # internal only per spec
    ohlcv_internal=True,
    open_interest=True,
    depth_limit=40,  # top 40 depth cap
  ),
  "lighter": VenueCapabilities(
    orderbook_l2=True,
    orderbook_l3=False,
    trades=True,
    ticker=False,
    funding="none",
    ohlcv_proxy="none",
    ohlcv_internal=True,
    open_interest=False,
    depth_limit=None,
  ),
}


__all__ = ["VenueCapabilities", "VENUE_CAPABILITIES"]

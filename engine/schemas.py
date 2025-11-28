from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, Field
from engine.venues import VenueCapabilities, VENUE_CAPABILITIES

# ---------------------------------------------------------------------
# Common types
# ---------------------------------------------------------------------

Millis = int  # UTC epoch in milliseconds
Price = float
Size = float
PriceSize = Tuple[Price, Size]


class EventType(str, Enum):
    TRADE = "trade"
    ORDERBOOK = "orderbook"
    TICKER = "ticker"
    FUNDING = "funding"
    HEARTBEAT = "heartbeat"
    RAW = "raw"
    SYSTEM = "system"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class BookUpdateType(str, Enum):
    SNAPSHOT = "snapshot"
    DELTA = "delta"


class MarketType(str, Enum):
    SPOT = "spot"
    PERP = "perp"
    FUTURE = "future"
    OPTION = "option"


# ---------------------------------------------------------------------
# Base event and helpers
# ---------------------------------------------------------------------


@dataclass(slots=True)
class BaseEvent:
    """
    Base normalized event.

    This is the *logical* schema; the on-the-wire JSON should match field names
    exactly for backward compatibility.
    """

    type: EventType
    exchange: str
    symbol: str
    ts_event: Millis
    ts_exchange: Optional[Millis] = None
    market_type: Optional[MarketType | str] = None
    raw: Optional[Dict[str, Any]] = None
    ts_internal: Optional[int] = None  # monotonic ns since start of process
    seq_internal: Optional[int] = None  # monotonic counter assigned by broker

    def to_wire(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        for key, value in list(d.items()):
            if isinstance(value, Enum):
                d[key] = value.value
        return d


# ---------------------------------------------------------------------
# Trade schema
# ---------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Trade(BaseEvent):
    type: EventType = field(init=False, default=EventType.TRADE)
    price: Price
    size: Size
    side: Side
    trade_id: Optional[str] = None
    match_id: Optional[str] = None
    agg_trade_id: Optional[str] = None
    maker: Optional[bool] = None
    liquidity: Optional[str] = None
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    is_liquidation: Optional[bool] = None
    is_block: Optional[bool] = None
    is_odd_lot: Optional[bool] = None
    fee: Optional[float] = None
    fee_currency: Optional[str] = None
    sequence: Optional[int] = None


# ---------------------------------------------------------------------
# Orderbook schema
# ---------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class OrderBook(BaseEvent):
    type: EventType = field(init=False, default=EventType.ORDERBOOK)
    bids: List[PriceSize]
    asks: List[PriceSize]
    update_type: BookUpdateType = BookUpdateType.SNAPSHOT
    depth: Optional[int] = None
    sequence: Optional[int] = None
    prev_sequence: Optional[int] = None
    checksum: Optional[int] = None
    is_reset: bool = False
    book_id: Optional[str] = None

    @property
    def best_bid(self) -> Optional[PriceSize]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[PriceSize]:
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Optional[Price]:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0][0] + self.asks[0][0]) / 2.0

    @property
    def spread(self) -> Optional[Price]:
        if not self.bids or not self.asks:
            return None
        return self.asks[0][0] - self.bids[0][0]


# ---------------------------------------------------------------------
# Ticker & Funding schemas
# ---------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Ticker(BaseEvent):
    type: EventType = field(init=False, default=EventType.TICKER)
    last: Optional[Price] = None
    mark: Optional[Price] = None
    index: Optional[Price] = None
    open_interest: Optional[float] = None


@dataclass(slots=True, kw_only=True)
class FundingUpdate(BaseEvent):
    type: EventType = field(init=False, default=EventType.FUNDING)
    rate: Optional[float] = None
    next_time: Optional[Millis] = None
    mark: Optional[Price] = None
    index: Optional[Price] = None


# ---------------------------------------------------------------------
# Heartbeats & raw messages
# ---------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Heartbeat(BaseEvent):
    type: EventType = field(init=False, default=EventType.HEARTBEAT)
    info: Optional[Dict[str, Any]] = None


@dataclass(slots=True, kw_only=True)
class RawMessage(BaseEvent):
    type: EventType = field(init=False, default=EventType.RAW)
    payload: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------
# Normalization utilities
# ---------------------------------------------------------------------


def normalize_symbol(exchange: str, raw_symbol: str) -> str:
    s = raw_symbol.replace("-", "").replace("_", "").replace("/", "").upper()
    common_quotes = ("USDT", "USDC", "BTC", "ETH", "USD", "EUR")
    for q in common_quotes:
        if s.endswith(q):
            base = s[: -len(q)]
            quote = q
            return f"{base}/{quote}"
    return raw_symbol


def unify_side(raw_side: Any) -> Side:
    if raw_side is None:
        return Side.UNKNOWN
    if isinstance(raw_side, bool):
        return Side.BUY if raw_side else Side.SELL
    s = str(raw_side).strip().lower()
    if s in ("b", "buy", "bid", "long"):
        return Side.BUY
    if s in ("s", "sell", "ask", "short", "offer", "ofr"):
        return Side.SELL
    return Side.UNKNOWN


# ---------------------------------------------------------------------
# Legacy models (Bond/Ekko compatibility)
# ---------------------------------------------------------------------


class TradeEvent(BaseModel):
  """Normalized trade event from any exchange."""
  ts: datetime = Field(description="UTC timestamp of event")
  source: str = Field(description="Exchange or data source name")
  symbol: str = Field(description="Trading pair (e.g., BTC/USDT)")
  price: float = Field(description="Trade price")
  qty: float = Field(description="Trade quantity")
  side: Optional[Literal["buy", "sell"]] = None


class TwitterEvent(BaseModel):
  """Normalized tweet/X event."""
  ts: datetime
  source: Literal["twitter"] = "twitter"
  text: str
  symbol: Optional[str] = None
  sentiment: Optional[float] = Field(None, ge=-1.0, le=1.0)
  tweet_id: Optional[str] = None
  user: Optional[str] = None


class OnchainEvent(BaseModel):
  """
  Normalized on-chain event from gRPC (Solana Yellowstone/Geyser).

  Fields:
    ts: Event timestamp (UTC)
    source: Always "onchain"
    chain: Blockchain (e.g., "sol" for Solana, "evm" for Ethereum)
    kind: Event type ("tx" | "transfer" | "metric" | "block" | "account")
    value: Numeric value (amount, count, etc.) - optional
    meta: Additional metadata (signature, slot, accounts, etc.)
  """
  ts: datetime
  source: Literal["onchain"] = "onchain"
  chain: str = Field(description="Blockchain identifier (sol, evm, etc.)")
  kind: Literal["tx", "transfer", "metric", "block", "account"] = Field(
    description="Event type"
  )
  value: Optional[float] = Field(None, description="Numeric value if applicable")
  meta: dict[str, Any] = Field(
    default_factory=dict,
    description="Additional context (signature, slot, program_id, etc.)"
  )


class CustomEvent(BaseModel):
  """Generic custom WebSocket event."""
  ts: datetime
  source: str
  event_type: str
  data: dict[str, Any]


class PolymarketEvent(BaseModel):
  """Polymarket event snapshot (prediction market metadata)."""
  ts: datetime
  source: Literal["polymarket"] = "polymarket"
  event_id: str = Field(description="Polymarket event identifier")
  slug: str = Field(description="Human-readable slug")
  title: str = Field(description="Event title")
  category: Optional[str] = Field(None, description="Polymarket category label")
  active: bool = Field(True, description="Whether the event is active")
  closed: bool = Field(False, description="Whether the event is closed")
  open_interest: Optional[float] = Field(None, description="Open interest (USD)")
  liquidity: Optional[float] = Field(None, description="Available liquidity (USD)")
  volume_total: Optional[float] = Field(None, description="Lifetime volume (USD)")
  volume_24h: Optional[float] = Field(None, description="24h volume (USD)")
  market_count: int = Field(0, description="Number of markets attached to event")
  markets: list[dict[str, Any]] = Field(
    default_factory=list,
    description="Subset of market metadata (questions, prices, liquidity)",
  )
  metadata: dict[str, Any] = Field(
    default_factory=dict,
    description="Additional context (start/end dates, resolution source, media)",
  )


class AggregatedEvent(BaseModel):
  """
  Merged event from multiple sources (output of pipeline).

  v0.2 additions:
    - onchain_count: Count of on-chain events in window
    - onchain_value_sum: Sum of on-chain event values in window
    - window_start/window_end: Optional window boundaries

  v0.3 additions:
    - Extended CCXT fields: bid, ask, high, low, open, close
  """
  ts: datetime
  window_start: Optional[datetime] = None
  window_end: Optional[datetime] = None
  price_avg: Optional[float] = None
  price_high: Optional[float] = None
  price_low: Optional[float] = None
  price_open: Optional[float] = None
  price_close: Optional[float] = None
  bid_avg: Optional[float] = None
  ask_avg: Optional[float] = None
  volume_sum: Optional[float] = None
  symbol: Optional[str] = None
  exchange: Optional[str] = None
  tweets: int = 0
  onchain_count: int = 0
  onchain_value_sum: Optional[float] = None
  custom_count: int = 0
  raw_data: dict[str, Any] = Field(default_factory=dict)


class ResampleTransform(BaseModel):
  op: Literal["resample"] = "resample"
  window: str = Field(description="Time window (e.g., '5s', '1m')")
  on: Optional[str] = Field(None, description="Field to aggregate")
  agg: list[Literal["mean", "last", "sum", "min", "max", "count"]] = Field(
    default_factory=lambda: ["mean"]
  )


class CountTransform(BaseModel):
  op: Literal["count"] = "count"
  window: str = Field(description="Time window (e.g., '10s')")
  as_field: str = Field("count", alias="as", description="Output field name")


class ProjectTransform(BaseModel):
  op: Literal["project"] = "project"
  fields: list[str] = Field(description="List of fields to keep")


# ---------------------------------------------------------------------
# New stream config (canonical)
# ---------------------------------------------------------------------


class ChannelOrderBook(BaseModel):
  enabled: bool = True
  depth: int = Field(20, gt=0, description="Max depth to request")
  l3: bool = Field(False, description="Request per-order L3 data if supported")


class ChannelOHLCV(BaseModel):
  enabled: bool = False
  interval: str = Field("1s", description="Candle interval (e.g., 1s, 1m, 1h)")
  mode: Literal["internal", "proxy"] = Field(
    "internal",
    description='proxy=exchange feed, internal=build from trades',
  )


class StreamChannels(BaseModel):
  orderbook: ChannelOrderBook = Field(default_factory=ChannelOrderBook)
  trades: bool = True
  ticker: bool = True
  funding: bool = False
  ohlcv: ChannelOHLCV = Field(default_factory=ChannelOHLCV)
  open_interest: bool = False


class StreamConfig(BaseModel):
  """
  Canonical stream configuration for exchange-native pipelines.
  """

  stream_id: str
  exchange: str
  symbols: List[str] = Field(..., min_length=1)
  channels: StreamChannels
  heartbeat_ms: int = Field(2000, gt=0)
  flush_interval_ms: int = Field(100, gt=0)
  ttl: int = Field(10, gt=0, description="Seconds to keep stream metadata alive")

  @staticmethod
  def _caps(exchange: str) -> VenueCapabilities:
    caps = VENUE_CAPABILITIES.get(exchange.lower())
    if not caps:
      raise ValueError(f"Unsupported exchange '{exchange}'")
    return caps

  @staticmethod
  def _require(cond: bool, message: str) -> None:
    if not cond:
      raise ValueError(message)

  def validate_against_capabilities(self) -> "StreamConfig":
    """
    Validate requested channels against per-venue capabilities.
    """
    caps = self._caps(self.exchange)

    # Orderbook / depth / L3
    if self.channels.orderbook.enabled:
      self._require(caps.orderbook_l2, f"{self.exchange} does not support L2 orderbooks")
      if self.channels.orderbook.l3:
        self._require(caps.orderbook_l3, f"{self.exchange} does not support L3 orderbooks")
      if caps.depth_limit is not None and self.channels.orderbook.depth > caps.depth_limit:
        raise ValueError(
          f"{self.exchange} depth capped at {caps.depth_limit} (requested {self.channels.orderbook.depth})"
        )

    # Trades
    if self.channels.trades:
      self._require(caps.trades, f"{self.exchange} does not support trades")

    # Ticker
    if self.channels.ticker:
      self._require(caps.ticker, f"{self.exchange} does not support ticker channel")

    # Funding
    if self.channels.funding:
      self._require(
        caps.funding != "none",
        f"{self.exchange} does not expose funding",
      )

    # OHLCV
    if self.channels.ohlcv.enabled:
      mode = self.channels.ohlcv.mode
      if mode == "proxy":
        self._require(
          caps.ohlcv_proxy != "none",
          f"{self.exchange} has no proxy OHLCV feed",
        )
      elif mode == "internal":
        self._require(
          caps.ohlcv_internal,
          f"{self.exchange} cannot build internal OHLCV",
        )
        self._require(
          self.channels.trades and caps.trades,
          f"{self.exchange} internal OHLCV requires trades channel enabled",
        )

    # Open interest
    if self.channels.open_interest:
      self._require(
        caps.open_interest,
        f"{self.exchange} does not support open interest",
      )

    # Symbols sanity
    for sym in self.symbols:
      if not isinstance(sym, str) or not sym.strip():
        raise ValueError("Symbols must be non-empty strings")

    return self


class StreamSpec(BaseModel):
  version: Literal["v1"] = "v1"
  name: Optional[str] = None
  description: Optional[str] = None
  sources: list[dict[str, Any]] = Field(
    description="List of data sources (ccxt, onchain, twitter, custom)"
  )
  interval_sec: float = Field(5, description="Aggregation window in seconds")
  symbols: list[str] = Field(["BTC/USDT"], description="Symbols to track")
  transforms: Optional[list[dict[str, Any]]] = Field(
    None,
    description="Transforms to apply (resample, count, project)"
  )
  ttl_sec: Optional[int] = Field(
    None,
    description="Optional stream TTL in seconds"
  )

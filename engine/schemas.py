"""
Event schemas for bond streaming platform.
DO NOT MODIFY without strong justification - this defines the standard event model.

Version: v0.2 (Indie Quant + On-Chain gRPC)
"""
from datetime import datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


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


class AggregatedEvent(BaseModel):
  """
  Merged event from multiple sources (output of pipeline).

  v0.2 additions:
    - onchain_count: Count of on-chain events in window
    - onchain_value_sum: Sum of on-chain event values in window
    - window_start/window_end: Optional window boundaries
  """
  ts: datetime
  window_start: Optional[datetime] = None
  window_end: Optional[datetime] = None
  price_avg: Optional[float] = None
  volume_sum: Optional[float] = None
  tweets: int = 0
  onchain_count: int = 0
  onchain_value_sum: Optional[float] = None
  custom_count: int = 0
  raw_data: dict[str, Any] = Field(default_factory=dict)


class ResampleTransform(BaseModel):
  """
  Resample events over time windows with aggregations.

  Example:
    {"op": "resample", "window": "5s", "on": "price", "agg": ["mean", "last"]}
  """
  op: Literal["resample"] = "resample"
  window: str = Field(description="Time window (e.g., '5s', '1m')")
  on: Optional[str] = Field(None, description="Field to aggregate")
  agg: list[Literal["mean", "last", "sum", "min", "max", "count"]] = Field(
    default_factory=lambda: ["mean"]
  )


class CountTransform(BaseModel):
  """
  Count events over a time window.

  Example:
    {"op": "count", "window": "10s", "as": "event_count"}
  """
  op: Literal["count"] = "count"
  window: str = Field(description="Time window (e.g., '10s')")
  as_field: str = Field("count", alias="as", description="Output field name")


class ProjectTransform(BaseModel):
  """
  Project (select) specific fields from events.

  Example:
    {"op": "project", "fields": ["price", "volume", "ts"]}
  """
  op: Literal["project"] = "project"
  fields: list[str] = Field(description="List of fields to keep")


class StreamSpec(BaseModel):
  """
  Stream specification - defines what data sources and aggregation to use.

  EARLY PHASE - avoid schema changes without backward compatibility plan.

  v0.2 additions:
    - transforms: List of transform operations (resample, count, project)
    - version: Schema version for backward compatibility
    - name/description: Optional metadata
  """
  version: Literal["v1"] = "v1"
  name: Optional[str] = None
  description: Optional[str] = None
  sources: list[dict[str, Any]] = Field(
    description="List of data sources (ccxt, onchain, twitter, custom)"
  )
  interval_sec: int = Field(5, description="Aggregation window in seconds")
  symbols: list[str] = Field(["BTC/USDT"], description="Symbols to track")
  transforms: Optional[list[dict[str, Any]]] = Field(
    None,
    description="Transforms to apply (resample, count, project)"
  )
  ttl_sec: Optional[int] = Field(
    None,
    description="Optional stream TTL in seconds"
  )

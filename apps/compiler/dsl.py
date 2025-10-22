"""
StreamSpec DSL models for Bond compiler.
Defines the structure for stream specifications with transforms support.
"""
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class StreamSpec(BaseModel):
  """
  Stream specification model (identical to engine/schemas.py for compatibility).
  
  This defines what data sources and transformations to apply.
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

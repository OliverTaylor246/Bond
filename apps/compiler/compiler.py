"""
Stream specification compiler.
Parses natural language or JSON into canonical StreamSpec.

EARLY PHASE - avoid schema changes without backward compatibility plan.
"""
import hashlib
import json
import re
from typing import Any
from engine.schemas import StreamSpec


def compile_spec(input_spec: str | dict) -> StreamSpec:
  """
  Compile a natural language string or dict into a StreamSpec.

  Args:
    input_spec: Either a natural language string or a dict

  Returns:
    Validated StreamSpec object

  Examples:
    >>> compile_spec("BTC price + tweets every 5s")
    StreamSpec(sources=[...], interval_sec=5, symbols=["BTC/USDT"])
  """
  if isinstance(input_spec, dict):
    # Already structured - just validate
    return StreamSpec(**input_spec)

  # Parse natural language
  spec_dict = _parse_natural_language(input_spec)
  return StreamSpec(**spec_dict)


def _parse_natural_language(text: str) -> dict[str, Any]:
  """
  Simple NL parser for common patterns.

  Supported patterns:
    - "BTC price + tweets every 5s"
    - "ETH trades + liquidations every 10 seconds"
    - "BTC/USDT market data with twitter feed"
  """
  text_lower = text.lower()

  # Extract interval
  interval_sec = 5  # default
  interval_match = re.search(r"every\s+(\d+)\s*s(?:ec(?:onds?)?)?", text_lower)
  if interval_match:
    interval_sec = int(interval_match.group(1))

  # Extract symbols
  symbols = []
  symbol_patterns = [
    r"\b([A-Z]{3,5})/([A-Z]{3,5})\b",  # BTC/USDT format
    r"\b([A-Z]{3,5})\s+(?:price|trades?|market)\b",  # BTC price format
  ]

  for pattern in symbol_patterns:
    matches = re.findall(pattern, text)
    for match in matches:
      if isinstance(match, tuple):
        if len(match) == 2:
          symbols.append(f"{match[0]}/{match[1]}")
        else:
          # Single symbol, add /USDT suffix
          symbols.append(f"{match[0]}/USDT")
      else:
        # Single symbol, add /USDT suffix
        symbols.append(f"{match}/USDT")

  if not symbols:
    symbols = ["BTC/USDT"]  # default

  # Determine sources
  sources = []

  # CCXT (price, trades, volume)
  if any(kw in text_lower for kw in ["price", "trades", "volume", "market"]):
    sources.append({"type": "ccxt", "symbols": symbols})

  # Twitter
  if any(kw in text_lower for kw in ["tweet", "twitter", "x ", "sentiment"]):
    sources.append({"type": "twitter", "symbols": symbols})

  # Custom (liquidations, etc.)
  if any(kw in text_lower for kw in ["liquidation", "liq", "custom"]):
    sources.append({"type": "custom", "mode": "mock_liq"})

  # Default to all three if none specified
  if not sources:
    sources = [
      {"type": "ccxt", "symbols": symbols},
      {"type": "twitter", "symbols": symbols},
      {"type": "custom", "mode": "mock_liq"},
    ]

  # Market type detection (futures/perpetuals vs spot)
  if any(kw in text_lower for kw in ["future", "futures", "perp", "perpetual"]):
    for source in sources:
      if source.get("type") == "ccxt":
        source["market_type"] = "futures"
  elif "spot" in text_lower:
    for source in sources:
      if source.get("type") == "ccxt":
        source["market_type"] = "spot"

  return {
    "sources": sources,
    "interval_sec": interval_sec,
    "symbols": symbols,
  }


def generate_stream_id(spec: StreamSpec) -> str:
  """
  Generate deterministic hash ID for a stream spec.

  Uses Blake2b for fast, collision-resistant hashing.

  Args:
    spec: StreamSpec to hash

  Returns:
    16-character hex stream ID
  """
  # Canonical JSON representation
  canonical = json.dumps(
    spec.model_dump(),
    sort_keys=True,
    separators=(",", ":"),
  )

  # Blake2b hash (16 bytes = 32 hex chars, truncate to 16)
  h = hashlib.blake2b(canonical.encode(), digest_size=8)
  return h.hexdigest()

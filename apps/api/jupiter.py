"""
Helpers for interacting with Jupiter Lite API (search + price endpoints).
"""
from __future__ import annotations

import os
from typing import Any, Optional, Sequence, Tuple

import httpx

JUPITER_BASE_URL = os.getenv("JUPITER_BASE_URL", "https://lite-api.jup.ag")
JUPITER_SEARCH_URL = os.getenv(
  "JUPITER_SEARCH_URL",
  f"{JUPITER_BASE_URL}/ultra/v1/search",
)
JUPITER_PRICE_URL = os.getenv(
  "JUPITER_PRICE_URL",
  f"{JUPITER_BASE_URL}/price/v3",
)

DEFAULT_TIMEOUT = float(os.getenv("JUPITER_HTTP_TIMEOUT", "10.0"))


def _to_float(value: Any) -> float | None:
  if value is None:
    return None
  try:
    if isinstance(value, str):
      cleaned = value.replace(",", "").strip()
      if not cleaned:
        return None
      value = cleaned
    return float(value)
  except (TypeError, ValueError):
    return None


def _sanitize_symbol(symbol: str | None) -> str:
  if not symbol:
    return ""
  return symbol.replace("$", "").strip().upper()


def _extract_volume(raw_token: dict[str, Any]) -> float | None:
  stats = raw_token.get("stats24h") or raw_token.get("stats24H") or {}
  if isinstance(stats, dict):
    buy = _to_float(stats.get("buyVolume"))
    sell = _to_float(stats.get("sellVolume"))
    total = (buy or 0.0) + (sell or 0.0)
    if total > 0:
      return total
  volume = _to_float(raw_token.get("volume24h") or raw_token.get("volume"))
  return volume


def summarize_token(raw_token: dict[str, Any]) -> dict[str, Any] | None:
  """Convert a raw Jupiter token into a normalized summary dict."""
  if not isinstance(raw_token, dict):
    return None
  mint = raw_token.get("id") or raw_token.get("mint")
  if not mint:
    return None
  symbol = _sanitize_symbol(raw_token.get("symbol"))
  summary = {
    "id": mint,
    "contract": mint,
    "name": raw_token.get("name") or symbol or mint,
    "symbol": symbol or mint[:6],
    "usd_price": _to_float(raw_token.get("usdPrice")),
    "mcap": _to_float(raw_token.get("mcap") or raw_token.get("fdv")),
    "fdv": _to_float(raw_token.get("fdv") or raw_token.get("mcap")),
    "liquidity": _to_float(raw_token.get("liquidity")),
    "volume_24h": _extract_volume(raw_token),
    "decimals": raw_token.get("decimals"),
    "icon": raw_token.get("icon"),
    "is_verified": bool(raw_token.get("isVerified"))
      if raw_token.get("isVerified") is not None
      else None,
    "tags": [
      tag for tag in raw_token.get("tags", []) if isinstance(tag, str)
    ],
    "raw": raw_token,
  }
  return summary


async def search_tokens(
  query: str,
  *,
  limit: int = 5,
  http_client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
  """Search for tokens on Jupiter by ticker/name and return normalized summaries."""
  if not query:
    return []

  client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
  owns_client = http_client is None

  try:
    response = await client.get(JUPITER_SEARCH_URL, params={"query": query})
    response.raise_for_status()
    payload = response.json()
  finally:
    if owns_client:
      await client.aclose()

  if isinstance(payload, list):
    raw_tokens = payload
  elif isinstance(payload, dict):
    raw_tokens = payload.get("tokens") or payload.get("data") or []
  else:
    raw_tokens = []

  summaries: list[dict[str, Any]] = []
  for raw in raw_tokens[:max(1, limit)]:
    summary = summarize_token(raw)
    if summary:
      summaries.append(summary)
  return summaries


def pick_best_token(
  tokens: Sequence[dict[str, Any]],
) -> Tuple[dict[str, Any] | None, str | None]:
  """
  Select the preferred token based on marketcap + volume heuristic.

  Returns the best token and a short reason describing the selection criteria.
  """
  if not tokens:
    return None, None

  def mcap_value(token: dict[str, Any]) -> float:
    return float(token.get("mcap") or 0.0)

  def volume_value(token: dict[str, Any]) -> float:
    return float(token.get("volume_24h") or 0.0)

  best_mcap = max(tokens, key=mcap_value, default=None)
  best_volume = max(tokens, key=volume_value, default=None)
  if best_mcap and best_volume and best_mcap.get("id") == best_volume.get("id"):
    return best_mcap, "highest_marketcap_and_volume"
  return best_mcap, "highest_marketcap"


async def fetch_price_snapshot(
  mint: str,
  *,
  http_client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any] | None:
  """Fetch a single price snapshot for the given mint from Jupiter."""
  if not mint:
    return None

  client = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
  owns_client = http_client is None

  try:
    response = await client.get(JUPITER_PRICE_URL, params={"ids": mint})
    response.raise_for_status()
    payload = response.json()
  finally:
    if owns_client:
      await client.aclose()

  if not isinstance(payload, dict):
    return None
  token_data = payload.get(mint)
  if not isinstance(token_data, dict):
    return None

  return {
    "mint": mint,
    "usd_price": _to_float(token_data.get("usdPrice")),
    "price_change_24h": _to_float(token_data.get("priceChange24h")),
    "block_id": token_data.get("blockId"),
    "decimals": token_data.get("decimals"),
  }


__all__ = [
  "fetch_price_snapshot",
  "pick_best_token",
  "search_tokens",
  "summarize_token",
]

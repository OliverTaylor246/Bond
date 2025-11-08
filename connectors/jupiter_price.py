"""
Jupiter price polling connector.
Continuously fetches token prices from the Jupiter Lite API and emits normalized events.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

import httpx

JUPITER_PRICE_URL = os.getenv("JUPITER_PRICE_URL", "https://lite-api.jup.ag/price/v3")


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


async def jupiter_price_stream(
  mint: str,
  *,
  symbol: str | None = None,
  interval_sec: float = 2.0,
  http_client: Optional[httpx.AsyncClient] = None,
) -> AsyncIterator[dict[str, Any]]:
  """
  Poll Jupiter for the latest USD price of the given mint and emit trade-like events.
  """
  if not mint:
    return

  interval = max(1.0, float(interval_sec or 2.0))
  client = http_client or httpx.AsyncClient(timeout=10.0)
  owns_client = http_client is None

  try:
    while True:
      token_data: dict[str, Any] | None = None
      try:
        response = await client.get(JUPITER_PRICE_URL, params={"ids": mint})
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
          token_data = payload.get(mint)
      except Exception as err:
        print(f"[jupiter] price fetch failed for {mint}: {err}", flush=True)

      if token_data:
        price = _to_float(token_data.get("usdPrice"))
        if price is not None:
          yield {
            "ts": datetime.now(timezone.utc),
            "source": "jupiter",
            "symbol": symbol or mint,
            "price": price,
            "volume": None,
            "metadata": {
              "mint": mint,
              "block_id": token_data.get("blockId"),
              "decimals": token_data.get("decimals"),
              "price_change_24h": _to_float(token_data.get("priceChange24h")),
            },
          }

      await asyncio.sleep(interval)
  finally:
    if owns_client:
      await client.aclose()

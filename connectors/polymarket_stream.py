"""
Polymarket events streaming connector + discovery helpers.
Polls the public gamma-api endpoint and emits normalized PolymarketEvent payloads.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable, Optional, Sequence

import httpx

from engine.schemas import PolymarketEvent

POLYMARKET_EVENTS_URL = os.getenv(
  "POLYMARKET_EVENTS_URL",
  "https://gamma-api.polymarket.com/events",
)
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET")

AUTH_HEADERS = {}
if POLYMARKET_API_KEY:
  AUTH_HEADERS["x-api-key"] = POLYMARKET_API_KEY
if POLYMARKET_API_SECRET:
  AUTH_HEADERS["x-api-secret"] = POLYMARKET_API_SECRET


def _normalize_list(values: Any) -> list[str]:
  if not values:
    return []
  if isinstance(values, str):
    return [values]
  if isinstance(values, Iterable):
    result: list[str] = []
    for value in values:
      if isinstance(value, str) and value.strip():
        result.append(value.strip())
    return result
  return []


def _to_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    if isinstance(value, str):
      value = value.replace(",", "").strip()
      if not value:
        return None
    return float(value)
  except (TypeError, ValueError):
    return None


def _json_list(value: Any) -> list[Any]:
  if isinstance(value, list):
    return value
  if isinstance(value, str):
    try:
      parsed = json.loads(value)
      if isinstance(parsed, list):
        return parsed
    except json.JSONDecodeError:
      return []
  return []


def _float_list(value: Any) -> list[float]:
  items = _json_list(value)
  result: list[float] = []
  for item in items:
    converted = _to_float(item)
    if converted is not None:
      result.append(converted)
  return result


def _summarize_markets(raw_markets: Any) -> list[dict[str, Any]]:
  if not isinstance(raw_markets, Iterable):
    return []

  summary: list[dict[str, Any]] = []
  for market in raw_markets:
    if not isinstance(market, dict):
      continue
    summary.append({
      "id": market.get("id"),
      "question": market.get("question"),
      "slug": market.get("slug"),
      "category": market.get("category"),
      "liquidity": _to_float(market.get("liquidity") or market.get("liquidityNum")),
      "volume": _to_float(market.get("volume") or market.get("volumeNum")),
      "best_bid": _to_float(market.get("bestBid") or market.get("bestBidPrice")),
      "best_ask": _to_float(market.get("bestAsk") or market.get("bestAskPrice")),
      "last_trade_price": _to_float(market.get("lastTradePrice")),
      "outcomes": _json_list(market.get("outcomes")),
      "outcome_prices": _float_list(market.get("outcomePrices")),
      "endDate": market.get("endDate"),
      "startDate": market.get("startDate"),
    })
  return summary


def normalize_polymarket_event(
  raw_event: dict[str, Any],
  *,
  timestamp: Optional[datetime] = None,
) -> Optional[PolymarketEvent]:
  """Convert a raw Polymarket API payload into PolymarketEvent."""
  if not isinstance(raw_event, dict):
    return None

  event_id = str(raw_event.get("id") or raw_event.get("slug") or "").strip()
  if not event_id:
    return None

  slug = str(raw_event.get("slug") or event_id)
  markets = _summarize_markets(raw_event.get("markets"))
  updated_at = raw_event.get("updatedAt") or raw_event.get("updated_at")

  return PolymarketEvent(
    ts=timestamp or datetime.now(timezone.utc),
    event_id=event_id,
    slug=slug,
    title=raw_event.get("title") or raw_event.get("ticker") or slug,
    category=raw_event.get("category"),
    active=bool(raw_event.get("active", False)),
    closed=bool(raw_event.get("closed", False)),
    open_interest=_to_float(raw_event.get("openInterest")),
    liquidity=_to_float(
      raw_event.get("liquidity")
      or raw_event.get("liquidityAmm")
      or raw_event.get("liquidityClob")
    ),
    volume_total=_to_float(raw_event.get("volume")),
    volume_24h=_to_float(raw_event.get("volume24hr")),
    market_count=len(markets),
    markets=markets,
    metadata={
      "resolutionSource": raw_event.get("resolutionSource"),
      "startDate": raw_event.get("startDate"),
      "endDate": raw_event.get("endDate"),
      "image": raw_event.get("image"),
      "icon": raw_event.get("icon"),
      "series": raw_event.get("series"),
      "tags": raw_event.get("tags"),
      "updatedAt": updated_at,
      "description": raw_event.get("description"),
    },
  )


async def polymarket_events_stream(
  event_ids: Sequence[str] | None = None,
  categories: Sequence[str] | None = None,
  tags: Sequence[str] | None = None,
  include_closed: bool = False,
  active_only: bool = True,
  interval: int = 60,
  limit: int = 100,
  http_client: Optional[httpx.AsyncClient] = None,
) -> AsyncIterator[dict]:
  """
  Stream Polymarket events with deduplicated updates.

  Args:
    event_ids: Optional list of event IDs or slugs to monitor
    categories: Optional list of categories (case-insensitive)
    include_closed: Whether to include closed events
    active_only: Whether to require events to be active
    interval: Poll interval in seconds (min 15s)
    limit: Number of events per API call (capped at 250)
    http_client: Optional httpx.AsyncClient for testing/DI
  """
  interval = max(15, int(interval))
  limit = max(1, min(int(limit), 250))
  event_filter = {item.lower() for item in _normalize_list(event_ids)} or None
  category_filter = {item.lower() for item in _normalize_list(categories)} or None

  seen_versions: "OrderedDict[str, str]" = OrderedDict()
  max_seen = 1024

  tag_filter = {item.lower() for item in _normalize_list(tags)} or None

  client = http_client or httpx.AsyncClient(timeout=20.0, headers=AUTH_HEADERS or None)
  owns_client = http_client is None

  print(
    "[polymarket] Starting stream "
    f"(interval={interval}s, limit={limit}, "
    f"event_filter={event_filter}, categories={category_filter})",
    flush=True,
  )

  try:
    while True:
      try:
        params = {"limit": limit, "offset": 0}
        if tag_filter:
          params["tag"] = next(iter(tag_filter))

        response = await client.get(
          POLYMARKET_EVENTS_URL,
          params=params,
        )
        response.raise_for_status()
        events = response.json()
        if not isinstance(events, list):
          raise ValueError("Unexpected response format from Polymarket events API")
      except Exception as err:
        print(f"[polymarket] Error fetching events: {err}", flush=True)
        yield {
          "ts": datetime.now(timezone.utc),
          "source": "polymarket",
          "event_type": "error",
          "error": str(err),
        }
        await asyncio.sleep(interval)
        continue

      now = datetime.now(timezone.utc)
      for raw_event in events:
        normalized = normalize_polymarket_event(raw_event, timestamp=now)
        if not normalized:
          continue

        slug = normalized.slug.lower()
        event_id_lower = normalized.event_id.lower()

        if event_filter and (
          event_id_lower not in event_filter and slug not in event_filter
        ):
          continue

        category = (normalized.category or "").lower()
        if category_filter and category not in category_filter:
          continue

        event_tags = set(
          tag.lower()
          for tag in _normalize_list(normalized.metadata.get("tags"))
        )
        if tag_filter and tag_filter.isdisjoint(event_tags):
          continue

        if active_only and not normalized.active:
          continue

        if not include_closed and normalized.closed:
          continue

        updated_at = normalized.metadata.get("updatedAt") or ""
        marker = f"{updated_at}:{normalized.open_interest}:{normalized.liquidity}"

        previous_marker = seen_versions.get(normalized.event_id)
        if previous_marker == marker:
          continue

        seen_versions[normalized.event_id] = marker
        seen_versions.move_to_end(normalized.event_id)
        if len(seen_versions) > max_seen:
          seen_versions.popitem(last=False)

        yield normalized.model_dump()

      await asyncio.sleep(interval)
  finally:
    if owns_client:
      await client.aclose()


STOP_WORDS = {
  "a",
  "an",
  "and",
  "are",
  "about",
  "all",
  "around",
  "at",
  "be",
  "can",
  "could",
  "do",
  "for",
  "from",
  "give",
  "have",
  "how",
  "in",
  "is",
  "it",
  "let",
  "me",
  "need",
  "of",
  "on",
  "please",
  "polymarket",
  "show",
  "stream",
  "tell",
  "that",
  "the",
  "this",
  "to",
  "want",
  "what",
  "when",
  "will",
  "with",
  "you",
}


def _build_query_terms(query: Optional[str]) -> list[str]:
  if not query:
    return []

  quoted = re.findall(r'"([^"]+)"', query)
  source_text = " ".join(quoted) if quoted else query

  tokens = [
    term
    for term in re.split(r"[^\w']+", source_text.lower())
    if term and len(term) > 2 and term not in STOP_WORDS
  ]

  if not tokens and quoted:
    tokens = [phrase.strip().lower() for phrase in quoted if phrase.strip()]

  # Deduplicate while preserving order
  seen: set[str] = set()
  terms: list[str] = []
  for token in tokens:
    if token not in seen:
      seen.add(token)
      terms.append(token)

  return terms[:6]


def _matches_query(event: PolymarketEvent, terms: list[str]) -> bool:
  if not terms:
    return True

  haystack_parts: list[str] = [
    event.title or "",
    event.slug or "",
    event.category or "",
    event.metadata.get("resolutionSource") or "",
    event.metadata.get("description") or "",
  ]
  for market in event.markets:
    question = market.get("question")
    if isinstance(question, str):
      haystack_parts.append(question)

  haystack = " ".join(part.lower() for part in haystack_parts if part)
  return all(term in haystack for term in terms)


async def discover_polymarket_events(
  query: Optional[str] = None,
  categories: Sequence[str] | None = None,
  tags: Sequence[str] | None = None,
  limit: int = 20,
  include_closed: bool = False,
  active_only: bool = True,
  http_client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
  """
  Fetch and filter Polymarket events for discovery/search use-cases.
  """
  limit = max(1, min(int(limit), 100))
  category_filter = {item.lower() for item in _normalize_list(categories)} or None
  query_terms = _build_query_terms(query)

  tag_filter = {item.lower() for item in _normalize_list(tags)} or None

  client = http_client or httpx.AsyncClient(timeout=20.0, headers=AUTH_HEADERS or None)
  owns_client = http_client is None

  results: list[dict[str, Any]] = []
  page_limit = 250
  offset = 0
  max_pages = 5
  pages = 0

  try:
    while len(results) < limit and pages < max_pages:
      try:
        params = {"limit": page_limit, "offset": offset}
        if tag_filter:
          params["tag"] = next(iter(tag_filter))

        response = await client.get(
          POLYMARKET_EVENTS_URL,
          params=params,
        )
        response.raise_for_status()
        events = response.json()
        if not isinstance(events, list):
          break
      except Exception as err:
        print(f"[polymarket] Discovery fetch failed: {err}", flush=True)
        break

      now = datetime.now(timezone.utc)
      for raw_event in events:
        normalized = normalize_polymarket_event(raw_event, timestamp=now)
        if not normalized:
          continue
        if active_only and not normalized.active:
          continue
        if not include_closed and normalized.closed:
          continue
        if category_filter and (normalized.category or "").lower() not in category_filter:
          continue
        event_tags = set(
          tag.lower()
          for tag in _normalize_list(normalized.metadata.get("tags"))
        )
        if tag_filter and tag_filter.isdisjoint(event_tags):
          continue
        if query_terms and not _matches_query(normalized, query_terms):
          continue

        results.append(normalized.model_dump())
        if len(results) >= limit:
          break

      if len(results) >= limit or not events:
        break

      offset += page_limit
      pages += 1

  finally:
    if owns_client:
      await client.aclose()

  return results

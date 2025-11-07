"""Polymarket-specific parsing helpers."""

from __future__ import annotations

import re
from typing import List, Tuple

from engine.schemas import StreamSpec

DEFAULT_INTERVAL = 3.0

CATEGORY_KEYWORDS = {
  "politics": {
    "election",
    "president",
    "congress",
    "parliament",
    "policy",
    "government",
    "shutdown",
    "senate",
    "house",
    "politic",
  },
  "sports": {
    "nba",
    "nfl",
    "mlb",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "game",
    "match",
    "tournament",
    "fight",
  },
  "crypto": {
    "bitcoin",
    "ethereum",
    "solana",
    "crypto",
    "token",
    "defi",
    "eth",
    "btc",
  },
  "finance": {
    "inflation",
    "interest",
    "fed",
    "stocks",
    "market",
    "economy",
    "gdp",
    "jobs",
    "employment",
  },
  "technology": {
    "ai",
    "tech",
    "startup",
    "nvidia",
    "apple",
    "google",
    "microsoft",
    "amazon",
    "openai",
  },
}

POLYMARKET_MARKERS = {
  "polymarket",
  "prediction market",
  "prediction markets",
  "polymarket events",
  "polymarket stream",
}


def _strip_command_prefix(text: str) -> str:
  lowered = text.lower()
  for keyword in ["give me", "show me", "stream", "get me", "fetch me"]:
    if lowered.startswith(keyword):
      return text[len(keyword):].strip(" ,.")
  return text


def _extract_after_marker(text: str, marker: str) -> str | None:
  lowered = text.lower()
  idx = lowered.find(marker)
  if idx == -1:
    return None
  start = idx + len(marker)
  return text[start:].strip(" :,.?!")


def extract_polymarket_query(text: str) -> Tuple[str | None, List[str]]:
  """
  Heuristic parser to extract the subject of a Polymarket question.
  Returns (query, categories).
  """
  if not text:
    return None, []

  lowered = text.lower()
  if not any(marker in lowered for marker in POLYMARKET_MARKERS):
    return None, []

  stripped = text.strip()

  quotes = re.findall(r'"([^"]+)"', stripped)
  if quotes:
    candidate = quotes[-1].strip()
    categories = infer_categories(candidate)
    return candidate or None, categories

  for marker in ["polymarket for", "for", "about", "regarding", "concerning"]:
    segment = _extract_after_marker(stripped, marker)
    if segment:
      categories = infer_categories(segment)
      return segment, categories

  core = _strip_command_prefix(stripped)
  categories = infer_categories(core)
  return (core or None), categories


def infer_categories(text: str | None) -> list[str]:
  if not text:
    return []
  lowered = text.lower()
  categories: list[str] = []
  for category, keywords in CATEGORY_KEYWORDS.items():
    if any(keyword in lowered for keyword in keywords):
      categories.append(category)
  return categories


def build_polymarket_spec(
  query: str,
  categories: list[str],
  interval_sec: float = DEFAULT_INTERVAL,
) -> StreamSpec:
  source_cfg = {
    "type": "polymarket",
    "interval_sec": max(3.0, interval_sec),
    "categories": categories,
    "event_ids": [],
    "tag": None,
  }

  spec_dict = {
    "sources": [source_cfg],
    "symbols": [],
    "interval_sec": max(3.0, interval_sec),
  }
  return StreamSpec(**spec_dict)

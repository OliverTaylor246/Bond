"""Helpers to convert parsed NL output into StreamSpec dictionaries."""
from __future__ import annotations

from typing import Any


def normalize_symbol(sym: str) -> str:
  symbol = (sym or "").strip()
  if not symbol:
    return symbol
  return symbol if "/" in symbol else f"{symbol}/USDT"


def build_spec_from_parsed_config(config: dict[str, Any]) -> dict[str, Any]:
  raw_symbols = config.get("symbols", [])
  symbols = [normalize_symbol(sym) for sym in raw_symbols]
  interval_sec = config.get("interval_sec", 5.0)

  spec_dict: dict[str, Any] = {
    "sources": [],
    "symbols": symbols,
    "interval_sec": interval_sec,
  }

  for exchange_cfg in config.get("exchanges", []):
    exchange_name = (exchange_cfg.get("exchange") or "").lower()
    if exchange_name == "binance":
      exchange_name = "binanceus"
    elif not exchange_name:
      exchange_name = "binanceus"

    spec_dict["sources"].append({
      "type": "ccxt",
      "exchange": exchange_name,
      "fields": exchange_cfg.get("fields", []),
      "symbols": symbols,
    })

  for source in config.get("additional_sources", []):
    if source == "twitter":
      spec_dict["sources"].append({"type": "twitter"})
    elif source == "google_trends":
      spec_dict["sources"].append({
        "type": "google_trends",
        "keywords": [s.split("/")[0].lower() for s in symbols],
        "timeframe": "now 1-d",
      })
    elif source == "polymarket":
      spec_dict["sources"].append({
        "type": "polymarket",
        "interval_sec": max(15, int(interval_sec)),
      })
    elif isinstance(source, dict) and source.get("type") == "nitter":
      spec_dict["sources"].append({
        "type": "nitter",
        "username": source.get("username", "elonmusk"),
        "interval_sec": source.get("interval_sec", interval_sec),
      })
    elif isinstance(source, dict) and source.get("type") == "polymarket":
      spec_dict["sources"].append({
        "type": "polymarket",
        "event_ids": source.get("event_ids", []),
        "categories": source.get("categories", []),
        "include_closed": bool(source.get("include_closed", False)),
        "active_only": bool(source.get("active_only", True)),
        "interval_sec": source.get("interval_sec", max(15, int(interval_sec))),
        "limit": source.get("limit", 100),
        "tag": source.get("tag"),
      })
    elif isinstance(source, dict) and source.get("type") in {"onchain", "onchain.grpc"}:
      mint = source.get("mint") or source.get("address") or source.get("contract")
      if not mint:
        continue
      token_symbol = source.get("token_symbol") or source.get("symbol")
      base_symbol = token_symbol or (symbols[0] if symbols else None)
      if base_symbol:
        base = base_symbol.split("/")[0]
      else:
        base = mint[:6].upper() if len(mint) >= 6 else mint.upper()
      derived_symbol = f"{base}/USDC"
      if not spec_dict["symbols"]:
        spec_dict["symbols"] = [derived_symbol]

      spec_dict["sources"].append({
        "type": "jupiter",
        "mint": mint,
        "symbol": derived_symbol,
        "token_symbol": token_symbol,
        "token_name": source.get("token_name"),
        "interval_sec": source.get("interval_sec", interval_sec),
      })

  return spec_dict


__all__ = ["normalize_symbol", "build_spec_from_parsed_config"]

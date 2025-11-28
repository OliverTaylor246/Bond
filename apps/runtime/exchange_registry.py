"""
Exchange connector registry for the new StreamRuntime.

Each connector module should expose `stream_exchange(config: StreamConfig)`.
During step 4 we will populate this registry with real implementations.
"""
from __future__ import annotations

from typing import AsyncIterator, Callable, Protocol

from engine.schemas import StreamConfig

# Import connector modules (skeletons for now)
from apps.runtime.connectors import (
  binance as binance_connector,
  bybit as bybit_connector,
  okx as okx_connector,
  kucoin as kucoin_connector,
  mexc as mexc_connector,
  hyperliquid as hyperliquid_connector,
  lighter as lighter_connector,
)

class ExchangeConnector(Protocol):
  async def stream_exchange(self, config: StreamConfig) -> AsyncIterator[dict]:
    ...


# Placeholder registry; to be populated with real connectors in step 4.
EXCHANGE_REGISTRY: dict[str, ExchangeConnector] = {}

# Populate with skeleton implementations; to be replaced with full wiring.
EXCHANGE_REGISTRY.update({
  "binance": binance_connector,
  "bybit": bybit_connector,
  "okx": okx_connector,
  "kucoin": kucoin_connector,
  "mexc": mexc_connector,
  "hyperliquid": hyperliquid_connector,
  "lighter": lighter_connector,
})


def get_exchange_connector(exchange: str) -> ExchangeConnector:
  connector = EXCHANGE_REGISTRY.get(exchange.lower())
  if not connector:
    raise ValueError(f"No connector registered for exchange '{exchange}'")
  return connector


__all__ = ["ExchangeConnector", "EXCHANGE_REGISTRY", "get_exchange_connector"]

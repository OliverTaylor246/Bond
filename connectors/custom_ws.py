"""
Generic custom WebSocket connector.
Can connect to any WebSocket source and normalize events.
"""
import asyncio
import json
import random
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Optional
import websockets
from engine.schemas import CustomEvent


async def custom_ws_stream(
  url: str,
  source_name: str = "custom",
  parser: Optional[Callable[[dict], dict]] = None
) -> AsyncIterator[dict]:
  """
  Connect to a custom WebSocket and stream normalized events.

  Args:
    url: WebSocket URL to connect to
    source_name: Name to tag events with
    parser: Optional function to transform raw WS messages

  Yields:
    CustomEvent dictionaries
  """
  async with websockets.connect(url) as ws:
    async for message in ws:
      try:
        raw_data = json.loads(message)

        # Apply custom parser if provided
        if parser:
          parsed = parser(raw_data)
        else:
          parsed = raw_data

        evt = CustomEvent(
          ts=datetime.now(tz=timezone.utc),
          source=source_name,
          event_type=parsed.get("type", "unknown"),
          data=parsed,
        )

        yield evt.model_dump()

      except Exception as e:
        print(f"[custom_ws] Error parsing message: {e}")
        continue


async def mock_liquidation_stream(
  symbol: str = "BTC/USDT",
  interval: int = 15
) -> AsyncIterator[dict]:
  """
  Mock liquidation events for testing.

  Args:
    symbol: Symbol to generate liquidations for
    interval: Average seconds between events

  Yields:
    CustomEvent dictionaries with liquidation data
  """
  while True:
    side = random.choice(["long", "short"])
    size = random.uniform(10000, 500000)

    evt = CustomEvent(
      ts=datetime.now(tz=timezone.utc),
      source="liquidations",
      event_type="liquidation",
      data={
        "symbol": symbol,
        "side": side,
        "size_usd": size,
      },
    )

    yield evt.model_dump()

    jitter = random.uniform(0.7, 1.3)
    await asyncio.sleep(interval * jitter)


async def custom_stream(
  source_type: str,
  config: dict[str, Any]
) -> AsyncIterator[dict]:
  """
  Router for different custom stream types.

  Args:
    source_type: Type of custom source ("ws", "mock_liq", etc.)
    config: Configuration dict with source-specific params

  Yields:
    CustomEvent dictionaries
  """
  if source_type == "ws":
    url = config["url"]
    source_name = config.get("name", "custom")
    parser = config.get("parser")
    async for evt in custom_ws_stream(url, source_name, parser):
      yield evt

  elif source_type == "mock_liq":
    symbol = config.get("symbol", "BTC/USDT")
    interval = config.get("interval", 15)
    async for evt in mock_liquidation_stream(symbol, interval):
      yield evt

  else:
    raise ValueError(f"Unknown custom source type: {source_type}")

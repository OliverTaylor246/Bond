"""
Twitter/X streaming connector.
Supports both real API (if credentials provided) and mock mode for testing.
"""
import asyncio
import random
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from engine.schemas import TwitterEvent


MOCK_TWEETS = [
  "BTC to the moon!",
  "Just bought more $BTC",
  "Bitcoin looking bearish today",
  "Crypto winter is here",
  "$BTC breaking resistance",
  "Ethereum flipping Bitcoin soon",
  "HODL gang checking in",
  "This dip is a buying opportunity",
]


async def x_mock_stream(
  symbol: str = "BTC",
  interval: int = 10
) -> AsyncIterator[dict]:
  """
  Mock Twitter stream - generates random tweets for testing.

  Args:
    symbol: Symbol to mention in tweets
    interval: Average seconds between tweets

  Yields:
    TwitterEvent dictionaries
  """
  while True:
    text = random.choice(MOCK_TWEETS)
    sentiment = random.uniform(-0.5, 0.8)  # Slight bullish bias

    evt = TwitterEvent(
      ts=datetime.now(tz=timezone.utc),
      text=text,
      symbol=symbol,
      sentiment=sentiment,
    )

    yield evt.model_dump()

    # Random interval with some jitter
    jitter = random.uniform(0.5, 1.5)
    await asyncio.sleep(interval * jitter)


async def x_filtered_stream(
  bearer_token: str,
  rules: list[str],
  symbol: Optional[str] = None
) -> AsyncIterator[dict]:
  """
  Real Twitter filtered stream (requires API credentials).

  Args:
    bearer_token: Twitter API v2 bearer token
    rules: List of filter rules (e.g., ["BTC", "bitcoin"])
    symbol: Optional symbol to tag tweets with

  Yields:
    TwitterEvent dictionaries

  Note:
    This is a placeholder - implement with tweepy or httpx when ready.
  """
  # TODO: Implement real Twitter API v2 filtered stream
  # For now, fall back to mock
  async for evt in x_mock_stream(symbol or "BTC"):
    yield evt


async def x_stream(
  symbol: str = "BTC",
  bearer_token: Optional[str] = None,
  interval: int = 10
) -> AsyncIterator[dict]:
  """
  Auto-routing function: uses real API if token provided, else mock.

  Args:
    symbol: Symbol to track
    bearer_token: Optional Twitter API bearer token
    interval: Polling interval for mock mode

  Yields:
    TwitterEvent dictionaries
  """
  if bearer_token:
    rules = [symbol, f"${symbol}"]
    async for evt in x_filtered_stream(bearer_token, rules, symbol):
      yield evt
  else:
    async for evt in x_mock_stream(symbol, interval):
      yield evt

"""
Twitter/X streaming connector.
Supports both real API (if credentials provided) and mock mode for testing.
Includes hashtag counting to stay within API limits.

TODO - When Basic Tier ($200/month) is available:
=================================================
Implement smart sentiment tracking system:

1. Tweet Counts endpoint (doesn't use quota):
   - Track hashtag volume every 5 minutes (#bitcoin, #crypto, #ethereum, etc.)
   - Monitor cashtag volume ($BTC, $ETH, etc.)
   - Compare bullish vs bearish hashtag counts for sentiment proxy
   - Detect volume spikes = breaking news events

2. Search endpoint (uses 10,000 tweet/month quota):
   - Only pull actual tweets when volume spike detected
   - Focus on high-value queries: verified influencers only
   - Extract engagement metrics: retweet/like counts, quote tweet ratio
   - Parse entities: hashtags, cashtags, URLs, mentions
   - Get context_annotations for AI-detected topics
   - Track language breakdown (which countries talking)

3. User endpoint (doesn't use quota):
   - Track follower count changes for crypto influencers
   - Detect bot accounts (low follower, high tweet frequency)

Endpoint reference:
- GET /tweets/counts/recent - hashtag volume (FREE)
- GET /tweets/search/recent - tweet search (COSTS quota)
- GET /users/:id - user lookup (FREE)
- GET /tweets/:id - specific tweet (FREE)

Strategy: Continuous count monitoring + smart tweet pulling on spikes
"""
import asyncio
import random
import os
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Optional
import httpx
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


async def x_hashtag_count(
  bearer_token: str,
  hashtags: list[str],
  minutes: int = 10
) -> dict:
  """
  Count tweets with specific hashtags in the last N minutes.
  Uses Twitter API v2 tweet counts endpoint - DOES NOT pull actual posts!

  Args:
    bearer_token: Twitter API v2 bearer token
    hashtags: List of hashtags to search (e.g., ["bitcoin", "crypto"])
    minutes: Time window in minutes (default 10)

  Returns:
    Dict with counts per hashtag and total
  """
  start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
  start_time_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
  end_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

  counts = {}
  total_tweets = 0

  async with httpx.AsyncClient() as client:
    for hashtag in hashtags:
      # Search query for hashtag
      query = f"#{hashtag} -is:retweet"

      # Use tweet counts endpoint instead of search
      url = "https://api.twitter.com/2/tweets/counts/recent"
      params = {
        "query": query,
        "start_time": start_time_str,
        "end_time": end_time_str,
        "granularity": "hour"
      }
      headers = {"Authorization": f"Bearer {bearer_token}"}

      try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

        # Sum up counts from all time buckets
        count = sum(bucket.get("tweet_count", 0) for bucket in data.get("data", []))
        counts[hashtag] = count
        total_tweets += count

        print(f"[x_stream] #{hashtag}: {count} tweets in last {minutes} min", flush=True)

      except httpx.HTTPStatusError as e:
        error_msg = e.response.text if hasattr(e.response, 'text') else str(e)
        print(f"[x_stream] Error #{hashtag} ({e.response.status_code}): {error_msg}", flush=True)
        counts[hashtag] = 0
      except Exception as e:
        print(f"[x_stream] Error: {e}", flush=True)
        counts[hashtag] = 0

  return {
    "total": total_tweets,
    "counts": counts,
    "time_window_minutes": minutes,
    "timestamp": datetime.now(timezone.utc)
  }


async def x_hashtag_stream(
  hashtags: list[str],
  interval: int = 60,
  minutes: int = 10,
  bearer_token: Optional[str] = None
) -> AsyncIterator[dict]:
  """
  Stream hashtag counts at regular intervals.

  Args:
    hashtags: List of hashtags to track
    interval: Seconds between counts (default 60)
    minutes: Time window for counting (default 10)
    bearer_token: Twitter API bearer token (if None, uses env var)

  Yields:
    TwitterEvent dictionaries with hashtag counts
  """
  token = bearer_token or os.getenv("TWITTER_BEARER_TOKEN")

  if not token:
    print("[x_stream] No bearer token - using mock mode", flush=True)
    async for evt in x_mock_stream("crypto", interval):
      yield evt
    return

  while True:
    result = await x_hashtag_count(token, hashtags, minutes)

    # Create event with count data
    evt = TwitterEvent(
      ts=result["timestamp"],
      text=f"Hashtag counts in last {minutes}min: {result['counts']}",
      symbol=hashtags[0] if hashtags else "crypto",
      sentiment=None
    )

    evt_dict = evt.model_dump()
    evt_dict["hashtag_counts"] = result["counts"]
    evt_dict["total_count"] = result["total"]
    evt_dict["time_window_minutes"] = result["time_window_minutes"]

    yield evt_dict

    await asyncio.sleep(interval)


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

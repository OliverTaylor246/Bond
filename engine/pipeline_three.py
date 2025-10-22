"""
Three-source pipeline: merges CCXT, Twitter, and custom data streams.
Aggregates events over time windows and produces unified output.

Performance requirement: latency must stay under 500ms for test loads.
"""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Any
from engine.schemas import AggregatedEvent
from engine.dispatch import RedisDispatcher


class ThreeSourcePipeline:
  """Merges and aggregates events from three data sources."""

  def __init__(
    self,
    stream_id: str,
    interval_sec: int = 5,
    dispatcher: RedisDispatcher | None = None
  ):
    self.stream_id = stream_id
    self.interval_sec = interval_sec
    self.dispatcher = dispatcher

    # Buffers for each source
    self.trade_buffer: list[dict] = []
    self.tweet_buffer: list[dict] = []
    self.custom_buffer: list[dict] = []

    self.last_flush = datetime.now(tz=timezone.utc)

  async def ingest(
    self,
    ccxt_stream: AsyncIterator[dict],
    twitter_stream: AsyncIterator[dict],
    custom_stream: AsyncIterator[dict]
  ) -> None:
    """
    Ingest from all three sources concurrently.

    Args:
      ccxt_stream: CCXT trade events
      twitter_stream: Twitter/X events
      custom_stream: Custom WebSocket events
    """
    # Start all three ingestion tasks
    tasks = [
      asyncio.create_task(self._ingest_trades(ccxt_stream)),
      asyncio.create_task(self._ingest_tweets(twitter_stream)),
      asyncio.create_task(self._ingest_custom(custom_stream)),
      asyncio.create_task(self._periodic_flush()),
    ]

    await asyncio.gather(*tasks)

  async def _ingest_trades(self, stream: AsyncIterator[dict]) -> None:
    """Consume trade events and buffer them."""
    async for event in stream:
      self.trade_buffer.append(event)

  async def _ingest_tweets(self, stream: AsyncIterator[dict]) -> None:
    """Consume tweet events and buffer them."""
    async for event in stream:
      self.tweet_buffer.append(event)

  async def _ingest_custom(self, stream: AsyncIterator[dict]) -> None:
    """Consume custom events and buffer them."""
    async for event in stream:
      self.custom_buffer.append(event)

  async def _periodic_flush(self) -> None:
    """Flush and aggregate buffers every interval."""
    while True:
      await asyncio.sleep(self.interval_sec)

      now = datetime.now(tz=timezone.utc)
      if (now - self.last_flush).total_seconds() >= self.interval_sec:
        await self._flush_and_aggregate()
        self.last_flush = now

  async def _flush_and_aggregate(self) -> None:
    """Aggregate buffered events and publish to Redis."""
    now = datetime.now(tz=timezone.utc)
    
    # Calculate aggregates from trade buffer
    prices = [e["price"] for e in self.trade_buffer if "price" in e]
    volumes = [e["qty"] for e in self.trade_buffer if "qty" in e]

    price_avg = sum(prices) / len(prices) if prices else None
    volume_sum = sum(volumes) if volumes else None

    # Count tweets and custom events
    tweets_count = len(self.tweet_buffer)
    custom_count = len(self.custom_buffer)

    # Aggregate on-chain events (treat custom buffer as onchain for now)
    # In future: separate onchain_buffer from custom_buffer
    onchain_count = len(self.custom_buffer)
    onchain_values = [
      e.get("value", 0) 
      for e in self.custom_buffer 
      if e.get("value") is not None
    ]
    onchain_value_sum = sum(onchain_values) if onchain_values else None

    # Build aggregated event
    agg_event = AggregatedEvent(
      ts=now,
      window_start=self.last_flush,
      window_end=now,
      price_avg=price_avg,
      volume_sum=volume_sum,
      tweets=tweets_count,
      onchain_count=onchain_count,
      onchain_value_sum=onchain_value_sum,
      custom_count=custom_count,
      raw_data={
        "trades": len(self.trade_buffer),
        "sources": list(set(e.get("source") for e in self.trade_buffer)),
        "interval_sec": self.interval_sec,
      },
    )

    # Publish to Redis
    if self.dispatcher:
      await self.dispatcher.publish(self.stream_id, agg_event.model_dump())

    # Clear buffers
    self.trade_buffer.clear()
    self.tweet_buffer.clear()
    self.custom_buffer.clear()


async def run_pipeline(
  stream_id: str,
  ccxt_stream: AsyncIterator[dict],
  twitter_stream: AsyncIterator[dict],
  custom_stream: AsyncIterator[dict],
  interval_sec: int = 5,
  redis_url: str = "redis://localhost:6379"
) -> None:
  """
  Convenience function to run the full pipeline.

  Args:
    stream_id: Unique stream identifier
    ccxt_stream: CCXT events
    twitter_stream: Twitter events
    custom_stream: Custom events
    interval_sec: Aggregation window
    redis_url: Redis connection string
  """
  dispatcher = RedisDispatcher(redis_url)
  await dispatcher.connect()

  pipeline = ThreeSourcePipeline(stream_id, interval_sec, dispatcher)

  try:
    await pipeline.ingest(ccxt_stream, twitter_stream, custom_stream)
  finally:
    await dispatcher.disconnect()

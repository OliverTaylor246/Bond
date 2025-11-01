"""
Three-source pipeline: merges CCXT, Twitter, and custom data streams.
Aggregates events over time windows and produces unified output.

Performance requirement: latency must stay under 500ms for test loads.
"""
import asyncio
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
    try:
      print(f"[pipeline] Starting trade ingestion for {self.stream_id}", flush=True)
      print(f"[pipeline] Stream object: {stream}", flush=True)
      print(f"[pipeline] About to start async for loop...", flush=True)
      async for event in stream:
        print(f"[pipeline] Received trade event: {event.get('source')} price={event.get('price')}", flush=True)
        self.trade_buffer.append(event)
    except Exception as e:
      print(f"[pipeline] ERROR in _ingest_trades: {e}", flush=True)
      import traceback
      traceback.print_exc()
      raise

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
    bids = [e["bid"] for e in self.trade_buffer if e.get("bid") is not None]
    asks = [e["ask"] for e in self.trade_buffer if e.get("ask") is not None]
    highs = [e["high"] for e in self.trade_buffer if e.get("high") is not None]
    lows = [e["low"] for e in self.trade_buffer if e.get("low") is not None]
    opens = [e["open"] for e in self.trade_buffer if e.get("open") is not None]
    closes = [e["close"] for e in self.trade_buffer if e.get("close") is not None]

    price_avg = sum(prices) / len(prices) if prices else None
    volume_sum = sum(volumes) if volumes else None
    bid_avg = sum(bids) / len(bids) if bids else None
    ask_avg = sum(asks) / len(asks) if asks else None
    price_high = max(highs) if highs else None
    price_low = min(lows) if lows else None
    price_open = opens[0] if opens else None
    price_close = closes[-1] if closes else None

    # Build per-exchange + per-symbol data structure
    exchange_data: dict[str, dict[str, Any]] = {}
    for event in self.trade_buffer:
      source = event.get("source")
      symbol = event.get("symbol")
      if not source or not symbol:
        continue

      exchange_entry = exchange_data.setdefault(
        source,
        {"symbols": {}, "latest_symbol": symbol},
      )
      exchange_entry["latest_symbol"] = symbol
      symbol_entry = exchange_entry["symbols"].setdefault(symbol, {})

      # Store latest value for each field at symbol granularity
      if "price" in event:
        symbol_entry["price"] = event["price"]
        exchange_entry["price"] = event["price"]
      if event.get("bid") is not None:
        symbol_entry["bid"] = event["bid"]
        exchange_entry["bid"] = event["bid"]
      if event.get("ask") is not None:
        symbol_entry["ask"] = event["ask"]
        exchange_entry["ask"] = event["ask"]
      if event.get("high") is not None:
        symbol_entry["high"] = event["high"]
        exchange_entry["high"] = event["high"]
      if event.get("low") is not None:
        symbol_entry["low"] = event["low"]
        exchange_entry["low"] = event["low"]
      if event.get("open") is not None:
        symbol_entry["open"] = event["open"]
        exchange_entry["open"] = event["open"]
      if event.get("close") is not None:
        symbol_entry["close"] = event["close"]
        exchange_entry["close"] = event["close"]
      if "qty" in event:
        symbol_entry["volume"] = event["qty"]
        exchange_entry["volume"] = event["qty"]

    # Count tweets and custom events
    tweets_count = len(self.tweet_buffer)
    custom_count = len(self.custom_buffer)

    # Aggregate on-chain events
    onchain_count = len(self.custom_buffer)
    onchain_values = [
      e.get("value", 0)
      for e in self.custom_buffer
      if e.get("value") is not None
    ]
    onchain_value_sum = sum(onchain_values) if onchain_values else None

    # Build aggregated event
    symbols_seen = sorted({
      e.get("symbol") for e in self.trade_buffer if e.get("symbol")
    })

    agg_event = AggregatedEvent(
      ts=now,
      window_start=self.last_flush,
      window_end=now,
      price_avg=price_avg,
      price_high=price_high,
      price_low=price_low,
      price_open=price_open,
      price_close=price_close,
      bid_avg=bid_avg,
      ask_avg=ask_avg,
      volume_sum=volume_sum,
      tweets=tweets_count,
      onchain_count=onchain_count,
      onchain_value_sum=onchain_value_sum,
      custom_count=custom_count,
      raw_data={
        "trades": len(self.trade_buffer),
        "sources": list(set(e.get("source") for e in self.trade_buffer)),
        "interval_sec": self.interval_sec,
        "exchange_data": exchange_data,  # Per-exchange breakdown
        "symbols": symbols_seen,
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

"""
Multi-source aggregation pipeline.
Merges and aggregates events from an arbitrary number of data streams.
"""
import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from engine.dispatch import RedisDispatcher
from engine.schemas import AggregatedEvent


@dataclass
class SourceConfig:
  """
  Source configuration passed into the pipeline.

  Attributes:
    name: Human-readable identifier for the source (e.g., "ccxt:binance")
    type: Logical type of the source (ccxt, twitter, onchain, custom, etc.)
    create_stream: Callable returning a fresh async iterator each time it is invoked
    metadata: Optional additional context (symbols, exchanges, etc.)
    on_failure: Optional callback invoked when the source experiences an error
  """

  name: str
  type: str
  create_stream: Callable[[], AsyncIterator[dict]]
  metadata: dict[str, Any] = field(default_factory=dict)
  on_failure: Optional[Callable[[Exception], None]] = None


class SourceSupervisor:
  """Runs an individual source with retry/backoff and optional failure callbacks."""

  def __init__(
    self,
    config: SourceConfig,
    sink: Callable[[SourceConfig, dict], Awaitable[None]],
    max_backoff: int = 30,
  ):
    self.config = config
    self.sink = sink
    self.max_backoff = max_backoff

  async def run(self) -> None:
    """Continuously stream data with exponential backoff on failure."""
    backoff = 1.0

    while True:
      try:
        stream = self.config.create_stream()
      except Exception as err:
        self._handle_failure(err)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, self.max_backoff)
        continue

      try:
        async for event in stream:
          backoff = 1.0
          await self.sink(self.config, event)
      except asyncio.CancelledError:
        await self._close_stream(stream)
        raise
      except Exception as err:
        await self._close_stream(stream)
        self._handle_failure(err)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, self.max_backoff)
        continue
      else:
        # Stream ended without error - restart after small pause
        await self._close_stream(stream)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, self.max_backoff)

  async def _close_stream(self, stream: AsyncIterator[dict]) -> None:
    """Close async generator if it exposes `aclose`."""
    closer = getattr(stream, "aclose", None)
    if closer:
      try:
        await closer()
      except Exception as err:
        print(f"[pipeline] Error closing stream {self.config.name}: {err}", flush=True)

  def _handle_failure(self, err: Exception) -> None:
    """Invoke optional failure callback and log."""
    print(f"[pipeline] Source {self.config.name} error: {err}", flush=True)
    if self.config.on_failure:
      try:
        self.config.on_failure(err)
      except Exception as callback_err:
        print(
          f"[pipeline] Failure callback error for {self.config.name}: {callback_err}",
          flush=True,
        )


class MultiSourcePipeline:
  """Merge, aggregate, and publish events from an arbitrary number of sources."""

  def __init__(
    self,
    stream_id: str,
    interval_sec: float = 5,
    dispatcher: Optional[RedisDispatcher] = None,
  ):
    self.stream_id = stream_id
    self.interval_sec = interval_sec
    self.dispatcher = dispatcher

    self.trade_buffer: list[dict[str, Any]] = []
    self.social_buffer: list[dict[str, Any]] = []
    self.custom_buffer: list[dict[str, Any]] = []

    self.last_emit_ts: Optional[datetime] = None
    self.latest_event_ts: Optional[datetime] = None
    self._event_lock = asyncio.Lock()

  async def ingest(self, sources: list[SourceConfig]) -> None:
    """Start supervisors for each source and consume events."""
    if not sources:
      raise ValueError("At least one source is required to build a pipeline")

    queue: asyncio.Queue[tuple[SourceConfig, dict]] = asyncio.Queue()

    async def enqueue(config: SourceConfig, event: dict) -> None:
      await queue.put((config, event))

    supervisors = [SourceSupervisor(cfg, enqueue) for cfg in sources]
    supervisor_tasks = [asyncio.create_task(supervisor.run()) for supervisor in supervisors]
    consumer_task = asyncio.create_task(self._consume(queue))

    try:
      await asyncio.gather(*supervisor_tasks, consumer_task)
    except asyncio.CancelledError:
      for task in supervisor_tasks:
        task.cancel()
      consumer_task.cancel()
      await asyncio.gather(*supervisor_tasks, return_exceptions=True)
      with contextlib.suppress(Exception):
        await consumer_task
      raise

  async def _consume(self, queue: asyncio.Queue[tuple[SourceConfig, dict]]) -> None:
    """Consume events pushed into the shared queue by source supervisors."""
    while True:
      config, event = await queue.get()
      await self._handle_event(config, event)

  async def _handle_event(self, config: SourceConfig, event: dict) -> None:
    """Update buffers and decide whether to flush aggregates."""
    event_ts = self._extract_timestamp(event)

    async with self._event_lock:
      if event_ts and (self.latest_event_ts is None or event_ts > self.latest_event_ts):
        self.latest_event_ts = event_ts

      if config.type == "twitter" or config.type == "nitter" or event.get("source") == "twitter" or (event.get("source") and event.get("source").startswith("nitter")):
        self.social_buffer.append(event)
      elif "price" in event:
        self.trade_buffer.append(event)
      else:
        self.custom_buffer.append(event)

      should_flush = (
        event_ts is not None
        and (
          self.last_emit_ts is None
          or (event_ts - self.last_emit_ts).total_seconds() >= self.interval_sec
        )
      )

    if should_flush:
      await self._flush(reference_ts=event_ts)

  async def _flush(self, reference_ts: Optional[datetime] = None) -> None:
    """Flush buffers, build AggregatedEvent, and publish to Redis."""
    async with self._event_lock:
      if not (self.trade_buffer or self.social_buffer or self.custom_buffer):
        return

      latest_ts = reference_ts or self.latest_event_ts or datetime.now(tz=timezone.utc)
      latest_ts = self._ensure_datetime(latest_ts)

      trade_events = self.trade_buffer[:]
      tweet_events = self.social_buffer[:]
      custom_events = self.custom_buffer[:]

      self.trade_buffer.clear()
      self.social_buffer.clear()
      self.custom_buffer.clear()

      self.last_emit_ts = latest_ts
      self.latest_event_ts = latest_ts

    agg_event = self._build_aggregated_event(
      latest_ts,
      trade_events,
      tweet_events,
      custom_events,
    )

    if self.dispatcher:
      await self.dispatcher.publish(self.stream_id, agg_event.model_dump())

  def _build_aggregated_event(
    self,
    latest_ts: datetime,
    trade_events: list[dict],
    tweet_events: list[dict],
    custom_events: list[dict],
  ) -> AggregatedEvent:
    """Calculate aggregates and build AggregatedEvent model."""
    prices = [e["price"] for e in trade_events if "price" in e]
    volume_values = [e["volume"] for e in trade_events if e.get("volume") is not None]
    base_volumes = [e["qty"] for e in trade_events if e.get("qty") is not None]
    base_volume_sum = sum(base_volumes) if base_volumes else None
    bids = [e["bid"] for e in trade_events if e.get("bid") is not None]
    asks = [e["ask"] for e in trade_events if e.get("ask") is not None]
    highs = [e["high"] for e in trade_events if e.get("high") is not None]
    lows = [e["low"] for e in trade_events if e.get("low") is not None]
    opens = [e["open"] for e in trade_events if e.get("open") is not None]
    closes = [e["close"] for e in trade_events if e.get("close") is not None]

    price_avg = sum(prices) / len(prices) if prices else None
    volume_sum = sum(volume_values) if volume_values else None
    bid_avg = sum(bids) / len(bids) if bids else None
    ask_avg = sum(asks) / len(asks) if asks else None
    price_high = max(highs) if highs else None
    price_low = min(lows) if lows else None
    price_open = opens[0] if opens else None
    price_close = closes[-1] if closes else None

    exchange_data: dict[str, dict[str, Any]] = {}
    for event in trade_events:
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
      if event.get("volume") is not None:
        symbol_entry["volume"] = event["volume"]
        exchange_entry["volume"] = event["volume"]
      if event.get("qty") is not None:
        symbol_entry["volume_base"] = event["qty"]
        exchange_entry["volume_base"] = event["qty"]

    symbols_seen = sorted({
      e.get("symbol") for e in trade_events if e.get("symbol")
    })

    onchain_values = [
      e.get("value", 0)
      for e in custom_events
      if e.get("value") is not None
    ]
    onchain_value_sum = sum(onchain_values) if onchain_values else None

    # Include raw tweet data if present
    tweet_data = None
    if tweet_events:
      latest_tweet = tweet_events[-1]  # Get most recent tweet
      if latest_tweet.get('event_type') == 'tweet':
        tweet_data = {
          'text': latest_tweet.get('text'),
          'timestamp_posted': latest_tweet.get('timestamp_posted'),
          'stats': latest_tweet.get('stats'),
          'symbol': latest_tweet.get('symbol'),
          'event_type': 'tweet',
        }

    agg_event = AggregatedEvent(
      ts=latest_ts,
      window_start=latest_ts - timedelta(seconds=self.interval_sec),
      window_end=latest_ts,
      price_avg=price_avg,
      price_high=price_high,
      price_low=price_low,
      price_open=price_open,
      price_close=price_close,
      bid_avg=bid_avg,
      ask_avg=ask_avg,
      volume_sum=volume_sum,
      tweets=len(tweet_events),
      onchain_count=len(custom_events),
      onchain_value_sum=onchain_value_sum,
      custom_count=len(custom_events),
      raw_data={
        "trades": len(trade_events),
        "sources": list({e.get("source") for e in trade_events if e.get("source")}),
        "interval_sec": self.interval_sec,
        "exchange_data": exchange_data,
        "symbols": symbols_seen,
        "volume_base_sum": base_volume_sum,
        "tweet_data": tweet_data,
      },
    )

    return agg_event

  def _extract_timestamp(self, event: dict) -> Optional[datetime]:
    """Attempt to extract a timezone-aware datetime from event payload."""
    ts = event.get("ts")
    if isinstance(ts, datetime):
      return self._ensure_datetime(ts)
    if isinstance(ts, str):
      try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return self._ensure_datetime(parsed)
      except ValueError:
        pass
    return datetime.now(tz=timezone.utc)

  @staticmethod
  def _ensure_datetime(ts: datetime) -> datetime:
    """Guarantee timestamps are timezone-aware (UTC)."""
    if ts.tzinfo is None:
      return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


# Backwards compatibility alias
ThreeSourcePipeline = MultiSourcePipeline


async def run_pipeline(
  stream_id: str,
  sources: list[SourceConfig],
  interval_sec: float = 5,
  redis_url: str = "redis://localhost:6379",
) -> None:
  """
  Convenience function to run the full pipeline with its own dispatcher.
  """
  dispatcher = RedisDispatcher(redis_url)
  await dispatcher.connect()

  pipeline = MultiSourcePipeline(stream_id, interval_sec, dispatcher)

  try:
    await pipeline.ingest(sources)
  finally:
    await dispatcher.disconnect()

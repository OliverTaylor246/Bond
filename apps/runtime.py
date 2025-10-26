"""
Runtime manager - orchestrates live stream pipelines.
Manages lifecycle of running streams (start, stop, health checks).
"""
import asyncio
from typing import Any
from engine.schemas import StreamSpec
from engine.pipeline_three import ThreeSourcePipeline
from engine.dispatch import RedisDispatcher
from connectors.ccxt_polling import ccxt_poll_stream
from connectors.ccxt_ws import ccxt_ws_stream
from connectors.x_stream import x_stream
from connectors.custom_ws import custom_stream
from connectors.onchain_grpc import onchain_stream
from connectors.google_trends_stream import google_trends_stream


class StreamRuntime:
  """Manages running stream pipelines."""

  def __init__(self, redis_url: str = "redis://localhost:6379"):
    self.redis_url = redis_url
    self.active_streams: dict[str, asyncio.Task] = {}
    self.dispatcher = RedisDispatcher(redis_url)

  async def start(self) -> None:
    """Initialize runtime and connect to Redis."""
    await self.dispatcher.connect()

  async def stop(self) -> None:
    """Shutdown all streams and disconnect."""
    # Cancel all running streams
    for stream_id, task in self.active_streams.items():
      task.cancel()

    await asyncio.gather(*self.active_streams.values(), return_exceptions=True)
    self.active_streams.clear()

    await self.dispatcher.disconnect()

  async def launch_stream(self, stream_id: str, spec: StreamSpec) -> None:
    """
    Launch a new stream pipeline.

    Args:
      stream_id: Unique stream identifier
      spec: Stream specification
    """
    if stream_id in self.active_streams:
      raise ValueError(f"Stream {stream_id} already running")

    # Create task for this stream
    task = asyncio.create_task(self._run_stream(stream_id, spec))
    self.active_streams[stream_id] = task

  async def _run_stream(self, stream_id: str, spec: StreamSpec) -> None:
    """
    Internal runner for a single stream.

    Args:
      stream_id: Stream identifier
      spec: Stream specification
    """
    print(f"[runtime] ====== _run_stream started for {stream_id} ======", flush=True)
    # Extract config from spec
    interval = spec.interval_sec
    symbols = spec.symbols
    print(f"[runtime] interval={interval}, symbols={symbols}", flush=True)

    # Determine which sources to activate
    print(f"[runtime] About to extract source_types", flush=True)
    source_types = {s["type"] for s in spec.sources}
    print(f"[runtime] source_types={source_types}", flush=True)
    print(f"[runtime] Creating source streams...", flush=True)

    # Create source streams
    print(f"[runtime] After setup, creating sources", flush=True)
    ccxt_src = None
    twitter_src = None
    custom_src = None
    google_trends_src = None
    print(f"[runtime] Initialized sources to None", flush=True)

    print(f"[runtime] Checking if ccxt in source_types: {'ccxt' in source_types}", flush=True)
    if "ccxt" in source_types:
      print(f"[runtime] YES, ccxt is in source_types", flush=True)
      try:
        symbol = symbols[0] if symbols else "BTC/USDT"
        print(f"[runtime] CCXT source detected, symbol: {symbol}", flush=True)
      except Exception as e:
        print(f"[runtime] ERROR getting symbol: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise

      # Collect all exchanges from CCXT sources
      exchanges_to_use = []
      for source in spec.sources:
        if source.get("type") == "ccxt" and source.get("exchange"):
          exchanges_to_use.append(source["exchange"])

      print(f"[runtime] Exchanges to use: {exchanges_to_use}")

      # If multiple exchanges, merge their streams
      if len(exchanges_to_use) > 1:
        async def merged_ccxt_stream():
          queue = asyncio.Queue()

          async def pump_exchange(ex_name: str):
            async for event in ccxt_ws_stream(symbol, ex_name):
              await queue.put(event)

          tasks = [asyncio.create_task(pump_exchange(ex)) for ex in exchanges_to_use]

          try:
            while True:
              yield await queue.get()
          finally:
            for task in tasks:
              task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        ccxt_src = merged_ccxt_stream()
        print(f"[runtime] Using merged WebSocket stream for {len(exchanges_to_use)} exchanges")
      elif len(exchanges_to_use) == 1:
        # Single exchange WebSocket
        print(f"[runtime] About to call ccxt_ws_stream({symbol}, {exchanges_to_use[0]})", flush=True)
        ccxt_src = ccxt_ws_stream(symbol, exchanges_to_use[0])
        print(f"[runtime] Created WebSocket stream generator: {ccxt_src}", flush=True)
        print(f"[runtime] Using single WebSocket stream: {exchanges_to_use[0]}")
      else:
        # Default to kraken WebSocket
        ccxt_src = ccxt_ws_stream(symbol, "kraken")
        print(f"[runtime] Using default WebSocket stream: kraken")

    if "twitter" in source_types:
      symbol = symbols[0].split("/")[0] if symbols else "BTC"
      twitter_src = x_stream(symbol, interval=interval)

    if "google_trends" in source_types:
      # Find google_trends source config
      trends_cfg = next((s for s in spec.sources if s["type"] == "google_trends"), {})
      keywords = trends_cfg.get("keywords", [])
      timeframe = trends_cfg.get("timeframe", "now 1-H")

      # If no keywords specified, derive from symbols
      if not keywords:
        keywords = [sym.split("/")[0].lower() for sym in symbols] if symbols else ["bitcoin"]

      print(f"[runtime] Google Trends source: keywords={keywords}, timeframe={timeframe}")
      google_trends_src = google_trends_stream(keywords, interval, timeframe)

    # Handle on-chain source (maps to custom_src for pipeline compatibility)
    if "onchain" in source_types or "onchain.grpc" in source_types:
      # Find onchain source config
      onchain_cfg = next(
        (s for s in spec.sources if s["type"] in ["onchain", "onchain.grpc"]),
        {}
      )
      chain = onchain_cfg.get("chain", "sol")
      event_types = onchain_cfg.get("event_types", ["tx", "transfer"])
      custom_src = onchain_stream(chain, event_types, interval)
    elif "google_trends" in source_types:
      # Map Google Trends to custom_src for pipeline compatibility
      custom_src = google_trends_src
    elif "custom" in source_types:
      # Find custom source config
      custom_cfg = next((s for s in spec.sources if s["type"] == "custom"), {})
      mode = custom_cfg.get("mode", "mock_liq")
      custom_src = custom_stream(mode, {"symbol": symbols[0] if symbols else "BTC/USDT", "interval": interval})

    # If no sources specified, use all three (ccxt + twitter + onchain)
    print(f"[runtime] Before fallback check: ccxt_src={ccxt_src}, twitter_src={twitter_src}, custom_src={custom_src}", flush=True)
    print(f"[runtime] any([ccxt_src, twitter_src, custom_src]) = {any([ccxt_src, twitter_src, custom_src])}", flush=True)
    if not any([ccxt_src, twitter_src, custom_src]):
      print(f"[runtime] FALLBACK: No sources set, using defaults with REST polling!", flush=True)
      symbol = symbols[0] if symbols else "BTC/USDT"
      ccxt_src = ccxt_poll_stream(symbol, interval)
      twitter_src = x_stream(symbol.split("/")[0], interval=interval)
      custom_src = onchain_stream("sol", ["tx", "transfer"], interval)

    # Create empty generators for missing sources
    async def empty_stream():
      while True:
        await asyncio.sleep(999999)
        yield  # Never actually yields

    # Ensure all sources have values (use empty stream if None)
    ccxt_src = ccxt_src or empty_stream()
    twitter_src = twitter_src or empty_stream()
    custom_src = custom_src or empty_stream()

    # Create pipeline
    pipeline = ThreeSourcePipeline(stream_id, interval, self.dispatcher)

    # Run pipeline (will block until cancelled)
    try:
      print(f"[runtime] Starting pipeline ingest for {stream_id}")
      print(f"[runtime] Passing to pipeline: ccxt_src={ccxt_src}, twitter_src={twitter_src}, custom_src={custom_src}", flush=True)
      await pipeline.ingest(ccxt_src, twitter_src, custom_src)
    except asyncio.CancelledError:
      # Clean shutdown
      print(f"[runtime] Stream {stream_id} cancelled")
      pass
    except Exception as e:
      print(f"[runtime] Stream {stream_id} error: {e}")
      import traceback
      traceback.print_exc()
    finally:
      # Cleanup
      if stream_id in self.active_streams:
        del self.active_streams[stream_id]

  async def stop_stream(self, stream_id: str) -> None:
    """
    Stop a running stream.

    Args:
      stream_id: Stream identifier
    """
    if stream_id not in self.active_streams:
      raise ValueError(f"Stream {stream_id} not running")

    task = self.active_streams[stream_id]
    task.cancel()

    try:
      await task
    except asyncio.CancelledError:
      pass

    del self.active_streams[stream_id]

  def is_running(self, stream_id: str) -> bool:
    """
    Check if a stream is currently running.

    Args:
      stream_id: Stream identifier

    Returns:
      True if stream is active
    """
    return stream_id in self.active_streams

  def list_streams(self) -> list[str]:
    """
    Get list of active stream IDs.

    Returns:
      List of stream IDs
    """
    return list(self.active_streams.keys())

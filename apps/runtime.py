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
from connectors.x_stream import x_stream
from connectors.custom_ws import custom_stream
from connectors.onchain_grpc import onchain_stream


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
    # Extract config from spec
    interval = spec.interval_sec
    symbols = spec.symbols

    # Determine which sources to activate
    source_types = {s["type"] for s in spec.sources}

    # Create source streams
    ccxt_src = None
    twitter_src = None
    custom_src = None

    if "ccxt" in source_types:
      symbol = symbols[0] if symbols else "BTC/USDT"
      ccxt_src = ccxt_poll_stream(symbol, interval)

    if "twitter" in source_types:
      symbol = symbols[0].split("/")[0] if symbols else "BTC"
      twitter_src = x_stream(symbol, interval=interval)

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
    elif "custom" in source_types:
      # Find custom source config
      custom_cfg = next((s for s in spec.sources if s["type"] == "custom"), {})
      mode = custom_cfg.get("mode", "mock_liq")
      custom_src = custom_stream(mode, {"symbol": symbols[0] if symbols else "BTC/USDT", "interval": interval})

    # If no sources specified, use all three (ccxt + twitter + onchain)
    if not any([ccxt_src, twitter_src, custom_src]):
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
      await pipeline.ingest(ccxt_src, twitter_src, custom_src)
    except asyncio.CancelledError:
      # Clean shutdown
      pass
    except Exception as e:
      print(f"[runtime] Stream {stream_id} error: {e}")
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

"""
Runtime manager - orchestrates live stream pipelines.
Manages lifecycle of running streams (start, stop, health checks).
"""
import asyncio
from typing import Any
from engine.schemas import StreamSpec
from engine.pipeline_three import MultiSourcePipeline, SourceConfig
from engine.dispatch import RedisDispatcher
from connectors.ccxt_ws import (
  ccxt_ws_exchange_stream,
  ccxt_ws_candle_stream,
  interval_to_timeframe,
)
from connectors.x_stream import x_stream
from connectors.custom_ws import custom_stream
from connectors.onchain_grpc import onchain_stream
from connectors.google_trends_stream import google_trends_stream
from connectors.nitter_playwright import nitter_playwright_stream

BASE_EXCHANGE_PREFERENCE = ["binance", "binanceus", "kraken", "kucoin"]


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
    interval = float(spec.interval_sec or 5.0)
    if interval <= 0:
      interval = 1.0

    symbols = list(spec.symbols or ["BTC/USDT"])
    if not symbols:
      symbols = ["BTC/USDT"]
    default_symbol = symbols[0]

    print(f"[runtime] interval={interval}, symbols={symbols}", flush=True)

    pipeline_sources: list[SourceConfig] = []
    source_counter = {"value": 0}

    def next_name(prefix: str) -> str:
      idx = source_counter["value"]
      source_counter["value"] += 1
      return f"{prefix}:{idx}"

    def normalize_symbols(raw: Any) -> list[str]:
      if not raw:
        return list(symbols)
      if isinstance(raw, str):
        return [raw]
      return [sym for sym in raw if isinstance(sym, str) and sym]

    def extract_exchange_list(source_cfg: dict) -> list[str]:
      exchanges: list[str] = []
      ex_value = source_cfg.get("exchange")
      if isinstance(ex_value, str):
        exchanges.append(ex_value)

      ex_list = source_cfg.get("exchanges")
      if isinstance(ex_list, list):
        for item in ex_list:
          if isinstance(item, str):
            exchanges.append(item)
          elif isinstance(item, dict) and isinstance(item.get("exchange"), str):
            exchanges.append(item["exchange"])

      return exchanges

    def add_ccxt_source(source_cfg: dict) -> None:
      source_symbols = normalize_symbols(source_cfg.get("symbols"))
      if not source_symbols:
        source_symbols = [default_symbol]

      candidates = self._build_exchange_preference(extract_exchange_list(source_cfg))
      state = {"idx": 0}

      def current_exchange() -> str:
        return candidates[state["idx"] % len(candidates)]

      market_type = source_cfg.get("market_type") or source_cfg.get("market")
      if isinstance(market_type, str):
        market_type = market_type.lower()
      else:
        market_type = "spot"

      timeframe = interval_to_timeframe(interval)
      use_candles = bool(timeframe) and interval >= 60

      metadata = {
        "symbols": list(source_symbols),
        "fields": source_cfg.get("fields", []),
        "exchanges": list(candidates),
        "mode": "ohlcv" if use_candles else "trades",
        "market_type": market_type,
      }
      if timeframe:
        metadata["timeframe"] = timeframe

      name = next_name("ccxt")

      def create_stream():
        exchange = current_exchange()
        metadata["active_exchange"] = exchange
        if use_candles:
          print(
            f"[runtime] Starting CCXT candle stream on {exchange} "
            f"for symbols={source_symbols} timeframe={timeframe}",
            flush=True,
          )
          return ccxt_ws_candle_stream(
            exchange,
            list(source_symbols),
            interval,
            market_type=market_type,
          )

        print(
          f"[runtime] Starting CCXT trade stream on {exchange} for symbols={source_symbols}",
          flush=True,
        )
        return ccxt_ws_exchange_stream(
          exchange,
          list(source_symbols),
          market_type=market_type,
        )

      def on_failure(err: Exception) -> None:
        previous = current_exchange()
        state["idx"] = (state["idx"] + 1) % len(candidates)
        new_exchange = current_exchange()
        metadata["active_exchange"] = new_exchange
        if new_exchange != previous:
          print(
            f"[runtime] CCXT failover {previous} -> {new_exchange}: {err}",
            flush=True,
          )

      pipeline_sources.append(
        SourceConfig(
          name=name,
          type="ccxt",
          create_stream=create_stream,
          metadata=metadata,
          on_failure=on_failure if len(candidates) > 1 else None,
        )
      )

    def add_twitter_source(source_cfg: dict) -> None:
      source_symbols = normalize_symbols(source_cfg.get("symbols"))
      symbol = source_symbols[0] if source_symbols else default_symbol
      base_symbol = symbol.split("/")[0]
      interval_override = int(source_cfg.get("interval_sec") or interval)
      interval_override = max(1, interval_override)
      bearer = source_cfg.get("bearer_token")

      def create_stream():
        print(
          f"[runtime] Starting Twitter stream for {base_symbol} (interval={interval_override}s)",
          flush=True,
        )
        return x_stream(base_symbol, bearer_token=bearer, interval=interval_override)

      pipeline_sources.append(
        SourceConfig(
          name=next_name("twitter"),
          type="twitter",
          create_stream=create_stream,
          metadata={
            "symbol": base_symbol,
            "interval_sec": interval_override,
          },
        )
      )

    def add_google_trends_source(source_cfg: dict) -> None:
      keywords = source_cfg.get("keywords")
      if not keywords:
        keywords = [sym.split("/")[0].lower() for sym in symbols]
      timeframe = source_cfg.get("timeframe", "now 1-H")
      trends_interval = int(source_cfg.get("interval_sec") or max(interval, 60))
      trends_interval = max(30, trends_interval)

      def create_stream():
        print(
          "[runtime] Starting Google Trends stream "
          f"keywords={keywords} timeframe={timeframe} interval={trends_interval}",
          flush=True,
        )
        return google_trends_stream(list(keywords), trends_interval, timeframe)

      pipeline_sources.append(
        SourceConfig(
          name=next_name("google_trends"),
          type="google_trends",
          create_stream=create_stream,
          metadata={
            "keywords": list(keywords),
            "timeframe": timeframe,
            "interval_sec": trends_interval,
          },
        )
      )

    def add_onchain_source(source_cfg: dict) -> None:
      chain = source_cfg.get("chain", "sol")
      event_types = source_cfg.get("event_types", ["tx", "transfer"])
      interval_override = int(source_cfg.get("interval_sec") or interval)
      interval_override = max(1, interval_override)

      def create_stream():
        print(
          f"[runtime] Starting on-chain stream chain={chain} events={event_types}",
          flush=True,
        )
        return onchain_stream(chain, list(event_types), interval_override)

      pipeline_sources.append(
        SourceConfig(
          name=next_name("onchain"),
          type="onchain",
          create_stream=create_stream,
          metadata={
            "chain": chain,
            "event_types": list(event_types),
            "interval_sec": interval_override,
          },
        )
      )

    def add_custom_source(source_cfg: dict) -> None:
      mode = source_cfg.get("mode", "mock_liq")
      base_config = dict(source_cfg.get("config") or {})
      base_config.setdefault("symbol", default_symbol)
      base_config.setdefault("interval", max(1, int(interval)))

      def create_stream():
        print(f"[runtime] Starting custom stream mode={mode}", flush=True)
        return custom_stream(mode, dict(base_config))

      pipeline_sources.append(
        SourceConfig(
          name=next_name("custom"),
          type="custom",
          create_stream=create_stream,
          metadata={
            "mode": mode,
            **{k: v for k, v in base_config.items() if k in {"symbol", "interval"}},
          },
        )
      )

    def add_nitter_source(source_cfg: dict) -> None:
      username = source_cfg.get("username", "elonmusk")
      interval_override = int(source_cfg.get("interval_sec") or interval)
      interval_override = max(1, interval_override)

      def create_stream():
        print(
          f"[runtime] Starting Nitter stream for @{username} (interval={interval_override}s)",
          flush=True,
        )
        return nitter_playwright_stream(username=username, interval=interval_override)

      pipeline_sources.append(
        SourceConfig(
          name=next_name("nitter"),
          type="nitter",
          create_stream=create_stream,
          metadata={
            "username": username,
            "interval_sec": interval_override,
          },
        )
      )

    handlers = {
      "ccxt": add_ccxt_source,
      "twitter": add_twitter_source,
      "google_trends": add_google_trends_source,
      "onchain": add_onchain_source,
      "onchain.grpc": add_onchain_source,
      "custom": add_custom_source,
      "nitter": add_nitter_source,
    }

    for source in spec.sources:
      source_type = source.get("type")
      handler = handlers.get(source_type)
      if handler:
        handler(source)
      else:
        print(f"[runtime] Unsupported source type '{source_type}' - skipping", flush=True)

    if not pipeline_sources:
      print("[runtime] FALLBACK: No sources provided, using default bundle", flush=True)
      add_ccxt_source({"symbols": symbols})
      add_twitter_source({})
      add_onchain_source({})

    pipeline = MultiSourcePipeline(stream_id, interval, self.dispatcher)

    try:
      print(
        f"[runtime] Starting pipeline ingest for {stream_id} with "
        f"sources={[cfg.name for cfg in pipeline_sources]}",
        flush=True,
      )
      await pipeline.ingest(pipeline_sources)
    except asyncio.CancelledError:
      print(f"[runtime] Stream {stream_id} cancelled")
      raise
    except Exception as e:
      print(f"[runtime] Stream {stream_id} error: {e}")
      import traceback
      traceback.print_exc()
    finally:
      # Cleanup
      if stream_id in self.active_streams:
        del self.active_streams[stream_id]

  def _build_exchange_preference(self, requested: list[str] | None) -> list[str]:
    """
    Build ordered list of exchanges with fallbacks.

    Preference order:
      1. User-requested exchanges (in provided order)
      2. Binance
      3. BinanceUS
      4. Kraken
      5. Kucoin (final catch-all)
    """
    preference: list[str] = []
    if requested:
      for ex in requested:
        if isinstance(ex, str):
          ex_lower = ex.lower()
          if ex_lower not in preference:
            preference.append(ex_lower)

    for ex in BASE_EXCHANGE_PREFERENCE:
      if ex not in preference:
        preference.append(ex)

    if "kraken" not in preference:
      preference.append("kraken")

    return preference

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

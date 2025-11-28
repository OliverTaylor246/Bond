"""
Runtime manager - orchestrates live stream pipelines.
Manages lifecycle of running streams (start, stop, health checks).
"""
import asyncio
import contextlib
import json
import os
import time
from typing import Any
from engine.schemas import StreamSpec
from engine.pipeline_three import MultiSourcePipeline, SourceConfig
from engine.dispatch import RedisDispatcher
from apps.runtime.heartbeat import (
  start_heartbeat,
  stop_heartbeat,
  stop_all_heartbeats,
)
from apps.runtime.reaper import start_reaper, stop_reaper
from connectors.ccxt_ws import (
  ccxt_ws_exchange_stream,
  ccxt_ws_candle_stream,
  interval_to_timeframe,
)
from connectors.x_stream import x_stream
from connectors.custom_ws import custom_stream
from connectors.google_trends_stream import google_trends_stream
from connectors.nitter_playwright import nitter_playwright_stream
from connectors.polymarket_stream import polymarket_events_stream
from connectors.jupiter_price import jupiter_price_stream

BASE_EXCHANGE_PREFERENCE = ["binanceus", "binance"]


class StreamRuntime:
  """Manages running stream pipelines."""

  def __init__(self, redis_url: str = "redis://localhost:6379"):
    self.redis_url = redis_url
    self.active_streams: dict[str, asyncio.Task] = {}
    self.dispatcher = RedisDispatcher(redis_url)
    self.stream_specs: dict[str, StreamSpec] = {}
    self.cleanup_task: asyncio.Task | None = None
    self.stream_ttls: dict[str, int] = {}
    self.default_ttl = int(os.getenv("STREAM_DEFAULT_TTL_SEC", "600"))
    self.heartbeat_interval = int(os.getenv("STREAM_HEARTBEAT_INTERVAL_SEC", "30"))
    self.stale_threshold = int(os.getenv("STREAM_STALE_THRESHOLD_SEC", "120"))
    self.cleanup_interval = int(os.getenv("STREAM_CLEANUP_INTERVAL_SEC", "30"))

  async def start(self) -> None:
    """Initialize runtime and connect to Redis."""
    await self.dispatcher.connect()
    self.cleanup_task = asyncio.create_task(self._cleanup_worker())
    await start_reaper(self.shutdown_stream)

  async def stop(self) -> None:
    """Shutdown all streams and disconnect."""
    # Cancel all running streams
    for stream_id, task in self.active_streams.items():
      task.cancel()

    await asyncio.gather(*self.active_streams.values(), return_exceptions=True)
    self.active_streams.clear()

    # Cancel heartbeat tasks
    await stop_all_heartbeats()
    self.stream_ttls.clear()

    if self.cleanup_task:
      self.cleanup_task.cancel()
      with contextlib.suppress(asyncio.CancelledError):
        await self.cleanup_task
      self.cleanup_task = None

    await stop_reaper()
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

    # Track spec for later inspection/restart
    self.stream_specs[stream_id] = spec

    ttl = await self._register_stream_metadata(stream_id, spec)

    # Create task for this stream
    task = asyncio.create_task(self._run_stream(stream_id, spec))
    self.active_streams[stream_id] = task

    await start_heartbeat(stream_id)
    self.stream_ttls[stream_id] = ttl

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

      resolved_map_raw = source_cfg.get("resolved_symbols")
      resolved_map: dict[str, list[str]] = {}
      if isinstance(resolved_map_raw, dict):
        for ex_name, sym_list in resolved_map_raw.items():
          if not sym_list:
            continue
          normalized = normalize_symbols(sym_list)
          if normalized:
            resolved_map[ex_name.lower()] = normalized

      candidates = self._build_exchange_preference(extract_exchange_list(source_cfg))
      if resolved_map:
        filtered = [ex for ex in candidates if ex in resolved_map]
        if filtered:
          candidates = filtered
      state = {"idx": 0}

      def current_exchange() -> str:
        return candidates[state["idx"] % len(candidates)]

      def resolve_symbols_for_exchange(exchange: str) -> list[str]:
        if resolved_map and resolved_map.get(exchange):
          return list(resolved_map[exchange])
        return list(source_symbols)

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
      if resolved_map:
        metadata["resolved_symbols"] = resolved_map

      name = next_name("ccxt")

      def create_stream():
        exchange = current_exchange()
        exchange_symbols = resolve_symbols_for_exchange(exchange)
        metadata["active_exchange"] = exchange
        metadata["symbols"] = list(exchange_symbols)
        if use_candles:
          print(
            f"[runtime] Starting CCXT candle stream on {exchange} "
            f"for symbols={exchange_symbols} timeframe={timeframe}",
            flush=True,
          )
          return ccxt_ws_candle_stream(
            exchange,
            list(exchange_symbols),
            interval,
            market_type=market_type,
          )

        print(
          f"[runtime] Starting CCXT trade stream on {exchange} for symbols={exchange_symbols}",
          flush=True,
        )
        return ccxt_ws_exchange_stream(
          exchange,
          list(exchange_symbols),
          market_type=market_type,
        )

      def on_failure(err: Exception) -> None:
        previous = current_exchange()
        err_str = str(err).lower()
        if "451" in err_str or "restricted" in err_str:
          state["idx"] = min(state["idx"] + 1, len(candidates) - 1)
        else:
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

    def add_polymarket_source(source_cfg: dict) -> None:
      def as_str_list(value: Any) -> list[str]:
        if isinstance(value, str) and value.strip():
          return [value.strip()]
        if isinstance(value, list):
          return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]
        return []

      event_ids = as_str_list(source_cfg.get("event_ids"))
      categories = as_str_list(source_cfg.get("categories"))
      tag = source_cfg.get("tag")
      if isinstance(tag, str):
        tag = tag.strip() or None
      include_closed = bool(source_cfg.get("include_closed", False))
      active_only = bool(source_cfg.get("active_only", True))
      interval_override = int(source_cfg.get("interval_sec") or max(interval, 30))
      interval_override = max(15, interval_override)
      limit = int(source_cfg.get("limit") or 100)

      def create_stream():
        print(
          "[runtime] Starting Polymarket stream "
          f"(events={event_ids or 'all'}, categories={categories or 'all'})",
          flush=True,
        )
        return polymarket_events_stream(
          event_ids=list(event_ids) or None,
          categories=list(categories) or None,
          tags=[tag] if tag else None,
          include_closed=include_closed,
          active_only=active_only,
          interval=interval_override,
          limit=limit,
        )

      pipeline_sources.append(
        SourceConfig(
          name=next_name("polymarket"),
          type="polymarket",
          create_stream=create_stream,
          metadata={
            "event_ids": list(event_ids),
            "categories": list(categories),
            "tag": tag,
            "include_closed": include_closed,
            "active_only": active_only,
            "interval_sec": interval_override,
            "limit": limit,
          },
        )
      )

    def add_jupiter_source(source_cfg: dict) -> None:
      mint = (
        source_cfg.get("mint")
        or source_cfg.get("contract")
        or source_cfg.get("id")
      )
      if not isinstance(mint, str) or not mint.strip():
        print("[runtime] Jupiter source missing mint/id - skipping", flush=True)
        return

      raw_symbol = (
        source_cfg.get("symbol")
        or source_cfg.get("token_symbol")
        or source_cfg.get("token")
        or default_symbol
      )
      normalized_symbol = (raw_symbol or "").replace("$", "").upper()
      if not normalized_symbol:
        normalized_symbol = default_symbol
      if "/" not in normalized_symbol:
        normalized_symbol = f"{normalized_symbol}/USDC"

      interval_override = float(source_cfg.get("interval_sec") or interval)
      interval_override = max(1.0, interval_override)
      token_name = source_cfg.get("token_name") or source_cfg.get("name")
      mint_clean = mint.strip()

      def create_stream():
        print(
          f"[runtime] Starting Jupiter stream mint={mint_clean} symbol={normalized_symbol} "
          f"(interval={interval_override}s)",
          flush=True,
        )
        return jupiter_price_stream(
          mint_clean,
          symbol=normalized_symbol,
          interval_sec=interval_override,
        )

      metadata = {
        "mint": mint_clean,
        "symbol": normalized_symbol,
        "token_name": token_name,
        "interval_sec": interval_override,
      }
      if source_cfg.get("decimals") is not None:
        metadata["decimals"] = source_cfg.get("decimals")

      pipeline_sources.append(
        SourceConfig(
          name=next_name("jupiter"),
          type="jupiter",
          create_stream=create_stream,
          metadata=metadata,
        )
      )

    handlers = {
      "ccxt": add_ccxt_source,
      "twitter": add_twitter_source,
      "google_trends": add_google_trends_source,
      "custom": add_custom_source,
      "nitter": add_nitter_source,
      "polymarket": add_polymarket_source,
      "jupiter": add_jupiter_source,
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

    if not preference:
      preference.extend(BASE_EXCHANGE_PREFERENCE)
    else:
      for ex in BASE_EXCHANGE_PREFERENCE:
        if ex not in preference:
          preference.append(ex)

    return preference

  async def stop_stream(self, stream_id: str, *, preserve_spec: bool = False) -> None:
    """
    Stop a running stream.

    Args:
      stream_id: Stream identifier
    """
    task = self.active_streams.get(stream_id)
    if not task:
      raise ValueError(f"Stream {stream_id} not running")

    task.cancel()

    try:
      await task
    except asyncio.CancelledError:
      pass

    self.active_streams.pop(stream_id, None)
    await self._deregister_stream(stream_id)
    if not preserve_spec:
      self.stream_specs.pop(stream_id, None)

  async def restart_stream(self, stream_id: str) -> None:
    """Restart a running stream with its last known spec."""
    if stream_id not in self.active_streams:
      raise ValueError(f"Stream {stream_id} not running")

    spec = self.stream_specs.get(stream_id)
    if not spec:
      raise ValueError(f"Spec for stream {stream_id} not found")

    await self.stop_stream(stream_id, preserve_spec=True)
    await self.launch_stream(stream_id, spec)

  async def update_stream(self, stream_id: str, spec: StreamSpec) -> None:
    """Replace a stream's spec and relaunch it."""
    if stream_id not in self.active_streams:
      raise ValueError(f"Stream {stream_id} not running")

    await self.stop_stream(stream_id)
    await self.launch_stream(stream_id, spec)

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

  def get_stream_spec(self, stream_id: str) -> StreamSpec:
    """Return the stored StreamSpec for a running stream."""
    spec = self.stream_specs.get(stream_id)
    if not spec:
      raise ValueError(f"Spec for stream {stream_id} not found")
    return spec

  async def _register_stream_metadata(self, stream_id: str, spec: StreamSpec) -> int:
    client = self.dispatcher.client
    if not client:
      return self.default_ttl

    ttl = int(spec.ttl_sec or self.default_ttl)
    ttl = max(ttl, self.heartbeat_interval * 2)
    config_key = f"stream:{stream_id}:config"
    heartbeat_key = f"stream:{stream_id}:last_heartbeat"
    payload = json.dumps(spec.model_dump())
    now = time.time()

    await client.sadd("active_streams", stream_id)
    await client.set(config_key, payload, ex=ttl)
    await client.set(heartbeat_key, str(now), ex=ttl)

    return ttl

  async def shutdown_stream(self, stream_id: str) -> None:
    if stream_id not in self.active_streams:
      print(f"[runtime] Reaper requested shutdown for inactive {stream_id}", flush=True)
      return
    print(f"[runtime] Reaper shutting down {stream_id}", flush=True)
    await self.stop_stream(stream_id)

  async def _deregister_stream(self, stream_id: str) -> None:
    client = self.dispatcher.client
    await stop_heartbeat(stream_id)
    self.stream_ttls.pop(stream_id, None)

    if not client:
      return

    await client.srem("active_streams", stream_id)
    await self._delete_stream_keys(stream_id, client)

  async def _delete_stream_keys(self, stream_id: str, client) -> None:
    await client.delete(
      f"stream:{stream_id}:config",
      f"stream:{stream_id}:subscribers",
      f"stream:{stream_id}:last_heartbeat",
      f"stream:{stream_id}:token",
    )
    await client.delete(f"stream:{stream_id}")

  async def _cleanup_worker(self) -> None:
    try:
      while True:
        await asyncio.sleep(self.cleanup_interval)
        await self._cleanup_stale_streams()
    except asyncio.CancelledError:
      pass

  async def _cleanup_stale_streams(self) -> None:
    client = self.dispatcher.client
    if not client:
      return
    stream_ids = await client.smembers("active_streams")
    if not stream_ids:
      return

    now = time.time()
    for stream_id in stream_ids:
      heartbeat_key = f"stream:{stream_id}:last_heartbeat"
      raw_last = await client.get(heartbeat_key)
      if not raw_last:
        await client.srem("active_streams", stream_id)
        await self._delete_stream_keys(stream_id, client)
        continue

      try:
        last = float(raw_last)
      except ValueError:
        last = 0.0

      if now - last > self.stale_threshold:
        print(f"[runtime] Stream {stream_id} stale > {self.stale_threshold}s, cleaning up", flush=True)
        if stream_id in self.active_streams:
          with contextlib.suppress(Exception):
            await self.stop_stream(stream_id)
        else:
          await client.srem("active_streams", stream_id)
          await self._delete_stream_keys(stream_id, client)


__all__ = ["StreamRuntime"]

# -------------------------------
# New native runtime (StreamConfig + exchange connectors)
# -------------------------------

import asyncio as _asyncio
import json as _json
import time as _time
from contextlib import suppress as _suppress
from typing import AsyncIterator as _AsyncIterator

from engine.schemas import StreamConfig as _StreamConfig
from engine.dispatch import RedisDispatcher as _RedisDispatcher
from engine.pipeline_native import run_native_pipeline as _run_native_pipeline
from apps.runtime.exchange_registry import get_exchange_connector as _get_exchange_connector


class NativeStreamRuntime:
  """
  StreamRuntime aligned to the canonical StreamConfig:
    - validates config against venue capabilities
    - selects the registered exchange connector
    - runs native pipeline (batch + Redis)
    - maintains heartbeat/TTL metadata in Redis
  """

  def __init__(self, redis_url: str = "redis://localhost:6379"):
    self.redis_url = redis_url
    self.dispatcher = _RedisDispatcher(redis_url)
    self.active_streams: dict[str, _asyncio.Task] = {}
    self.heartbeat_tasks: dict[str, _asyncio.Task] = {}
    self.default_ttl = 600

  async def start(self) -> None:
    await self.dispatcher.connect()

  async def stop(self) -> None:
    for task in self.active_streams.values():
      task.cancel()
    await _asyncio.gather(*self.active_streams.values(), return_exceptions=True)
    self.active_streams.clear()
    for hb in self.heartbeat_tasks.values():
      hb.cancel()
    await _asyncio.gather(*self.heartbeat_tasks.values(), return_exceptions=True)
    self.heartbeat_tasks.clear()
    await self.dispatcher.disconnect()

  async def launch_stream(self, config: _StreamConfig) -> None:
    config = config.validate_against_capabilities()
    if config.stream_id in self.active_streams:
      raise ValueError(f"Stream {config.stream_id} already running")

    connector = _get_exchange_connector(config.exchange)
    await self._register_metadata(config)

    task = _asyncio.create_task(self._run_stream(config, connector.stream_exchange))
    self.active_streams[config.stream_id] = task

    hb_task = _asyncio.create_task(self._heartbeat_loop(config))
    self.heartbeat_tasks[config.stream_id] = hb_task

  async def stop_stream(self, stream_id: str) -> None:
    task = self.active_streams.pop(stream_id, None)
    if task:
      task.cancel()
      with _suppress(_asyncio.CancelledError):
        await task
    hb = self.heartbeat_tasks.pop(stream_id, None)
    if hb:
      hb.cancel()
    await self._deregister_metadata(stream_id)

  async def _run_stream(self, config: _StreamConfig, stream_fn) -> None:
    try:
      events: _AsyncIterator[dict] = stream_fn(config)
      await _run_native_pipeline(
        config.stream_id,
        events,
        self.dispatcher,
        config.flush_interval_ms,
      )
    except _asyncio.CancelledError:
      raise
    except Exception as exc:
      print(f"[runtime-v2] Stream {config.stream_id} error: {exc}", flush=True)
      raise
    finally:
      self.active_streams.pop(config.stream_id, None)

  async def _register_metadata(self, config: _StreamConfig) -> None:
    client = self.dispatcher.client
    if not client:
      return
    ttl = max(config.ttl, int(config.heartbeat_ms / 1000) * 2, self.default_ttl)
    config_key = f"stream:{config.stream_id}:config"
    heartbeat_key = f"stream:{config.stream_id}:last_heartbeat"
    plan = {
      "type": "exchange_stream",
      "exchange": config.exchange,
      "channels": _json.loads(config.channels.model_dump_json()),
      "symbols": config.symbols,
      "pipeline_params": {
        "flush_interval_ms": config.flush_interval_ms,
        "heartbeat_ms": config.heartbeat_ms,
      },
    }
    await client.sadd("active_streams", config.stream_id)
    await client.set(config_key, config.model_dump_json(), ex=ttl)
    await client.set(heartbeat_key, str(_time.time()), ex=ttl)
    await client.set(f"stream:{config.stream_id}:plan", _json.dumps(plan), ex=ttl)

  async def _deregister_metadata(self, stream_id: str) -> None:
    client = self.dispatcher.client
    if not client:
      return
    await client.srem("active_streams", stream_id)
    await client.delete(
      f"stream:{stream_id}",
      f"stream:{stream_id}:config",
      f"stream:{stream_id}:plan",
      f"stream:{stream_id}:last_heartbeat",
      f"stream:{stream_id}:token",
    )

  async def _heartbeat_loop(self, config: _StreamConfig) -> None:
    client = self.dispatcher.client
    if not client:
      return
    key = f"stream:{config.stream_id}:last_heartbeat"
    ttl = max(config.ttl, int(config.heartbeat_ms / 1000) * 2, self.default_ttl)
    try:
      while True:
        now = _time.time()
        await client.set(key, str(now), ex=ttl)
        await _asyncio.sleep(max(config.heartbeat_ms, 500) / 1000.0)
    except _asyncio.CancelledError:
      pass


__all__.append("NativeStreamRuntime")

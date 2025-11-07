"""
CCXT Pro WebSocket connector - real-time market data streaming.
Uses ccxt.pro for low-latency WebSocket connections to exchanges.

Latency: ~50-200ms (vs 1-6 seconds for REST polling)
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
import ccxt
import ccxt.pro as ccxtpro
from engine.schemas import TradeEvent


EXCHANGES = ["binanceus", "binance"]

INTERVAL_TO_TIMEFRAME = {
  1: "1s",
  5: "5s",
  10: "10s",
  15: "15s",
  30: "30s",
  60: "1m",
  120: "2m",
  180: "3m",
  300: "5m",
  600: "10m",
  900: "15m",
  1200: "20m",
  1800: "30m",
  2700: "45m",
  3600: "1h",
  7200: "2h",
  14400: "4h",
  21600: "6h",
  43200: "12h",
  86400: "1d",
}


def normalize_market_type(market_type: Optional[str]) -> Optional[str]:
  if not market_type:
    return None

  mt = market_type.lower()
  mapping = {
    "spot": "spot",
    "margin": "margin",
    "future": "future",
    "futures": "future",
    "swap": "swap",
    "perp": "swap",
    "perpetual": "swap",
  }
  return mapping.get(mt, mt)


def _allowed_market_types(market_type: Optional[str]) -> set[str]:
  """
  Return the set of market types to allow when filtering markets.

  Args:
    market_type: Requested market type (spot, futures, swap, etc.)

  Returns:
    Set of allowed market types for filtering
  """
  normalized = normalize_market_type(market_type)
  if not normalized:
    return {"spot"}  # Default to spot if not specified

  if normalized == "future":
    return {"future", "swap"}  # Futures can include perpetuals
  elif normalized == "swap":
    return {"swap"}  # Perpetuals only
  else:
    return {normalized}


def build_stream_params(market_type: Optional[str]) -> dict[str, Any]:
  normalized = normalize_market_type(market_type)
  if normalized in {"future", "swap", "margin"}:
    return {"type": normalized}
  return {}


def create_pro_client(exchange: str, market_type: Optional[str] = None):
  exchange_class = getattr(ccxtpro, exchange)
  params: dict[str, Any] = {}
  normalized = normalize_market_type(market_type)

  if normalized and normalized not in {"spot"}:
    params["options"] = {"defaultType": normalized}

  return exchange_class(params)


def create_rest_client(exchange: str, market_type: Optional[str] = None):
  exchange_class = getattr(ccxt, exchange)
  params: dict[str, Any] = {}
  normalized = normalize_market_type(market_type)

  if normalized and normalized not in {"spot"}:
    params["options"] = {"defaultType": normalized}

  return exchange_class(params)


def resolve_symbol_map(
  exchanges: list[str],
  requested_symbols: list[str],
  market_type: Optional[str] = None,
) -> dict[str, list[str]]:
  """
  Map requested symbols to exchange-specific symbols based on availability.

  For each exchange, finds the best matching symbol format. For example:
  - BTC/USDT might map to BTC/USDT on Binance
  - BTC/USDT might map to BTC/USD on Kraken for spot
  - BTC/USDT might map to BTC/USDT:USDT on Binance for perpetuals

  Args:
    exchanges: List of exchange names to check
    requested_symbols: List of symbols in standard format (e.g., ["BTC/USDT"])
    market_type: Market type filter (spot, futures, swap, etc.)

  Returns:
    Dict mapping exchange name to list of resolved symbols
  """
  result: dict[str, list[str]] = {}
  allowed_types = _allowed_market_types(market_type)

  for exchange_name in exchanges:
    try:
      ex = create_rest_client(exchange_name, market_type)
      markets = ex.load_markets()

      resolved: list[str] = []
      for req_symbol in requested_symbols:
        base_quote = req_symbol.split("/")
        if len(base_quote) != 2:
          continue

        base, quote = base_quote[0], base_quote[1]

        # Try exact match first
        if req_symbol in markets:
          market_info = markets[req_symbol]
          if market_info.get("type") in allowed_types:
            resolved.append(req_symbol)
            continue

        # Try common variations
        candidates = [
          f"{base}/{quote}",
          f"{base}/USD",
          f"{base}/USDC",
          f"{base}/{quote}:USDT",  # Perpetuals format
          f"{base}/{quote}:{quote}",  # Alternative perpetuals
        ]

        found = False
        for candidate in candidates:
          if candidate in markets:
            market_info = markets[candidate]
            if market_info.get("type") in allowed_types:
              resolved.append(candidate)
              found = True
              break

        if not found:
          print(
            f"[ccxt_ws] Symbol {req_symbol} not available on {exchange_name} "
            f"for market_type={market_type}",
            flush=True,
          )

      if resolved:
        result[exchange_name] = resolved
        print(
          f"[ccxt_ws] Resolved {exchange_name}: {requested_symbols} -> {resolved}",
          flush=True,
        )

      close_fn = getattr(ex, "close", None)
      if callable(close_fn):
        close_fn()

    except Exception as err:
      print(f"[ccxt_ws] Failed to resolve symbols for {exchange_name}: {err}", flush=True)

  return result


def interval_to_timeframe(interval_sec: float | int | None) -> Optional[str]:
  """
  Convert aggregation interval (seconds) into exchange timeframe string.

  Returns None if no reasonable mapping is found.
  """
  if not interval_sec:
    return None

  rounded = int(round(float(interval_sec)))
  return INTERVAL_TO_TIMEFRAME.get(rounded)


def _candle_to_event(
  exchange_id: str,
  symbol: str,
  candle: list[Any],
  timeframe: str,
) -> dict[str, Any]:
  """
  Convert an OHLCV candle into the event structure used by the pipeline.
  """
  if not candle:
    raise ValueError("Empty OHLCV candle")

  ts_ms, open_, high, low, close, volume = candle[:6]
  ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
  event: dict[str, Any] = {
    "ts": ts,
    "source": exchange_id,
    "symbol": symbol,
    "price": close,
    "open": open_,
    "high": high,
    "low": low,
    "close": close,
    "qty": volume,
    "base_volume": volume,
    "timeframe": timeframe,
  }

  if close is not None and volume is not None:
    event["volume"] = close * volume

  return event


def _ticker_to_event(
  exchange_id: str,
  symbol: str,
  ticker: dict[str, Any],
) -> dict[str, Any]:
  ts = ticker.get("timestamp")
  ts_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else datetime.now(tz=timezone.utc)

  last = ticker.get("last") or ticker.get("close")
  base_volume = ticker.get("baseVolume") or ticker.get("baseVolume24h")
  quote_volume = ticker.get("quoteVolume") or ticker.get("quoteVolume24h")

  event: dict[str, Any] = {
    "ts": ts_dt,
    "source": exchange_id,
    "symbol": symbol,
    "price": last,
    "bid": ticker.get("bid"),
    "ask": ticker.get("ask"),
    "high": ticker.get("high"),
    "low": ticker.get("low"),
    "open": ticker.get("open"),
    "close": ticker.get("close") or last,
  }

  if base_volume is not None:
    event["qty"] = base_volume
    event["base_volume"] = base_volume

  if last is not None and base_volume is not None:
    event["volume"] = base_volume * last
  elif quote_volume is not None:
    event["volume"] = quote_volume

  return event


async def ccxt_ws_stream(
  symbol: str = "BTC/USDT",
  exchange: str = "binance",
  exchange_instance: Optional[Any] = None,
  market_type: Optional[str] = None,
) -> AsyncIterator[dict]:
  """
  Stream real-time trade data via WebSocket from a single exchange.

  Args:
    symbol: Trading pair to track (e.g., "BTC/USDT")
    exchange: Exchange name (kraken, kucoin, binance, etc.)
    exchange_instance: Optional pre-created ccxt.pro exchange to reuse

  Yields:
    TradeEvent dictionaries with extended fields
  """
  print(f"[ccxt_ws] FUNCTION CALLED: symbol={symbol}, exchange={exchange}", flush=True)

  manage_lifecycle = exchange_instance is None

  params = build_stream_params(market_type)
  watch_trades_args = {
    "since": None,
    "limit": None,
    "params": params,
  }

  if manage_lifecycle:
    print(f"[ccxt_ws] Getting exchange class for {exchange}", flush=True)
    ex = create_pro_client(exchange, market_type)
    print(f"[ccxt_ws] Exchange instance created: {ex}", flush=True)
    await ex.load_markets()
  else:
    ex = exchange_instance
    print(f"[ccxt_ws] Reusing provided exchange instance for {exchange}", flush=True)

  try:
    print(f"[ccxt_ws] Entered try block", flush=True)
    print(f"[ccxt_ws] Connecting to {exchange} WebSocket for {symbol}...", flush=True)

    while True:
      try:
        # Watch trades (higher frequency than ticker)
        trades = await ex.watch_trades(
          symbol,
          watch_trades_args["since"],
          watch_trades_args["limit"],
          watch_trades_args["params"],
        )

        # Get latest trade from the list
        if trades:
          trade = trades[-1]
          print(f"[ccxt_ws] {exchange} trade: price={trade.get('price')}, amount={trade.get('amount')}")

          # Create trade event
          evt = TradeEvent(
            ts=datetime.now(tz=timezone.utc),
            source=ex.id,
            symbol=symbol,
            price=trade["price"],
            qty=trade.get("amount", 0.0),
          )

          # Fetch ticker for extended fields (bid/ask/high/low)
          try:
            ticker = ex.last_json_response if hasattr(ex, "last_json_response") else {}
            ticker_data = ticker.get("ticker", {}) if isinstance(ticker, dict) else {}
          except:
            ticker_data = {}

          # Add extended fields
          evt_dict = evt.model_dump()
          evt_dict["base_volume"] = evt_dict.get("qty")
          if evt_dict.get("price") is not None and evt_dict.get("qty") is not None:
            evt_dict["volume"] = evt_dict["price"] * evt_dict["qty"]
          evt_dict["bid"] = ticker_data.get("bid")
          evt_dict["ask"] = ticker_data.get("ask")
          evt_dict["high"] = ticker_data.get("high")
          evt_dict["low"] = ticker_data.get("low")
          evt_dict["open"] = ticker_data.get("open")
          evt_dict["close"] = trade.get("price")  # Use trade price as close

          yield evt_dict

      except Exception as e:
        print(f"[ccxt_ws] Error from {exchange}: {e}")
        await asyncio.sleep(1)  # Brief pause before retry

  finally:
    # Clean up WebSocket connection if we created it
    if manage_lifecycle:
      await ex.close()
      print(f"[ccxt_ws] Closed {exchange} WebSocket")


async def ccxt_ws_exchange_stream(
  exchange: str,
  symbols: list[str],
  market_type: Optional[str] = None,
) -> AsyncIterator[dict]:
  """Stream ticker/price updates for multiple symbols on a single exchange."""
  if not symbols:
    return

  print(
    f"[ccxt_ws] Starting shared stream for {exchange} with symbols={symbols} "
    f"market_type={market_type}",
    flush=True,
  )

  ex = create_pro_client(exchange, market_type)
  await ex.load_markets()
  if not getattr(ex, "has", {}).get("watchOHLCV"):
    raise RuntimeError(f"Exchange {exchange} does not support watchOHLCV")
  params = build_stream_params(market_type)

  capabilities = getattr(ex, "has", {}) or {}
  supports_batch = bool(capabilities.get("watchTickers"))
  supports_single = bool(capabilities.get("watchTicker"))
  supports_trades = bool(capabilities.get("watchTrades"))

  if not any([supports_batch, supports_single, supports_trades]):
    raise RuntimeError(
      f"Exchange {exchange} does not support watchTickers/watchTicker/watchTrades"
    )

  try:
    if supports_batch and len(symbols) > 1:
      while True:
        try:
          tickers = await ex.watch_tickers(symbols, params)
          for sym, ticker in tickers.items():
            if not ticker:
              continue
            yield _ticker_to_event(ex.id, sym, ticker)
        except asyncio.CancelledError:
          raise
        except Exception as err:
          print(f"[ccxt_ws] watch_tickers error {exchange}: {err}", flush=True)
          await asyncio.sleep(1.0)
    else:
      queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

      async def seed_ticker(sym: str) -> None:
        try:
          rest_exchange = create_rest_client(exchange, market_type)
          try:
            ticker = await asyncio.to_thread(
              rest_exchange.fetch_ticker,
              sym,
              params,
            )
            if ticker:
              await queue.put(_ticker_to_event(rest_exchange.id, sym, ticker))
          finally:
            close_fn = getattr(rest_exchange, "close", None)
            if callable(close_fn):
              close_fn()
        except Exception as err:
          print(f"[ccxt_ws] Seed ticker error {exchange} {sym}: {err}", flush=True)

      async def pump_watch_ticker(sym: str) -> None:
        while True:
          try:
            ticker = await ex.watch_ticker(sym, params)
            if ticker:
              await queue.put(_ticker_to_event(ex.id, sym, ticker))
          except asyncio.CancelledError:
            raise
          except Exception as err:
            print(f"[ccxt_ws] watch_ticker error {exchange} {sym}: {err}", flush=True)
            await asyncio.sleep(1.0)

      async def pump_trades(sym: str) -> None:
        try:
          async for event in ccxt_ws_stream(
            sym,
            exchange,
            exchange_instance=ex,
            market_type=market_type,
          ):
            await queue.put(event)
        except asyncio.CancelledError:
          raise
        except Exception as err:
          print(f"[ccxt_ws] Pump trades error {exchange} {sym}: {err}", flush=True)

      seed_tasks = [asyncio.create_task(seed_ticker(sym)) for sym in symbols]
      tasks: list[asyncio.Task] = []
      if supports_single:
        tasks = [asyncio.create_task(pump_watch_ticker(sym)) for sym in symbols]
      elif supports_trades:
        tasks = [asyncio.create_task(pump_trades(sym)) for sym in symbols]
      else:
        raise RuntimeError(
          f"Exchange {exchange} lacks watchTicker but also cannot fallback to trades"
        )

      try:
        while True:
          event = await queue.get()
          yield event
      finally:
        for seed in seed_tasks:
          seed.cancel()
        await asyncio.gather(*seed_tasks, return_exceptions=True)
        for task in tasks:
          task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
  finally:
    await ex.close()
    print(f"[ccxt_ws] Closed shared {exchange} WebSocket", flush=True)


async def ccxt_ws_candle_stream(
  exchange: str,
  symbols: list[str],
  interval_sec: float,
  market_type: Optional[str] = None,
) -> AsyncIterator[dict]:
  """
  Stream OHLCV candles for the requested symbols and deliver an immediate snapshot.
  """
  if not symbols:
    return

  timeframe = interval_to_timeframe(interval_sec)
  if not timeframe:
    raise ValueError(f"No timeframe mapping for interval {interval_sec}s")

  print(
    f"[ccxt_ws] Starting candle stream for {exchange} timeframe={timeframe} symbols={symbols}",
    flush=True,
  )

  ex = create_pro_client(exchange, market_type)
  await ex.load_markets()
  queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
  params = build_stream_params(market_type)

  async def seed_symbol(sym: str) -> None:
    """Fetch the most recent closed candle to populate data immediately."""
    try:
      candles = await ex.fetch_ohlcv(sym, timeframe, limit=1, params=params)
      if candles:
        await queue.put(_candle_to_event(ex.id, sym, candles[-1], timeframe))
    except AttributeError:
      rest_exchange = create_rest_client(exchange, market_type)
      try:
        candles = await asyncio.to_thread(
          rest_exchange.fetch_ohlcv,
          sym,
          timeframe,
          None,
          1,
          params,
        )
        if candles:
          await queue.put(
            _candle_to_event(rest_exchange.id, sym, candles[-1], timeframe)
          )
      finally:
        close_fn = getattr(rest_exchange, "close", None)
        if callable(close_fn):
          close_fn()
    except asyncio.CancelledError:
      raise
    except Exception as err:
      print(f"[ccxt_ws] Seed candle error {exchange} {sym}: {err}", flush=True)

  async def watch_symbol(sym: str) -> None:
    """Subscribe to live candle updates and forward the latest data."""
    try:
      async for candles in ex.watch_ohlcv(sym, timeframe, params=params):
        if candles:
          await queue.put(_candle_to_event(ex.id, sym, candles[-1], timeframe))
    except asyncio.CancelledError:
      raise
    except Exception as err:
      print(f"[ccxt_ws] watch_ohlcv error {exchange} {sym}: {err}", flush=True)

  seed_tasks = [asyncio.create_task(seed_symbol(sym)) for sym in symbols]
  watch_tasks = [asyncio.create_task(watch_symbol(sym)) for sym in symbols]
  tasks = seed_tasks + watch_tasks

  try:
    while True:
      event = await queue.get()
      yield event
  finally:
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await ex.close()
    print(f"[ccxt_ws] Closed candle stream for {exchange}", flush=True)


async def ccxt_ws_multi_stream(
  symbol: str = "BTC/USDT",
  exchanges: list[str] = None
) -> AsyncIterator[dict]:
  """
  Stream from multiple exchanges concurrently via WebSocket.

  Args:
    symbol: Trading pair to track
    exchanges: List of exchange names (default: ["kraken", "kucoin"])

  Yields:
    TradeEvent dictionaries from all exchanges
  """
  if exchanges is None:
    exchanges = EXCHANGES

  # Create queue for merging streams
  queue = asyncio.Queue()

  async def pump_exchange(exchange_name: str):
    """Pump events from one exchange into shared queue."""
    async for event in ccxt_ws_stream(symbol, exchange_name):
      await queue.put(event)

  # Start all exchange streams concurrently
  tasks = [asyncio.create_task(pump_exchange(ex)) for ex in exchanges]

  try:
    while True:
      # Yield events as they arrive from any exchange
      event = await queue.get()
      yield event
  finally:
    # Clean up all tasks
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

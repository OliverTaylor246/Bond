"""
CCXT Pro WebSocket connector - real-time market data streaming.
Uses ccxt.pro for low-latency WebSocket connections to exchanges.

Latency: ~50-200ms (vs 1-6 seconds for REST polling)
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional
import ccxt.pro as ccxtpro
from engine.schemas import TradeEvent


EXCHANGES = ["kraken", "kucoin"]


async def ccxt_ws_stream(
  symbol: str = "BTC/USDT",
  exchange: str = "kraken",
  exchange_instance: Optional[Any] = None,
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

  if manage_lifecycle:
    print(f"[ccxt_ws] Getting exchange class for {exchange}", flush=True)
    exchange_class = getattr(ccxtpro, exchange)
    print(f"[ccxt_ws] Creating exchange instance", flush=True)
    ex = exchange_class()
    print(f"[ccxt_ws] Exchange instance created: {ex}", flush=True)
  else:
    ex = exchange_instance
    print(f"[ccxt_ws] Reusing provided exchange instance for {exchange}", flush=True)

  try:
    print(f"[ccxt_ws] Entered try block", flush=True)
    print(f"[ccxt_ws] Connecting to {exchange} WebSocket for {symbol}...", flush=True)

    while True:
      try:
        # Watch trades (higher frequency than ticker)
        trades = await ex.watch_trades(symbol)

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
) -> AsyncIterator[dict]:
  """
  Stream all requested symbols for a single exchange using one shared connection.
  """
  if not symbols:
    return

  print(f"[ccxt_ws] Starting shared stream for {exchange} with symbols={symbols}", flush=True)
  exchange_class = getattr(ccxtpro, exchange)
  ex = exchange_class()
  queue = asyncio.Queue()

  async def pump_symbol(sym: str):
    try:
      async for event in ccxt_ws_stream(sym, exchange, exchange_instance=ex):
        await queue.put(event)
    except asyncio.CancelledError:
      raise
    except Exception as err:
      print(f"[ccxt_ws] Pump error for {exchange} {sym}: {err}", flush=True)

  tasks = [asyncio.create_task(pump_symbol(sym)) for sym in symbols]

  try:
    while True:
      event = await queue.get()
      yield event
  finally:
    for task in tasks:
      task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await ex.close()
    print(f"[ccxt_ws] Closed shared {exchange} WebSocket", flush=True)


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

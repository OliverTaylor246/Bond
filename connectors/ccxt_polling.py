"""
CCXT polling connector - fetches market data via REST API.
Uses regular CCXT (not ccxt.pro) for simplicity.
"""
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
import ccxt
from engine.schemas import TradeEvent


EXCHANGES = ["kraken", "kucoin"]  # coinbase requires API keys


async def ccxt_poll_stream(
  symbol: str = "BTC/USDT",
  interval: int = 5
) -> AsyncIterator[dict]:
  """
  Poll multiple exchanges for ticker data every `interval` seconds.

  Args:
    symbol: Trading pair to track
    interval: Polling interval in seconds

  Yields:
    TradeEvent dictionaries
  """
  clients = [getattr(ccxt, name)() for name in EXCHANGES]

  while True:
    for ex in clients:
      try:
        # Fetch ticker data (REST call)
        ticker = await asyncio.to_thread(ex.fetch_ticker, symbol)

        evt = TradeEvent(
          ts=datetime.now(tz=timezone.utc),
          source=ex.id,
          symbol=symbol,
          price=ticker["last"],
          qty=ticker.get("baseVolume", 0.0),
        )

        # Add extended fields to event dict
        evt_dict = evt.model_dump()
        evt_dict["bid"] = ticker.get("bid")
        evt_dict["ask"] = ticker.get("ask")
        evt_dict["high"] = ticker.get("high")
        evt_dict["low"] = ticker.get("low")
        evt_dict["open"] = ticker.get("open")
        evt_dict["close"] = ticker.get("close")

        yield evt_dict

      except Exception as e:
        # Silently skip errors for robustness
        print(f"[ccxt_polling] Error from {ex.id}: {e}")
        continue

    await asyncio.sleep(interval)


async def ccxt_poll_trades(
  symbol: str = "BTC/USDT",
  interval: int = 5
) -> AsyncIterator[dict]:
  """
  Alternative: poll recent trades instead of ticker.

  Args:
    symbol: Trading pair
    interval: Polling interval in seconds

  Yields:
    TradeEvent dictionaries for each trade
  """
  clients = [getattr(ccxt, name)() for name in EXCHANGES]

  while True:
    for ex in clients:
      try:
        trades = await asyncio.to_thread(ex.fetch_trades, symbol, limit=10)

        for trade in trades:
          evt = TradeEvent(
            ts=datetime.fromtimestamp(trade["timestamp"] / 1000, tz=timezone.utc),
            source=ex.id,
            symbol=symbol,
            price=trade["price"],
            qty=trade["amount"],
            side=trade.get("side"),
          )
          yield evt.model_dump()

      except Exception as e:
        print(f"[ccxt_polling] Error from {ex.id}: {e}")
        continue

    await asyncio.sleep(interval)

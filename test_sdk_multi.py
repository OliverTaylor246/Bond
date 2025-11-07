"""
Multi-symbol SDK test - create a BTC + ETH stream and print sample events.
"""
import asyncio
import os
import sys
from typing import Any

# Ensure the Python SDK is importable when running this file directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sdk/python"))

from bond.client import BondClient


DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT"]
MAX_EVENTS = int(os.getenv("BOND_MAX_EVENTS", "10"))
API_URL = os.getenv("BOND_API_URL", "http://localhost:8000")
WS_URL = os.getenv("BOND_WS_URL", "ws://localhost:8080")


def parse_symbols() -> list[str]:
  raw = os.getenv("BOND_SYMBOLS")
  if not raw:
    return DEFAULT_SYMBOLS
  return [sym.strip() for sym in raw.split(",") if sym.strip()]


def build_spec(symbols: list[str], interval_sec: int) -> dict[str, Any]:
  return {
    "sources": [
      {
        "type": "ccxt",
        "symbols": symbols,
        "fields": ["price", "volume"],
      }
    ],
    "symbols": symbols,
    "interval_sec": interval_sec,
  }


def summarize_exchange_data(raw_data: dict[str, Any]) -> str:
  exchange_data = raw_data.get("exchange_data", {}) if isinstance(raw_data, dict) else {}
  if not exchange_data:
    return "    (waiting for first trade update)"

  lines: list[str] = []
  for exchange, info in exchange_data.items():
    symbol = info.get("latest_symbol")
    price = info.get("price")
    bid = info.get("bid")
    ask = info.get("ask")
    volume = info.get("volume")
    lines.append(
      "    {ex}: {sym} price={price} bid={bid} ask={ask} volume={volume}".format(
        ex=exchange,
        sym=symbol or "?",
        price=f"{price:.2f}" if isinstance(price, (int, float)) else price,
        bid=f"{bid:.2f}" if isinstance(bid, (int, float)) else bid,
        ask=f"{ask:.2f}" if isinstance(ask, (int, float)) else ask,
        volume=f"{volume:.2f}" if isinstance(volume, (int, float)) else volume,
      )
    )

  return "\n".join(lines)


async def main() -> None:
  symbols = parse_symbols()
  interval_sec = int(os.getenv("BOND_INTERVAL_SEC", "5"))

  client = BondClient(api_url=API_URL, ws_url=WS_URL)
  spec = build_spec(symbols, interval_sec)

  print("Creating multi-symbol stream:")
  print(f"  Symbols     : {symbols}")
  print(f"  Interval    : {interval_sec}s")
  print(f"  API URL     : {API_URL}")
  print(f"  WebSocket   : {WS_URL}")
  print()

  stream_info = await client.create_stream(spec)
  stream_id = stream_info["stream_id"]
  access_token = stream_info["access_token"]

  print("Stream ready!")
  print(f"  Stream ID   : {stream_id}")
  print(f"  Access token: {access_token}")
  print("  WS URL      :", stream_info["ws_url"])
  print("\nListening for events...\n")

  event_count = 0

  try:
    async for event in client.subscribe(stream_id, access_token):
      event_count += 1
      price_avg = event.get("price_avg")
      volume_sum = event.get("volume_sum")

      if isinstance(price_avg, (int, float)):
        price_display = f"{price_avg:.2f}"
      else:
        price_display = price_avg

      if isinstance(volume_sum, (int, float)):
        volume_display = f"{volume_sum:.2f}"
      else:
        volume_display = volume_sum

      print(f"Event #{event_count}")
      print(f"  price_avg={price_display} volume_sum={volume_display}")

      raw_data = event.get("raw_data", {})
      print(summarize_exchange_data(raw_data))
      print()

      if MAX_EVENTS and event_count >= MAX_EVENTS:
        print(f"Reached max events ({MAX_EVENTS}). Stopping subscription.")
        break

  except KeyboardInterrupt:
    print(f"\nInterrupted after {event_count} events.")
  except Exception as err:
    print(f"\nError while streaming: {err}")
  finally:
    print("Done.")


if __name__ == "__main__":
  asyncio.run(main())

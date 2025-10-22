"""
Simple example of using the Bond SDK.

Usage:
  python examples/simple_client.py
"""
import asyncio
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sdk.python.bond import listen


async def main():
  """Create a stream and print events."""
  print("Creating stream: BTC price + tweets + liquidations every 5s")
  print("Press Ctrl+C to stop\n")

  try:
    async for event in listen("BTC price + tweets + liquidations every 5s"):
      print(f"[{event['ts']}]")
      if event.get('price_avg'):
        print(f"  Price: ${event['price_avg']:.2f}")
      if event.get('volume_sum'):
        print(f"  Volume: {event['volume_sum']:.2f}")
      print(f"  Tweets: {event['tweets']}")
      print(f"  Custom events: {event['custom_count']}")
      print()

  except KeyboardInterrupt:
    print("\nStopped by user")


if __name__ == "__main__":
  asyncio.run(main())

"""
Test Bond SDK - Subscribe to your live BTC/USDT stream
"""
import asyncio
import sys
import os

# Add sdk to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sdk/python'))

from bond.client import BondClient


async def main():
    # Your live stream from UI
    stream_id = "76cee6a20d0d1362"
    access_token = "2077586214.8dd22c252891a049"

    client = BondClient(
        api_url="http://localhost:8000",
        ws_url="ws://localhost:8080"
    )

    print(f"✓ Connecting to stream {stream_id}...")
    print("✓ Subscribing to BTC/USDT live data\n")

    event_count = 0
    try:
        async for event in client.subscribe(stream_id, access_token):
            event_count += 1
            price = event.get('price_avg')
            bid = event.get('bid_avg')
            ask = event.get('ask_avg')
            volume = event.get('volume_sum')
            ts = event.get('ts')

            print(f"[{ts}] BTC/USDT = ${price:,.2f} | Bid: ${bid:,.2f} | Ask: ${ask:,.2f} | Vol: {volume:,.0f}")

            # Stop after 20 events for demo
            if event_count >= 20:
                print(f"\n✅ SDK Test Complete! Received {event_count} live events.")
                break

    except KeyboardInterrupt:
        print(f"\n✅ Stopped. Received {event_count} events.")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

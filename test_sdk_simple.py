"""
Simple SDK test - Subscribe to stream e5df1750b8991581
"""
import asyncio
import sys
import os

# Add sdk to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sdk/python'))

from bond.client import BondClient


async def main():
    stream_id = "e5df1750b8991581"
    access_token = "2077583564.f1189e3ca7ce6df8"

    client = BondClient(
        api_url="http://localhost:8000",
        ws_url="ws://localhost:8080"
    )

    print(f"✓ Connecting to stream {stream_id}...")
    print("✓ Waiting for BTC/USDT price events (Ctrl+C to stop)\n")

    event_count = 0
    try:
        async for event in client.subscribe(stream_id, access_token):
            event_count += 1
            price = event.get('price_avg')
            volume = event.get('volume_sum')

            print(f"Event #{event_count}: BTC/USDT = ${price:,.2f} | Volume: {volume:,.2f}" if price else f"Event #{event_count}: {event}")

            # Stop after 10 events for testing
            if event_count >= 10:
                print(f"\n✓ Successfully received {event_count} events!")
                break

    except KeyboardInterrupt:
        print(f"\n✓ Received {event_count} events. Test complete!")
    except Exception as e:
        print(f"\n✗ Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

"""
Test script for Bond SDK - Subscribe to existing stream
"""
import asyncio
import sys
import os

# Add sdk to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sdk/python'))

from bond.client import BondClient


async def main():
    # Your stream details from UI
    stream_id = "e5df1750b8991581"
    access_token = input("Enter your access token from the UI: ").strip()

    # Create client
    client = BondClient(
        api_url="http://localhost:8000",
        ws_url="ws://localhost:8080"
    )

    print(f"Connecting to stream {stream_id}...")
    print("Waiting for events (press Ctrl+C to stop)...\n")

    event_count = 0
    try:
        async for event in client.subscribe(stream_id, access_token):
            event_count += 1
            print(f"Event #{event_count}:")
            print(f"  Timestamp: {event.get('ts')}")
            print(f"  Price Avg: ${event.get('price_avg', 'N/A')}")
            print(f"  Volume Sum: {event.get('volume_sum', 'N/A')}")
            print(f"  Window: {event.get('window_start')} -> {event.get('window_end')}")
            print()

    except KeyboardInterrupt:
        print(f"\nReceived {event_count} events. Disconnecting...")


if __name__ == "__main__":
    asyncio.run(main())

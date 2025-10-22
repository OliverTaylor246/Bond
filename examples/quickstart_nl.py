"""
Bond Quickstart - Natural Language Example

This example shows how to:
1. Create a stream using natural language
2. Connect via WebSocket
3. Print events in real-time

Requirements: pip install websockets
"""
import asyncio
import json
import os
import httpx
import websockets


API_URL = os.getenv("BOND_API_URL", "http://localhost:8000")


async def main():
  """Run the quickstart example."""
  print("🔗 Bond Quickstart Example")
  print("=" * 50)
  
  # Step 1: Create stream with natural language
  print("\n1. Creating stream...")
  async with httpx.AsyncClient() as client:
    response = await client.post(
      f"{API_URL}/v1/streams",
      json={"natural_language": "BTC price + tweets + onchain every 5s"},
      timeout=10.0
    )
    response.raise_for_status()
    data = response.json()
  
  stream_id = data["stream_id"]
  ws_url = data["ws_url"]
  
  print(f"✓ Stream created: {stream_id}")
  print(f"✓ WebSocket URL: {ws_url}")
  print(f"\nSpec: {json.dumps(data['spec'], indent=2)}")
  
  # Step 2: Connect to WebSocket
  print("\n2. Connecting to stream...")
  print("(Press Ctrl+C to stop)\n")
  
  try:
    async with websockets.connect(ws_url) as websocket:
      event_count = 0
      
      # Step 3: Receive and print events
      async for message in websocket:
        event_count += 1
        event = json.loads(message)
        
        # Pretty print key fields
        print(f"Event #{event_count} @ {event.get('ts', 'N/A')}")
        print(f"  💰 Price: ${event.get('price_avg', 0):.2f}")
        print(f"  📊 Volume: {event.get('volume_sum', 0):.2f}")
        print(f"  🐦 Tweets: {event.get('tweets', 0)}")
        print(f"  ⛓️  On-chain: {event.get('onchain_count', 0)} events")
        if event.get('onchain_value_sum'):
          print(f"     Value: {event.get('onchain_value_sum', 0):.2f}")
        print()
        
        # Stop after 10 events for demo
        if event_count >= 10:
          print("✓ Received 10 events, stopping...")
          break
  
  except KeyboardInterrupt:
    print("\n✓ Stopped by user")
  
  # Step 4: Clean up (optional)
  print("\n3. Cleaning up...")
  async with httpx.AsyncClient() as client:
    await client.delete(f"{API_URL}/v1/streams/{stream_id}")
  
  print("✓ Stream deleted")
  print("\n✅ Quickstart complete!")


if __name__ == "__main__":
  asyncio.run(main())

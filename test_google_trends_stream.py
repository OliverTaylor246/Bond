"""Test Google Trends streaming functionality"""
import asyncio
from connectors.google_trends_stream import google_trends_stream

async def test_stream():
    print("=" * 60)
    print("Testing Google Trends Stream")
    print("=" * 60)
    print("\n🔍 Keywords: bitcoin, ethereum, crypto")
    print("⏱️  Interval: 10 seconds (for testing)")
    print("📅 Timeframe: Last 24 hours\n")

    keywords = ['bitcoin', 'ethereum', 'crypto']
    interval = 10  # Fast for testing
    timeframe = "now 1-d"

    count = 0
    max_updates = 3  # Only show 3 updates

    async for event in google_trends_stream(keywords, interval, timeframe):
        count += 1

        if 'error' in event:
            print(f"\n❌ Error: {event['error']}")
        elif event.get('source') == 'google_trends':
            keyword = event.get('keyword', 'unknown')
            interest = event.get('interest', 0)
            ts = event.get('ts')
            print(f"\n📊 Update #{count}: {keyword}")
            print(f"   Interest Score: {interest}/100")
            print(f"   Timestamp: {ts}")
        elif event.get('source') == 'google_trends_related':
            keyword = event.get('keyword', 'unknown')
            rising = event.get('related_rising', [])
            top = event.get('related_top', [])
            print(f"\n🔥 Related queries for '{keyword}':")
            if rising:
                print(f"   Rising ({len(rising)} queries):")
                for q in rising[:3]:
                    print(f"      - {q.get('query')}: +{q.get('value')}%")
            if top:
                print(f"   Top ({len(top)} queries):")
                for q in top[:3]:
                    print(f"      - {q.get('query')}: {q.get('value')}")

        if count >= max_updates * (len(keywords) + 1):  # keywords + 1 related event per cycle
            print(f"\n✅ Test complete! Received {count} events")
            break

if __name__ == "__main__":
    asyncio.run(test_stream())

#!/usr/bin/env python3
"""
Test script for Twitter hashtag counting.
Counts tweets with specific hashtags in the last N minutes.
"""
import asyncio
import os
from connectors.x_stream import x_hashtag_count, x_hashtag_stream


async def test_single_count():
    """Test counting hashtags once."""
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")

    if not bearer_token:
        print("❌ No TWITTER_BEARER_TOKEN found in environment")
        print("Set it with: export TWITTER_BEARER_TOKEN='your_token_here'")
        return

    # Track these hashtags
    hashtags = ["bitcoin", "crypto", "ethereum"]
    minutes = 10  # Last 10 minutes

    print(f"🔍 Counting tweets with hashtags: {hashtags}")
    print(f"📅 Time window: Last {minutes} minutes\n")

    result = await x_hashtag_count(bearer_token, hashtags, minutes)

    print(f"\n📊 Results:")
    print(f"   Total tweets: {result['total']}")
    print(f"   Breakdown:")
    for tag, count in result['counts'].items():
        print(f"      #{tag}: {count} tweets")
    print(f"   Time: {result['timestamp']}")


async def test_streaming_counts():
    """Test streaming hashtag counts over time."""
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")

    if not bearer_token:
        print("❌ No TWITTER_BEARER_TOKEN found")
        return

    hashtags = ["bitcoin", "crypto"]
    interval = 60  # Check every 60 seconds
    minutes = 5    # Count tweets from last 5 minutes

    print(f"📡 Streaming hashtag counts every {interval}s")
    print(f"📋 Hashtags: {hashtags}")
    print(f"⏱️  Window: {minutes} minutes\n")
    print("Press Ctrl+C to stop...\n")

    count = 0
    async for event in x_hashtag_stream(hashtags, interval, minutes, bearer_token):
        count += 1
        print(f"[Update #{count}] Total: {event['total_count']} | {event['hashtag_counts']}")

        if count >= 3:  # Stop after 3 updates for demo
            print("\n✅ Demo complete!")
            break


if __name__ == "__main__":
    print("=" * 60)
    print("Twitter Hashtag Counter")
    print("=" * 60 + "\n")

    # Choose which test to run
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "stream":
        asyncio.run(test_streaming_counts())
    else:
        asyncio.run(test_single_count())
        print("\n💡 Tip: Run with 'python test_twitter_hashtags.py stream' to test streaming mode")

"""
Nitter Playwright connector - SYNC version (works better in WSL).
Continuously scrapes tweets using synchronous Playwright.
"""
import time
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright


NITTER_INSTANCE = "nitter.poast.org"


def fetch_latest_tweet_sync(username: str, page) -> dict | None:
    """
    Fetch latest tweet using sync Playwright.

    Args:
        username: Twitter username (without @)
        page: Playwright page object

    Returns:
        Dict with tweet data or None if failed
    """
    url = f"https://{NITTER_INSTANCE}/{username}"

    try:
        # Navigate to profile
        page.goto(url, timeout=30000)

        # Wait for content to load
        page.wait_for_timeout(3000)

        # Check if tweets are present
        tweets = page.locator('.timeline-item')
        count = tweets.count()

        if count == 0:
            print(f"[nitter_sync] No tweets found for @{username}")
            return None

        # Get first tweet
        first_tweet = tweets.first

        # Extract text
        tweet_content = first_tweet.locator('.tweet-content')
        text = tweet_content.inner_text() if tweet_content.count() > 0 else ""

        # Extract timestamp
        tweet_date = first_tweet.locator('.tweet-date a')
        timestamp = None
        if tweet_date.count() > 0:
            timestamp = tweet_date.get_attribute('title')

        # Extract stats
        stats_container = first_tweet.locator('.tweet-stats')
        stats_text = ""
        if stats_container.count() > 0:
            stats_text = stats_container.inner_text()

        return {
            'username': username,
            'text': text,
            'timestamp': timestamp,
            'stats': stats_text,
            'instance': NITTER_INSTANCE,
            'fetched_at': datetime.now(tz=timezone.utc).isoformat()
        }

    except Exception as e:
        print(f"[nitter_sync] Error fetching @{username}: {e}")
        return None


def nitter_sync_stream(username: str = "elonmusk", interval: int = 1):
    """
    Continuously poll Nitter for latest tweet (sync generator).

    Args:
        username: Twitter username (without @)
        interval: Polling interval in seconds

    Yields:
        Tweet event dictionaries
    """
    last_tweet_text = None

    print(f"[nitter_sync] Starting stream for @{username} (interval: {interval}s)")

    with sync_playwright() as p:
        # Launch browser (headless=False works in WSL)
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        print(f"[nitter_sync] Browser launched and ready\n")

        try:
            while True:
                tweet = fetch_latest_tweet_sync(username, page)

                if tweet:
                    # Only yield if it's a new tweet
                    if tweet['text'] != last_tweet_text:
                        print(f"[nitter_sync] 🆕 New tweet from @{username}")
                        print(f"      {tweet['text'][:80]}...")

                        # Format as Bond event
                        evt = {
                            'ts': datetime.now(tz=timezone.utc),
                            'source': f'nitter:{username}',
                            'symbol': username.upper(),
                            'text': tweet['text'],
                            'timestamp_posted': tweet['timestamp'],
                            'stats': tweet['stats'],
                            'instance': tweet['instance'],
                            'event_type': 'tweet'
                        }

                        yield evt
                        last_tweet_text = tweet['text']
                    else:
                        # Same tweet, no update
                        print(f"[nitter_sync] No new tweet from @{username} (checked at {datetime.now().strftime('%H:%M:%S')})")
                else:
                    print(f"[nitter_sync] Failed to fetch tweet from @{username}")

                time.sleep(interval)

        finally:
            print(f"\n[nitter_sync] Closing browser...")
            browser.close()


# Test function
def test_stream():
    """Test the streaming connector"""
    print("=" * 70)
    print("Testing Nitter Sync Stream Connector")
    print("=" * 70)
    print(f"Checking for new tweets every 5 seconds")
    print(f"Press Ctrl+C to stop\n")

    try:
        for tweet in nitter_sync_stream(username="elonmusk", interval=5):
            print(f"\n{'='*70}")
            print(f"🐦 @{tweet['symbol']}")
            print(f"📝 {tweet['text'][:200]}")
            print(f"🕐 {tweet['timestamp_posted']}")
            print(f"📊 {tweet['stats']}")
            print(f"{'='*70}\n")
    except KeyboardInterrupt:
        print("\n\n⏹️  Stopped by user")


if __name__ == "__main__":
    test_stream()

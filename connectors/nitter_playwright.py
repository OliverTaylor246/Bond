"""
Nitter Playwright connector - continuously scrapes tweets using Playwright.
Handles JavaScript challenges and bot protection.
"""
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
from playwright.async_api import async_playwright


NITTER_INSTANCE = "nitter.poast.org"

#DO NOT TAKE THE FIRST TWEET AS NEW - SORT BY DATETIME (BECAUSE OF PINNED)

async def fetch_latest_tweet_playwright(username: str, page) -> dict | None:
    """
    Fetch latest tweet using Playwright (reusing existing page).

    Args:
        username: Twitter username (without @)
        page: Playwright page object (already opened)

    Returns:
        Dict with tweet data or None if failed
    """
    url = f"https://{NITTER_INSTANCE}/{username}"

    try:
        # Navigate to profile
        await page.goto(url, timeout=30000, wait_until='networkidle')

        # Wait longer for bot check to complete
        await page.wait_for_timeout(5000)

        # Check if tweets are present
        tweets = page.locator('.timeline-item')
        count = await tweets.count()

        if count == 0:
            print(f"[nitter_playwright] No tweets found for @{username}")
            return None

        # Get first tweet
        first_tweet = tweets.first

        # Extract text
        tweet_content = first_tweet.locator('.tweet-content')
        text = await tweet_content.inner_text() if await tweet_content.count() > 0 else ""

        # Extract timestamp
        tweet_date = first_tweet.locator('.tweet-date a')
        timestamp = None
        if await tweet_date.count() > 0:
            timestamp = await tweet_date.get_attribute('title')

        # Extract stats
        stats_container = first_tweet.locator('.tweet-stats')
        stats_text = ""
        if await stats_container.count() > 0:
            stats_text = await stats_container.inner_text()

        return {
            'username': username,
            'text': text,
            'timestamp': timestamp,
            'stats': stats_text,
            'instance': NITTER_INSTANCE,
            'fetched_at': datetime.now(tz=timezone.utc).isoformat()
        }

    except Exception as e:
        print(f"[nitter_playwright] Error fetching @{username}: {e}")
        return None


async def nitter_playwright_stream(
    username: str = "elonmusk",
    interval: int = 1
) -> AsyncIterator[dict]:
    """
    Continuously poll Nitter for latest tweet using Playwright.

    Args:
        username: Twitter username (without @)
        interval: Polling interval in seconds

    Yields:
        Tweet event dictionaries
    """
    last_tweet_text = None

    print(f"[nitter_playwright] Starting stream for @{username} (interval: {interval}s)")

    # Launch browser once and keep it alive
    async with async_playwright() as p:
        # Use headless mode with evasion techniques
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled'
            ]
        )
        # Set realistic viewport and user agent, inject evasion scripts
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
            timezone_id='America/New_York'
        )
        page = await context.new_page()

        # Inject anti-detection script
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        print(f"[nitter_playwright] Browser launched and ready")

        try:
            first_fetch = True  # Track if this is the first fetch

            while True:
                tweet = await fetch_latest_tweet_playwright(username, page)

                if tweet:
                    # Always yield on first fetch, then only yield if tweet changed
                    if first_fetch or tweet['text'] != last_tweet_text:
                        if first_fetch:
                            print(f"[nitter_playwright] 📌 Initial tweet from @{username}: {tweet['text'][:60]}...")
                            first_fetch = False
                        else:
                            print(f"[nitter_playwright] 🆕 New tweet from @{username}: {tweet['text'][:60]}...")

                        # Format as Bond event
                        evt = {
                            'ts': datetime.now(tz=timezone.utc),
                            'source': f'nitter_playwright:{username}',
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
                        print(f"[nitter_playwright] No new tweet from @{username}")
                else:
                    print(f"[nitter_playwright] Failed to fetch tweet from @{username}")

                await asyncio.sleep(interval)

        finally:
            print(f"[nitter_playwright] Closing browser...")
            await browser.close()


async def nitter_playwright_multi_users(
    usernames: list[str] = None,
    interval: int = 5
) -> AsyncIterator[dict]:
    """
    Poll multiple Twitter users simultaneously.

    Args:
        usernames: List of usernames to track
        interval: Polling interval in seconds per user

    Yields:
        Tweet event dictionaries from any tracked user
    """
    if usernames is None:
        usernames = ["elonmusk", "vitalikbuterin"]

    print(f"[nitter_playwright] Tracking users: {usernames}")

    last_tweets = {user: None for user in usernames}

    # Launch browser once and keep it alive
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
        page = await browser.new_page()

        print(f"[nitter_playwright] Browser launched and ready")

        try:
            while True:
                for username in usernames:
                    tweet = await fetch_latest_tweet_playwright(username, page)

                    if tweet and tweet['text'] != last_tweets[username]:
                        print(f"[nitter_playwright] New tweet from @{username}")

                        evt = {
                            'ts': datetime.now(tz=timezone.utc),
                            'source': f'nitter_playwright:{username}',
                            'symbol': username.upper(),
                            'text': tweet['text'],
                            'timestamp_posted': tweet['timestamp'],
                            'stats': tweet['stats'],
                            'event_type': 'tweet'
                        }

                        yield evt
                        last_tweets[username] = tweet['text']

                    await asyncio.sleep(1)  # Small delay between users

                await asyncio.sleep(interval)

        finally:
            print(f"[nitter_playwright] Closing browser...")
            await browser.close()


# Test function
async def test_stream():
    """Test the streaming connector"""
    print("Testing Nitter Playwright stream connector...")
    print("Will check for new tweets every 5 seconds. Press Ctrl+C to stop.\n")

    try:
        async for tweet in nitter_playwright_stream(username="elonmusk", interval=5):
            print(f"\n{'='*70}")
            print(f"🐦 @{tweet['symbol']}")
            print(f"📝 {tweet['text'][:200]}")
            print(f"🕐 {tweet['timestamp_posted']}")
            print(f"📊 {tweet['stats']}")
            print(f"{'='*70}\n")
    except KeyboardInterrupt:
        print("\n\n⏹️  Stopped by user")


if __name__ == "__main__":
    asyncio.run(test_stream())

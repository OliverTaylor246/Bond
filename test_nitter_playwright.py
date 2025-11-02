"""Test Nitter scraping with Playwright (handles JavaScript challenges)"""
from playwright.sync_api import sync_playwright
import time

NITTER_INSTANCE = "nitter.poast.org"
USERNAME = "elonmusk"

def scrape_latest_tweet_nitter():
    """Scrape latest tweet from Nitter using Playwright"""

    url = f"https://{NITTER_INSTANCE}/{USERNAME}"
    print(f"🔍 Scraping: {url}")

    with sync_playwright() as p:
        # Launch browser (headless=False to see what's happening)
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        try:
            print("📡 Loading page...")
            page.goto(url, timeout=30000)

            # Wait for content to load (bot protection might take a few seconds)
            print("⏳ Waiting for tweets to load...")
            page.wait_for_timeout(5000)

            # Try multiple selectors (Nitter HTML structure)
            print("🔍 Looking for tweets...")

            # Check if we see the timeline
            timeline = page.locator('.timeline')
            if timeline.count() > 0:
                print(f"✅ Found timeline with {timeline.count()} elements")

            # Get first tweet
            tweets = page.locator('.timeline-item')
            tweet_count = tweets.count()
            print(f"📊 Found {tweet_count} tweets")

            if tweet_count > 0:
                first_tweet = tweets.first

                # Extract tweet content
                tweet_content = first_tweet.locator('.tweet-content')
                if tweet_content.count() > 0:
                    text = tweet_content.inner_text()
                    print(f"\n{'='*60}")
                    print(f"✅ SUCCESS! Latest tweet:")
                    print(f"{'='*60}")
                    print(text)
                    print(f"{'='*60}\n")

                    # Extract timestamp
                    tweet_date = first_tweet.locator('.tweet-date a')
                    if tweet_date.count() > 0:
                        timestamp = tweet_date.get_attribute('title')
                        print(f"🕐 Posted: {timestamp}")

                    # Extract stats (likes, retweets, etc)
                    stats_container = first_tweet.locator('.tweet-stats')
                    if stats_container.count() > 0:
                        stats_text = stats_container.inner_text()
                        print(f"📊 Stats: {stats_text}")

                    browser.close()
                    return {
                        "success": True,
                        "text": text,
                        "timestamp": timestamp if 'timestamp' in locals() else None
                    }
                else:
                    print("⚠️  Found tweets but no tweet-content")
            else:
                print("❌ No tweets found - checking page content...")

                # Save screenshot for debugging
                page.screenshot(path="/home/samshv/stream/Bond/nitter_screenshot.png")
                print("📸 Saved screenshot to: nitter_screenshot.png")

                # Print page title
                print(f"Page title: {page.title()}")

                # Check if bot protection is showing
                if "bot" in page.content().lower():
                    print("🤖 Bot protection detected - may need to wait longer")

        except Exception as e:
            print(f"❌ ERROR: {e}")
            page.screenshot(path="/home/samshv/stream/Bond/nitter_error.png")
            print("📸 Saved error screenshot")

        finally:
            print("🔒 Closing browser...")
            browser.close()

    return {"success": False}

if __name__ == "__main__":
    result = scrape_latest_tweet_nitter()

    if result["success"]:
        print("\n🎉 Nitter scraping with Playwright works!")
    else:
        print("\n⚠️  Scraping failed - check screenshots for debugging")

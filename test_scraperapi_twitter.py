from playwright.sync_api import sync_playwright
import time

SCRAPERAPI_KEY = "25df12af4966b53e9d2d1b9debea9563"

def test_scraperapi_with_twitter():
    """Test ScraperAPI proxy with Twitter scraping"""

    # ScraperAPI proxy configuration
    proxy_server = "http://proxy-server.scraperapi.com:8001"
    proxy_username = "scraperapi"
    proxy_password = SCRAPERAPI_KEY

    print("🔧 Testing ScraperAPI + Playwright on Twitter...")
    print(f"📡 Using proxy: {proxy_server}")
    print(f"🔑 API Key: {SCRAPERAPI_KEY[:10]}...")

    with sync_playwright() as p:
        # Launch browser with ScraperAPI proxy
        browser = p.chromium.launch(
            headless=False,
            proxy={
                "server": proxy_server,
                "username": proxy_username,
                "password": proxy_password
            }
        )

        # Create context with SSL verification disabled for proxy
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            print("\n🐦 Navigating to Elon Musk's profile...")
            page.goto('https://twitter.com/elonmusk', timeout=60000)

            # Wait for tweets to load
            print("⏳ Waiting for tweets to load...")
            page.wait_for_timeout(5000)

            # Get the first tweet (most recent)
            print("🔍 Locating first tweet...")
            first_tweet = page.locator('article[data-testid="tweet"]').first

            # Extract data
            text = first_tweet.locator('[data-testid="tweetText"]').inner_text()
            timestamp = first_tweet.locator('time').get_attribute('datetime')

            print("\n✅ SUCCESS! ScraperAPI is working!")
            print("=" * 60)
            print(f"📝 Tweet Text: {text}")
            print(f"🕐 Posted At: {timestamp}")
            print("=" * 60)

            return {
                "success": True,
                "text": text,
                "timestamp": timestamp
            }

        except Exception as e:
            print(f"\n❌ ERROR: {e}")
            print("\nTrying to get page title to verify connection...")
            try:
                title = page.title()
                print(f"Page title: {title}")
            except:
                print("Could not get page title")

            return {
                "success": False,
                "error": str(e)
            }

        finally:
            print("\n🔒 Closing browser...")
            browser.close()

if __name__ == "__main__":
    result = test_scraperapi_with_twitter()

    if result["success"]:
        print("\n🎉 ScraperAPI test PASSED - ready to build connector!")
    else:
        print("\n⚠️  ScraperAPI test FAILED - check your API key and quota")

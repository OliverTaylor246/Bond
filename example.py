from playwright.sync_api import sync_playwright

def scrape_latest_elon_tweet():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        # Go to Elon's profile
        page.goto('https://twitter.com/elonmusk')
        
        # Wait for tweets to load
        page.wait_for_timeout(3000)
        
        # Get the first tweet (most recent)
        first_tweet = page.locator('article[data-testid="tweet"]').first
        
        # Extract data
        try:
            text = first_tweet.locator('[data-testid="tweetText"]').inner_text()
            timestamp = first_tweet.locator('time').get_attribute('datetime')
            
            print("🐦 Most Recent Tweet:")
            print(f"Text: {text}")
            print(f"Posted: {timestamp}")
            
        except Exception as e:
            print(f"Error: {e}")
        
        browser.close()

scrape_latest_elon_tweet()
import requests
import json

SCRAPERAPI_KEY = "25df12af4966b53e9d2d1b9debea9563"

def test_scraperapi_direct():
    """Test ScraperAPI direct API method (without Playwright)"""

    # ScraperAPI endpoint
    url = "http://api.scraperapi.com"

    params = {
        'api_key': SCRAPERAPI_KEY,
        'url': 'https://twitter.com/elonmusk',
        'render': 'true',  # Enable JavaScript rendering
    }

    print("🔧 Testing ScraperAPI Direct API...")
    print(f"🎯 Target: https://twitter.com/elonmusk")
    print(f"🔑 API Key: {SCRAPERAPI_KEY[:10]}...")
    print("\n⏳ Making request (this may take 10-20 seconds)...\n")

    try:
        response = requests.get(url, params=params, timeout=60)

        print(f"📊 Status Code: {response.status_code}")
        print(f"📦 Response Length: {len(response.text)} bytes")

        if response.status_code == 200:
            html = response.text

            # Check if we got actual Twitter content
            if "elonmusk" in html.lower():
                print("\n✅ SUCCESS! Got Twitter page content")

                # Save HTML for inspection
                with open('/home/samshv/stream/Bond/twitter_scraped.html', 'w') as f:
                    f.write(html)
                print("💾 Saved HTML to: twitter_scraped.html")

                # Try to find tweet text (basic check)
                if 'tweet' in html.lower():
                    print("🐦 Found tweet-related content!")

                return {"success": True, "html_length": len(html)}
            else:
                print("\n⚠️  Got response but doesn't look like Twitter")
                print(f"First 500 chars: {html[:500]}")
                return {"success": False, "error": "Not Twitter content"}
        else:
            print(f"\n❌ Request failed with status {response.status_code}")
            print(f"Response: {response.text[:500]}")
            return {"success": False, "error": f"Status {response.status_code}"}

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    result = test_scraperapi_direct()

    if result["success"]:
        print("\n🎉 ScraperAPI is working! Next step: parse tweets from HTML")
    else:
        print("\n⚠️  ScraperAPI test failed - may need premium tier for Twitter")

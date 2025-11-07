"""Simple test to check if Nitter is accessible"""
import requests
from bs4 import BeautifulSoup

NITTER_INSTANCES = [
    "nitter.poast.org",
    "nitter.net",
    "nitter.privacydev.net",
    "nitter.cz",
    "nitter.unixfox.eu",
    "nitter.1d4.us",
    "nitter.kavin.rocks",
    "nitter.mint.lgbt",
    "nitter.fdn.fr",
    "nitter.it",
]

def test_nitter_access():
    """Test if we can access Nitter instances"""

    # Headers to mimic a real browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }

    for instance in NITTER_INSTANCES:
        url = f"https://{instance}/elonmusk"
        print(f"\n🔍 Testing: {url}")

        try:
            response = requests.get(url, timeout=10, headers=headers)
            print(f"   Status: {response.status_code}")

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'lxml')

                # Try to find tweets
                tweets = soup.find_all('div', class_='timeline-item')
                print(f"   Found {len(tweets)} tweet containers")

                if tweets:
                    # Get first tweet text
                    first_tweet = tweets[0]
                    content = first_tweet.find('div', class_='tweet-content')
                    if content:
                        text = content.get_text(strip=True)
                        print(f"   ✅ Latest tweet: {text[:80]}...")
                        return True
                    else:
                        print(f"   ⚠️  No tweet-content div found")
                else:
                    print(f"   ⚠️  No timeline-item divs found")

                # Save HTML for debugging
                with open(f'/home/samshv/stream/Bond/nitter_{instance.replace(".", "_")}.html', 'w') as f:
                    f.write(response.text)
                print(f"   💾 Saved HTML for inspection")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    return False

if __name__ == "__main__":
    success = test_nitter_access()
    if success:
        print("\n🎉 Nitter is accessible and working!")
    else:
        print("\n⚠️  Could not access Nitter - may need to check instance status")

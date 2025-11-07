"""Debug script to see what HTML Nitter returns"""
import requests

url = "https://nitter.poast.org/elonmusk"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

response = requests.get(url, headers=headers, timeout=10)
print(f"Status: {response.status_code}")
print(f"Content length: {len(response.text)}")

# Save to file
with open('/home/samshv/stream/Bond/nitter_debug.html', 'w') as f:
    f.write(response.text)

print("\nFirst 1000 chars:")
print(response.text[:1000])

# Check for common patterns
if 'timeline-item' in response.text:
    print("\n✅ Found 'timeline-item'")
else:
    print("\n❌ 'timeline-item' NOT found")

if 'tweet-content' in response.text:
    print("✅ Found 'tweet-content'")
else:
    print("❌ 'tweet-content' NOT found")

if 'bot' in response.text.lower() or 'blocked' in response.text.lower():
    print("⚠️  May contain bot detection/blocking")

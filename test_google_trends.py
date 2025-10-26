"""Test Google Trends API for crypto keyword tracking"""
from pytrends.request import TrendReq
import json
from datetime import datetime

# Initialize
pytrends = TrendReq(hl='en-US', tz=360)

# Test 1: Interest over time
print("=" * 60)
print("Google Trends - Interest Over Time")
print("=" * 60)
keywords = ['bitcoin', 'ethereum', 'crypto']
pytrends.build_payload(keywords, timeframe='now 1-d')  # Last 24 hours
interest = pytrends.interest_over_time()

if not interest.empty:
    print(f"\n📊 Search interest (0-100 scale) in last 24h:\n")
    print(interest.tail(5))  # Last 5 data points
else:
    print("No data available")

# Test 2: Trending searches (US)
print("\n" + "=" * 60)
print("Google Trends - Trending Now (US)")
print("=" * 60)
try:
    trending = pytrends.trending_searches(pn='united_states')
    print(f"\n🔥 Top 20 trending searches:\n")
    print(trending.head(20))
except Exception as e:
    print(f"Error: {e}")

# Test 3: Related queries
print("\n" + "=" * 60)
print("Google Trends - Related Queries for 'bitcoin'")
print("=" * 60)
pytrends.build_payload(['bitcoin'], timeframe='now 1-d')
related = pytrends.related_queries()
if related and 'bitcoin' in related:
    print("\n⬆️ Rising queries:")
    print(related['bitcoin']['rising'].head(10) if related['bitcoin']['rising'] is not None else "None")
    print("\n🔝 Top queries:")
    print(related['bitcoin']['top'].head(10) if related['bitcoin']['top'] is not None else "None")

print("\n✅ Google Trends test complete!")

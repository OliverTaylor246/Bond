"""
Nitter polling connector - fetches tweets via Nitter instances.
Uses simple HTTP requests + BeautifulSoup for parsing.
"""
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator
import httpx
from bs4 import BeautifulSoup


# List of public Nitter instances (fallback if one is down)
NITTER_INSTANCES = [
  "nitter.poast.org",
  "nitter.net",
  "nitter.privacydev.net",
]


async def fetch_latest_tweet(username: str, instance: str = None) -> dict | None:
  """
  Fetch the most recent tweet from a user via Nitter.

  Args:
    username: Twitter username (without @)
    instance: Nitter instance to use (default: first in list)

  Returns:
    Dict with tweet data or None if failed
  """
  if instance is None:
    instance = NITTER_INSTANCES[0]

  url = f"https://{instance}/{username}"

  try:
    async with httpx.AsyncClient(timeout=10.0) as client:
      response = await client.get(url, follow_redirects=True)

      if response.status_code != 200:
        print(f"[nitter] HTTP {response.status_code} from {instance}")
        return None

      soup = BeautifulSoup(response.text, 'lxml')

      # Find first tweet container
      tweet_div = soup.find('div', class_='timeline-item')

      if not tweet_div:
        print(f"[nitter] No tweets found for @{username}")
        return None

      # Extract tweet text
      tweet_content = tweet_div.find('div', class_='tweet-content')
      text = tweet_content.get_text(strip=True) if tweet_content else ""

      # Extract timestamp
      tweet_date = tweet_div.find('span', class_='tweet-date')
      timestamp_elem = tweet_date.find('a') if tweet_date else None
      timestamp_str = timestamp_elem.get('title') if timestamp_elem else None

      # Extract tweet stats (likes, retweets, etc.)
      stats = {}
      tweet_stats = tweet_div.find('div', class_='tweet-stats')
      if tweet_stats:
        stat_items = tweet_stats.find_all('span', class_='tweet-stat')
        for item in stat_items:
          icon = item.find('span', class_='icon')
          value = item.find('div', class_='icon-text')
          if icon and value:
            stat_type = icon.get('class', [])
            stat_value = value.get_text(strip=True)
            # Parse stat type from icon class
            if 'icon-comment' in stat_type:
              stats['replies'] = stat_value
            elif 'icon-retweet' in stat_type:
              stats['retweets'] = stat_value
            elif 'icon-heart' in stat_type:
              stats['likes'] = stat_value

      return {
        'username': username,
        'text': text,
        'timestamp': timestamp_str,
        'stats': stats,
        'source_instance': instance,
        'fetched_at': datetime.now(tz=timezone.utc).isoformat()
      }

  except Exception as e:
    print(f"[nitter] Error fetching from {instance}: {e}")
    return None


async def nitter_poll_stream(
  username: str = "elonmusk",
  interval: int = 1
) -> AsyncIterator[dict]:
  """
  Continuously poll Nitter for latest tweet from a user.

  Args:
    username: Twitter username (without @)
    interval: Polling interval in seconds

  Yields:
    Tweet event dictionaries
  """
  last_tweet_text = None
  instance_idx = 0

  print(f"[nitter] Starting poll stream for @{username} (interval: {interval}s)")

  while True:
    # Rotate instances if one fails
    instance = NITTER_INSTANCES[instance_idx % len(NITTER_INSTANCES)]

    tweet = await fetch_latest_tweet(username, instance)

    if tweet:
      # Only yield if it's a new tweet (text changed)
      if tweet['text'] != last_tweet_text:
        print(f"[nitter] New tweet from @{username}: {tweet['text'][:50]}...")

        # Create event in Bond format
        evt = {
          'ts': datetime.now(tz=timezone.utc),
          'source': f'nitter:{username}',
          'symbol': username.upper(),  # Treat username as symbol
          'text': tweet['text'],
          'timestamp_posted': tweet['timestamp'],
          'stats': tweet['stats'],
          'instance': tweet['source_instance']
        }

        yield evt
        last_tweet_text = tweet['text']
      else:
        # Same tweet as before, no update
        pass
    else:
      # Failed to fetch, try next instance
      print(f"[nitter] Failed to fetch from {instance}, trying next...")
      instance_idx += 1

    await asyncio.sleep(interval)


async def nitter_poll_multi_users(
  usernames: list[str] = None,
  interval: int = 5
) -> AsyncIterator[dict]:
  """
  Poll multiple Twitter users simultaneously.

  Args:
    usernames: List of usernames to track
    interval: Polling interval in seconds

  Yields:
    Tweet event dictionaries from any tracked user
  """
  if usernames is None:
    usernames = ["elonmusk", "vitalikbuterin", "cz_binance"]

  print(f"[nitter] Tracking users: {usernames}")

  last_tweets = {user: None for user in usernames}

  while True:
    for username in usernames:
      tweet = await fetch_latest_tweet(username)

      if tweet and tweet['text'] != last_tweets[username]:
        print(f"[nitter] New tweet from @{username}")

        evt = {
          'ts': datetime.now(tz=timezone.utc),
          'source': f'nitter:{username}',
          'symbol': username.upper(),
          'text': tweet['text'],
          'timestamp_posted': tweet['timestamp'],
          'stats': tweet['stats']
        }

        yield evt
        last_tweets[username] = tweet['text']

    await asyncio.sleep(interval)


# Test function
async def test_nitter():
  """Quick test of Nitter scraping"""
  print("Testing Nitter connector...")

  async for tweet in nitter_poll_stream(username="elonmusk", interval=2):
    print(f"\n{'='*60}")
    print(f"Tweet: {tweet['text'][:100]}")
    print(f"Posted: {tweet['timestamp_posted']}")
    print(f"Stats: {tweet['stats']}")
    print(f"{'='*60}\n")

    # Stop after first tweet for testing
    break


if __name__ == "__main__":
  asyncio.run(test_nitter())

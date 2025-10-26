"""
Google Trends streaming connector.
Tracks search interest for keywords over time using pytrends.
"""
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
from pytrends.request import TrendReq


async def google_trends_stream(
    keywords: list[str],
    interval: int = 300,
    timeframe: str = "now 1-H"
) -> AsyncIterator[dict]:
    """
    Stream Google Trends search interest data for keywords.

    Args:
        keywords: List of keywords to track (max 5 per API request)
        interval: Seconds between updates (default 300 = 5 minutes)
        timeframe: Google Trends timeframe string
                   - 'now 1-H' = last hour
                   - 'now 4-H' = last 4 hours
                   - 'now 1-d' = last 24 hours
                   - 'now 7-d' = last 7 days
                   - 'today 1-m' = last 30 days

    Yields:
        Dict with search interest data:
        {
            'ts': timestamp,
            'keyword': keyword,
            'interest': 0-100 score,
            'source': 'google_trends',
            'timeframe': timeframe,
            'related_rising': [...],  # Breakout queries
            'related_top': [...]      # Most popular queries
        }
    """
    print(f"[google_trends] Starting stream for keywords: {keywords}", flush=True)
    print(f"[google_trends] Timeframe: {timeframe}, Interval: {interval}s", flush=True)

    # Limit to 5 keywords (Google Trends API limit)
    if len(keywords) > 5:
        print(f"[google_trends] Warning: Max 5 keywords allowed, truncating to first 5", flush=True)
        keywords = keywords[:5]

    # Note: retries/backoff_factor parameters may cause issues with newer urllib3
    # Using minimal config for compatibility
    pytrends = TrendReq(hl='en-US', tz=360)

    while True:
        try:
            # Build payload for keywords
            pytrends.build_payload(keywords, timeframe=timeframe)

            # Get interest over time
            interest_df = pytrends.interest_over_time()

            if not interest_df.empty:
                # Get most recent data point
                latest = interest_df.iloc[-1]
                timestamp = datetime.now(timezone.utc)

                # Yield data for each keyword
                for keyword in keywords:
                    if keyword in latest:
                        interest_score = int(latest[keyword])

                        evt = {
                            'ts': timestamp,
                            'keyword': keyword,
                            'interest': interest_score,
                            'source': 'google_trends',
                            'timeframe': timeframe,
                            'is_partial': bool(latest.get('isPartial', False))
                        }

                        print(f"[google_trends] {keyword}: {interest_score}/100 interest", flush=True)
                        yield evt

                # Get related queries for first keyword (to avoid rate limits)
                if keywords:
                    try:
                        related = pytrends.related_queries()
                        main_keyword = keywords[0]

                        if related and main_keyword in related:
                            rising = related[main_keyword]['rising']
                            top = related[main_keyword]['top']

                            # Yield related queries as separate event
                            related_evt = {
                                'ts': timestamp,
                                'keyword': main_keyword,
                                'source': 'google_trends_related',
                                'timeframe': timeframe,
                                'related_rising': rising.to_dict('records') if rising is not None else [],
                                'related_top': top.to_dict('records') if top is not None else []
                            }

                            if rising is not None and not rising.empty:
                                print(f"[google_trends] {main_keyword} breakout queries: {len(rising)}", flush=True)

                            yield related_evt
                    except Exception as e:
                        print(f"[google_trends] Error fetching related queries: {e}", flush=True)

            else:
                print(f"[google_trends] No data returned for {keywords}", flush=True)

        except Exception as e:
            print(f"[google_trends] Error: {e}", flush=True)
            # Yield error event to keep stream alive
            yield {
                'ts': datetime.now(timezone.utc),
                'keyword': keywords[0] if keywords else 'unknown',
                'source': 'google_trends',
                'error': str(e)
            }

        # Wait before next update
        await asyncio.sleep(interval)


async def google_trends_single(
    keywords: list[str],
    timeframe: str = "now 1-H"
) -> dict:
    """
    Single snapshot of Google Trends data (non-streaming).

    Args:
        keywords: List of keywords (max 5)
        timeframe: Time range to query

    Returns:
        Dict with interest data for all keywords
    """
    if len(keywords) > 5:
        keywords = keywords[:5]

    # Note: retries/backoff_factor parameters may cause issues with newer urllib3
    # Using minimal config for compatibility
    pytrends = TrendReq(hl='en-US', tz=360)
    pytrends.build_payload(keywords, timeframe=timeframe)

    interest_df = pytrends.interest_over_time()

    result = {
        'timestamp': datetime.now(timezone.utc),
        'timeframe': timeframe,
        'keywords': {}
    }

    if not interest_df.empty:
        latest = interest_df.iloc[-1]
        for keyword in keywords:
            if keyword in latest:
                result['keywords'][keyword] = int(latest[keyword])

    return result

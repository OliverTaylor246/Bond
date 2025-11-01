"""Test the natural language parser with DeepSeek."""

import asyncio
import os
from apps.api.nlp_parser import parse_stream_request

async def test_queries():
    api_key = "sk-82113664eaea4eb78ecb0df7c937e875"

    test_cases = [
        "show me live btc prices",
        "bitcoin from kraken with volume",
        "eth from all exchanges",
        "solana prices right now",
    ]

    for query in test_cases:
        print(f"\n{'='*60}")
        print(f"QUERY: {query}")
        print('='*60)

        try:
            result = await parse_stream_request(query, api_key)
            print(f"\nSymbols: {result['symbols']}")
            print(f"Exchanges: {result['exchanges']}")
            print(f"Interval: {result['interval_sec']}s")
            print(f"Additional Sources: {result.get('additional_sources', [])}")
            print(f"\nReasoning: {result['reasoning']}")
        except Exception as e:
            print(f"ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_queries())

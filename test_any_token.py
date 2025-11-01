"""Test that LLM can handle any cryptocurrency symbol, not just popular ones."""

import asyncio
import json
import httpx

async def test_parse(query: str, api_key: str):
    """Test parsing a natural language query."""

    AVAILABLE_OPTIONS = {
        "symbols": "any cryptocurrency symbol (e.g., BTC, ETH, SOL, AVAX, MATIC, LINK, UNI, DOGE, ADA, DOT, AAVE, CRV, SUSHI, COMP, MKR, YFI, SNX, etc.)",
        "popular_symbols": ["BTC", "ETH", "SOL", "AVAX", "MATIC", "LINK", "UNI", "DOGE", "ADA", "DOT", "AAVE", "CRV", "SUSHI", "COMP", "MKR", "YFI", "SNX", "LTC", "XRP", "ATOM", "ALGO", "FIL", "SAND", "MANA", "AXS"],
        "exchanges": ["kraken", "kucoin"],
        "fields": ["price", "bid", "ask", "high", "low", "open", "close", "volume"],
        "sources": ["twitter", "onchain", "liquidations", "google_trends"],
    }

    prompt = f"""You are a financial data stream configuration assistant. Parse the user's request into a structured stream configuration.

Available options:
- Symbols: {AVAILABLE_OPTIONS['symbols']}
- Popular symbols: {', '.join(AVAILABLE_OPTIONS['popular_symbols'])}
- Exchanges: {', '.join(AVAILABLE_OPTIONS['exchanges'])}
- Fields: {', '.join(AVAILABLE_OPTIONS['fields'])}
- Additional sources: {', '.join(AVAILABLE_OPTIONS['sources'])}

User request: "{query}"

Return ONLY a JSON object with this structure:
{{
  "symbols": ["BTC"],
  "exchanges": [
    {{"exchange": "kraken", "fields": ["price", "volume"]}}
  ],
  "additional_sources": [],
  "interval_sec": 1.0,
  "reasoning": "Brief explanation of choices"
}}

Rules:
1. You can use ANY cryptocurrency symbol (BTC, ETH, LINK, AAVE, CRV, etc.) - not limited to the popular list
2. If user says "all crypto" or "all cryptocurrencies" or "all available" -> use the popular symbols list: {', '.join(AVAILABLE_OPTIONS['popular_symbols'])}
3. If user doesn't specify exchange, use ALL available exchanges
4. If user doesn't specify fields, use ["price", "volume"] as defaults
5. If user says "realtime" or "fast" or "fastest", use interval 0.1
6. If user says "live", "right now", "current" -> use 1 second interval
7. If user mentions "twitter" or "social", add twitter source
8. If user mentions "search interest" or "trends", add google_trends source
9. Extract ALL mentioned cryptocurrencies (bitcoin=BTC, ethereum=ETH, chainlink=LINK, etc.)
10. Be generous - if user says "show me bitcoin", include price, volume, high, low
11. "fastest refresh rate" or "fastest possible" means interval 0.1 seconds
12. Convert all cryptocurrency names to uppercase ticker symbols (e.g., "bitcoin" -> "BTC", "ethereum" -> "ETH")

Return valid JSON only, no markdown formatting."""

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 1024
            }
        )

        response.raise_for_status()
        result = response.json()
        response_text = result["choices"][0]["message"]["content"].strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        return json.loads(response_text)

async def main():
    api_key = "sk-82113664eaea4eb78ecb0df7c937e875"

    test_queries = [
        # Specific tokens NOT in popular list
        "show me PEPE prices",
        "I want SHIB and BONK data from kucoin",
        "give me all crypto live prices from kucoin with the fastest refresh rate possible",
    ]

    for query in test_queries:
        print(f"\n{'='*70}")
        print(f"QUERY: {query}")
        print('='*70)

        try:
            result = await test_parse(query, api_key)
            print(f"\n✓ Symbols ({len(result['symbols'])}): {result['symbols']}")
            print(f"✓ Exchanges: {[e['exchange'] for e in result['exchanges']]}")
            print(f"✓ Interval: {result['interval_sec']}s")
            print(f"\n💡 Reasoning: {result['reasoning']}")
        except Exception as e:
            print(f"✗ ERROR: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())

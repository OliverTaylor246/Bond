"""Natural language parser for stream configuration using DeepSeek LLM."""

import json
import httpx
from typing import Dict, Any

AVAILABLE_OPTIONS = {
    "symbols": "any cryptocurrency symbol (e.g., BTC, ETH, SOL, AVAX, MATIC, LINK, UNI, DOGE, ADA, DOT, AAVE, CRV, SUSHI, COMP, MKR, YFI, SNX, etc.)",
    "popular_symbols": ["BTC", "ETH", "SOL"],  # Limited to top 3 - broker can't handle more concurrent WebSocket streams
    "exchanges": ["binanceus", "binance", "kraken", "kucoin"],
    "fields": ["price", "bid", "ask", "high", "low", "open", "close", "volume"],
    "sources": ["twitter", "onchain", "liquidations", "google_trends", "nitter"],
    "twitter_users": ["elonmusk", "vitalikbuterin", "cz_binance", "SBF_FTX", "APompliano"],
    "intervals": {
        "realtime": 0.5,
        "fast": 1,
        "normal": 3,
        "slow": 5
    }
}


async def parse_stream_request(user_input: str, api_key: str) -> Dict[str, Any]:
    """
    Parse natural language into stream configuration using DeepSeek.

    Example:
        Input: "show me live btc prices"
        Output: {
            "symbols": ["BTC"],
            "exchanges": [{"exchange": "binanceus", "fields": ["price", "volume"]},
                         {"exchange": "binance", "fields": ["price", "volume"]}],
            "interval_sec": 1.0,
            "reasoning": "User wants live Bitcoin prices, using all exchanges with default fields"
        }
    """

    prompt = f"""You are a financial data stream configuration assistant. Parse the user's request into a structured stream configuration.

Available options:
- Symbols: {AVAILABLE_OPTIONS['symbols']}
- Popular symbols: {', '.join(AVAILABLE_OPTIONS['popular_symbols'])}
- Exchanges: {', '.join(AVAILABLE_OPTIONS['exchanges'])}
- Fields: {', '.join(AVAILABLE_OPTIONS['fields'])}
- Additional sources: {', '.join(AVAILABLE_OPTIONS['sources'])}
- Twitter users for Nitter: {', '.join(AVAILABLE_OPTIONS['twitter_users'])}

User request: "{user_input}"

Return ONLY a JSON object with this structure:
{{
  "symbols": ["BTC"],
  "exchanges": [
    {{"exchange": "binanceus", "fields": ["price", "volume"]}}
  ],
  "additional_sources": [],
  "interval_sec": 1.0,
  "reasoning": "Brief explanation of choices"
}}

Rules:
1. You can use ANY cryptocurrency symbol (BTC, ETH, LINK, AAVE, CRV, etc.) - not limited to the popular list
2. If user says "all crypto" or "all cryptocurrencies" or "all available" -> use the popular symbols list: {', '.join(AVAILABLE_OPTIONS['popular_symbols'])}
3. If user doesn't specify exchange, use Binance US
4. If user doesn't specify fields, use ["price", "volume"] as defaults
5. If user says "realtime" or "fast" or "fastest", use interval 0.1
6. If user says "live", "right now", "current" -> use 1 second interval
7. If user mentions "twitter" or "social", add twitter source
8. If user mentions "search interest" or "trends", add google_trends source
9. Extract ALL mentioned cryptocurrencies (bitcoin=BTC, ethereum=ETH, chainlink=LINK, etc.)
10. Be generous - if user says "show me bitcoin", include price, volume, high, low
11. "fastest refresh rate" or "fastest possible" means interval 0.1 seconds
12. Convert all cryptocurrency names to uppercase ticker symbols (e.g., "bitcoin" -> "BTC", "ethereum" -> "ETH")
13. If user mentions "tweets" OR specific Twitter usernames (elonmusk, vitalik, etc.) OR "@username" -> add nitter source with username
14. For Nitter sources, format as: {{"type": "nitter", "username": "elonmusk", "interval_sec": X}}
15. Default Twitter users: elonmusk, vitalikbuterin, cz_binance
16. If user says "Elon tweets" or "Elon Musk tweets" -> add nitter source with username="elonmusk"
17. IMPORTANT: If user ONLY asks for tweets/social data (no crypto mentioned), DO NOT add exchanges or symbols - return empty symbols array and empty exchanges array
18. If user asks ONLY for tweets, only include the nitter source in additional_sources, no CCXT sources

Return valid JSON only, no markdown formatting."""

    print("[nlp_parser] Prompt submitted:", user_input, flush=True)

    request_payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 1024
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        print(
            "[nlp_parser] API call -> POST https://api.deepseek.com/v1/chat/completions",
            flush=True,
        )
        response = await client.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=request_payload
        )

        response.raise_for_status()
        result = response.json()

        print("[nlp_parser] API response:", result, flush=True)

        response_text = result["choices"][0]["message"]["content"].strip()

        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        config = json.loads(response_text)
        print("[nlp_parser] Parsed configuration:", config, flush=True)
        return config

"""Natural language parser for stream configuration using LangChain + DeepSeek."""

from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

AVAILABLE_OPTIONS = {
    "symbols": "any cryptocurrency symbol (e.g., BTC, ETH, SOL, AVAX, MATIC, LINK, UNI, DOGE, ADA, DOT, AAVE, CRV, SUSHI, COMP, MKR, YFI, SNX, etc.)",
    "popular_symbols": ["BTC", "ETH", "SOL"],  # Limited to top 3 - broker can't handle more concurrent WebSocket streams
    "exchanges": ["binanceus", "binance", "kraken", "kucoin"],
    "fields": ["price", "bid", "ask", "high", "low", "open", "close", "volume"],
    "sources": ["twitter", "onchain", "liquidations", "google_trends", "nitter", "polymarket"],
    "twitter_users": ["elonmusk", "vitalikbuterin", "cz_binance", "SBF_FTX", "APompliano"],
    "intervals": {
        "realtime": 0.5,
        "fast": 1,
        "normal": 3,
        "slow": 5
    }
}

_FORMATTER = JsonOutputParser()

_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """You are a financial data stream configuration assistant that maps user goals \
into a structured JSON object. Use only the provided catalog of options.

Available options:
{available_options}

Existing stream spec context (use as baseline when provided):
{current_context}

You have two response modes:

**Mode 1: Conversational (when request is unclear or just greeting/chat)**
Return this schema:
{{
  "mode": "conversation",
  "message": "your helpful conversational response here"
}}

Use this mode when:
- User is greeting you (hello, hi, hey)
- Request is too vague to create a stream
- User is asking what you can do
- User needs guidance on available data sources

In your message, be helpful and guide them toward the data sources you can access:
- Exchanges: binanceus, binance, kraken, kucoin
- Cryptocurrency prices, volume, and market data
- Polymarket prediction markets
- Solana DEX data via Jupiter
- Twitter mentions and sentiment
- On-chain data
- Google Trends
- Liquidation feeds

**Mode 2: Stream Specification (when request is clear enough)**
Return this schema:
{{
  "mode": "stream_spec",
  "symbols": ["BTC", "ETH"],
  "exchanges": [
    {{"exchange": "binanceus", "fields": ["price", "volume", "high", "low"]}}
  ],
  "additional_sources": [
    "twitter",
    "google_trends",
    {{"type": "nitter", "username": "elonmusk", "interval_sec": 1.0}}
  ],
  "interval_sec": 1.0,
  "reasoning": "short natural language explanation",
  "confidence": 0.95
}}

Rules:
{rules}

Return ONLY JSON in the appropriate schema above. Choose conversation mode for greetings/unclear requests, stream_spec mode for clear data requests.

Output requirements:
{format_instructions}
""",
    ),
    (
        "human",
        "{user_request}",
    ),
])

_RULES = """**For stream_spec mode:**
1. Use any cryptocurrency ticker the user mentions (convert names to uppercase symbols).
2. "All crypto" style requests should map to the popular symbols list.
3. Binance US is the default exchange and ["price", "volume"] the default fields unless specified.
4. Interpret refresh hints: "realtime"/"fastest" -> 0.1, "live"/"right now"/"current" -> 1 second.
5. Mentioning "twitter"/"social" adds twitter source; "search interest"/"trends" adds google_trends.
6. Detect all crypto names mentioned (bitcoin -> BTC, ethereum -> ETH, etc.).
7. Single-asset asks should include price, volume, high, low fields.
8. Handle "fastest refresh rate"/"fastest possible" as 0.1 seconds.
9. If tweets or @handles are requested, add a nitter source with the username.
10. Set "confidence" field (0.0-1.0) based on:
    - 0.9-1.0: Clear, specific request with known symbols/sources
    - 0.7-0.9: Reasonable interpretation but some ambiguity
    - 0.5-0.7: Vague request, multiple interpretations possible
    - Below 0.5: Unclear request, missing critical information
11. Default Twitter handles: elonmusk, vitalikbuterin, cz_binance.
12. If user only wants tweets/social data, leave symbols/exchanges empty and only return the relevant sources.
13. When an existing stream spec is provided, start from it and only change the parts the user requested—preserve other symbols, sources, and intervals.
14. Treat additive requests ("add tweets", "include Google Trends") as augmentations unless the user explicitly asks to replace or remove sources.
15. Mentioning "Polymarket", "prediction markets", or "event odds" adds a polymarket source (track all events by default unless the user specifies filters).

**For conversation mode:**
1. Use this mode for greetings (hello, hi, hey), vague requests, or questions about capabilities.
2. Be conversational and helpful - guide the user toward making a specific data stream request.
3. Suggest examples like "track BTC price", "show me Elon's tweets", "Polymarket election odds".
4. Keep messages concise and actionable.
"""

_AVAILABLE_OPTIONS_TEXT = f"""- Symbols: {AVAILABLE_OPTIONS['symbols']}
- Popular symbols: {', '.join(AVAILABLE_OPTIONS['popular_symbols'])}
- Exchanges: {', '.join(AVAILABLE_OPTIONS['exchanges'])}
- Fields: {', '.join(AVAILABLE_OPTIONS['fields'])}
- Additional sources: {', '.join(AVAILABLE_OPTIONS['sources'])}
- Twitter users: {', '.join(AVAILABLE_OPTIONS['twitter_users'])}
- Interval presets: {', '.join(f"{k}={v}s" for k, v in AVAILABLE_OPTIONS['intervals'].items())}"""


def _format_current_spec(spec: Dict[str, Any] | None) -> str:
    if not spec:
        return "None provided. Start from a blank configuration."
    try:
        return json.dumps(spec, indent=2)
    except (TypeError, ValueError):
        return str(spec)


async def parse_stream_request(
    user_input: str,
    api_key: str,
    *,
    current_spec: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Parse natural language into stream configuration using a LangChain pipeline.
    """

    print("[nlp_parser] Prompt submitted:", user_input, flush=True)

    llm = ChatOpenAI(
        model="deepseek-chat",
        temperature=0.3,
        max_tokens=1024,
        api_key=api_key,
        base_url="https://api.deepseek.com/v1",
        timeout=30,
    )

    prompt = _PROMPT.partial(
        available_options=_AVAILABLE_OPTIONS_TEXT,
        rules=_RULES,
        format_instructions=_FORMATTER.get_format_instructions(),
        current_context=_format_current_spec(current_spec),
    )

    chain = prompt | llm | _FORMATTER

    config = await chain.ainvoke({"user_request": user_input})
    print("[nlp_parser] Parsed configuration:", config, flush=True)
    return config

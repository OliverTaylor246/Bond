"""
Rule-based multi-agent planner that builds StreamSpec fragments
from user intent and merges them into a final specification.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from apps.api.jupiter import (
  pick_best_token as pick_best_jupiter_token,
  search_tokens as search_jupiter_tokens,
)
from apps.api.polymarket import (
  build_polymarket_spec,
  extract_polymarket_query,
)
from apps.api.spec_builder import build_spec_from_parsed_config, normalize_symbol
from apps.api.nlp_parser import parse_stream_request
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

SOLANA_MINT_REGEX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

DEFAULT_CONVERSATION_MESSAGE = (
  "I build real-time crypto data streams (prices, tweets, Polymarket, on-chain). "
  "Tell me which symbols or sources you'd like me to track."
)
DEFAULT_CLARIFICATION_MESSAGE = (
  "Can you clarify which assets or data sources you want to monitor so I can set up the right stream?"
)

INTENT_PROMPT = ChatPromptTemplate.from_messages([
  (
    "system",
    """You are the intent-classification agent for the 3KK0 market data assistant.\n"
    "Decide whether the user's latest message should be treated as a conversation, a clarification request, or a stream action.\n"
    "\n"
    "Definitions:\n"
    "- conversation: greetings, small talk, math questions, or any request unrelated to configuring data streams.\n"
    "- clarification: the user wants stream data but their instructions are incomplete or ambiguous.\n"
    "- action: a clear instruction to create, update, or inspect data streams (symbols, intervals, sources, etc.).\n"
    "\n"
    "If the mode is conversation or clarification, craft a short message (<50 words) responding appropriately.\n"
    "For conversation: be friendly and remind them what you can do.\n"
    "For clarification: politely ask the missing details (symbols, timeframe, sources, etc.).\n"
    "For action: no message is required.\n"
    "\n"
    "Return ONLY JSON in this schema:\n"
    "{\n"
    "  \"mode\": \"conversation|clarification|action\",\n"
    "  \"message\": \"string (required for conversation/clarification, optional for action)\"\n"
    "}\n"
    """,
  ),
  (
    "human",
    "Current stream context (if any):\n{current_context}\n\n"
    "User message: {user_text}",
  ),
])


@dataclass
class PlannerContext:
  user_text: str
  api_key: str
  current_spec: dict[str, Any] | None = None
  metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
  name: str
  handled: bool
  spec: dict[str, Any] | None = None
  reasoning: str = ""
  confidence: float = 1.0  # 0.0 to 1.0, where 1.0 is fully confident
  metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlannerResult:
  handled: bool
  spec: dict[str, Any] | None
  reasoning: list[str]
  agent_outputs: list[AgentOutput]
  confidence: float = 1.0  # Overall confidence (minimum of all agents)
  needs_confirmation: bool = False  # True if confidence < threshold
  conversation_message: str | None = None  # Set when LLM chooses conversational mode


class BaseAgent:
  name = "agent"

  async def run(self, ctx: PlannerContext) -> AgentOutput:  # pragma: no cover - interface
    raise NotImplementedError


def _format_context(spec: dict[str, Any] | None, limit: int = 1200) -> str:
  if not spec:
    return "No active stream."
  try:
    text = json.dumps(spec, indent=2)
  except (TypeError, ValueError):
    text = str(spec)
  if len(text) <= limit:
    return text
  return text[: limit - 3] + "..."


class IntentAgent(BaseAgent):
  name = "intent"

  async def run(self, ctx: PlannerContext) -> AgentOutput:
    user_text = (ctx.user_text or "").strip()
    if not user_text:
      return AgentOutput(
        self.name,
        handled=False,
        spec=None,
        reasoning="Empty prompt treated as conversation",
        metadata={
          "conversation_message": DEFAULT_CONVERSATION_MESSAGE,
          "intent_mode": "conversation",
        },
      )

    parser = JsonOutputParser()
    llm = ChatOpenAI(
      model="deepseek-chat",
      temperature=0.0,
      max_tokens=256,
      api_key=ctx.api_key,
      base_url="https://api.deepseek.com/v1",
      timeout=20,
    )
    chain = INTENT_PROMPT.partial(current_context=_format_context(ctx.current_spec)) | llm | parser

    try:
      result = await chain.ainvoke({"user_text": user_text})
    except Exception as exc:  # pragma: no cover - intent fallback
      return AgentOutput(
        self.name,
        handled=False,
        reasoning=f"intent agent error: {exc}",
        metadata={"intent_mode": "action"},
      )

    mode = str(result.get("mode", "action")).strip().lower()
    message = (result.get("message") or "").strip()

    if mode == "conversation":
      if not message:
        message = DEFAULT_CONVERSATION_MESSAGE
      return AgentOutput(
        self.name,
        handled=False,
        spec=None,
        reasoning="conversation response",
        metadata={
          "conversation_message": message,
          "intent_mode": "conversation",
        },
      )

    if mode == "clarification":
      if not message:
        message = DEFAULT_CLARIFICATION_MESSAGE
      return AgentOutput(
        self.name,
        handled=False,
        spec=None,
        reasoning="clarification needed",
        metadata={
          "conversation_message": message,
          "intent_mode": "clarification",
        },
      )

    return AgentOutput(
      self.name,
      handled=False,
      spec=None,
      reasoning="actionable request",
      metadata={"intent_mode": "action"},
    )


class DexAgent(BaseAgent):
  name = "dex"

  async def run(self, ctx: PlannerContext) -> AgentOutput:
    mint = _detect_solana_mint(ctx.user_text)
    if not mint:
      return AgentOutput(self.name, handled=False)

    tokens = await search_jupiter_tokens(mint, limit=5)
    if not tokens:
      return AgentOutput(self.name, handled=False)

    summary = next((token for token in tokens if token.get("id") == mint), None)
    reason = "provided_contract_match"
    if not summary:
      summary, reason = pick_best_jupiter_token(tokens)
    if not summary:
      return AgentOutput(self.name, handled=False)

    spec = _build_jupiter_spec_from_summary(summary)
    reasoning = f"Jupiter mint {mint} ({summary.get('symbol') or 'token'}) selected [{reason}]."
    return AgentOutput(self.name, handled=True, spec=spec, reasoning=reasoning, metadata={"mint": mint})


class PolymarketAgent(BaseAgent):
  name = "polymarket"

  async def run(self, ctx: PlannerContext) -> AgentOutput:
    query, categories = extract_polymarket_query(ctx.user_text)
    if not query:
      return AgentOutput(self.name, handled=False)

    spec = build_polymarket_spec(query, categories).model_dump()
    reasoning = f"Polymarket request detected for '{query}'."
    return AgentOutput(self.name, handled=True, spec=spec, reasoning=reasoning, metadata={"query": query})


class ExchangeAgent(BaseAgent):
  """
  Fallback agent that leverages the existing LangChain parser to build the
  core CCXT/social spec when no specialized agent fully handles the request.
  """

  name = "exchange"

  def __init__(self, parse_fn: Callable[..., Awaitable[dict[str, Any]]]):
    self._parse_fn = parse_fn

  async def run(self, ctx: PlannerContext) -> AgentOutput:
    config = await self._parse_fn(ctx.user_text, ctx.api_key, current_spec=ctx.current_spec)

    # Check if LLM chose conversational mode
    mode = config.get("mode", "stream_spec")
    if mode == "conversation":
      return AgentOutput(
        self.name,
        handled=False,  # Not handling as a stream request
        spec=None,
        reasoning="Conversational response",
        confidence=1.0,
        metadata={"conversation_message": config.get("message", "")},
      )

    # Stream spec mode
    spec = build_spec_from_parsed_config(config)
    confidence = float(config.get("confidence", 1.0))
    return AgentOutput(
      self.name,
      handled=bool(spec.get("sources")),
      spec=spec,
      reasoning=config.get("reasoning", "Exchange agent produced base spec."),
      confidence=confidence,
      metadata={"parsed_config": config},
    )


async def run_multi_agent_planner(
  user_text: str,
  *,
  api_key: str,
  current_spec: dict[str, Any] | None = None,
) -> PlannerResult:
  """
  Execute planner agents and merge their spec fragments.
  """
  if not user_text:
    return PlannerResult(False, None, [], [])

  ctx = PlannerContext(user_text=user_text, api_key=api_key, current_spec=current_spec)
  agents: list[BaseAgent] = [
    IntentAgent(),
    DexAgent(),
    PolymarketAgent(),
    ExchangeAgent(parse_stream_request),
  ]

  fragments: list[dict[str, Any]] = []
  reasoning: list[str] = []
  outputs: list[AgentOutput] = []
  conversation_message: str | None = None

  for agent in agents:
    try:
      result = await agent.run(ctx)
    except Exception as exc:  # pragma: no cover - defensive
      outputs.append(AgentOutput(agent.name, handled=False, reasoning=f"error: {exc}"))
      continue

    outputs.append(result)

    # Check if this is a conversational response
    if result.metadata.get("conversation_message"):
      conversation_message = result.metadata["conversation_message"]
      # Return immediately in conversation mode
      return PlannerResult(
        handled=False,
        spec=None,
        reasoning=[],
        agent_outputs=outputs,
        confidence=1.0,
        needs_confirmation=False,
        conversation_message=conversation_message,
      )

    if result.handled and result.spec:
      fragments.append(result.spec)
      if result.reasoning:
        reasoning.append(result.reasoning)

  combined = _merge_specs(current_spec, fragments)
  handled = bool(fragments) and bool(combined.get("sources"))

  # Calculate overall confidence (minimum of all handled agents)
  confidence_scores = [output.confidence for output in outputs if output.handled]
  overall_confidence = min(confidence_scores) if confidence_scores else 1.0

  # Set confirmation threshold (e.g., 0.8)
  CONFIDENCE_THRESHOLD = 0.8
  needs_confirmation = overall_confidence < CONFIDENCE_THRESHOLD

  return PlannerResult(
    handled=handled,
    spec=combined if combined.get("sources") else None,
    reasoning=reasoning,
    agent_outputs=outputs,
    confidence=overall_confidence,
    needs_confirmation=needs_confirmation,
    conversation_message=None,
  )


def _merge_specs(base: dict[str, Any] | None, fragments: Iterable[dict[str, Any]]) -> dict[str, Any]:
  combined = copy.deepcopy(base) if base else {"sources": [], "symbols": [], "interval_sec": None}
  combined.setdefault("sources", [])
  combined.setdefault("symbols", [])

  for fragment in fragments:
    for source in fragment.get("sources", []):
      if source not in combined["sources"]:
        combined["sources"].append(source)

    for symbol in fragment.get("symbols", []):
      normalized = normalize_symbol(symbol)
      if normalized and normalized not in combined["symbols"]:
        combined["symbols"].append(normalized)

    interval = fragment.get("interval_sec")
    if interval:
      if not combined.get("interval_sec"):
        combined["interval_sec"] = interval
      else:
        combined["interval_sec"] = min(combined["interval_sec"], interval)

  if not combined.get("interval_sec"):
    combined["interval_sec"] = 3.0

  return combined


def _detect_solana_mint(text: str | None) -> str | None:
  if not text:
    return None
  match = SOLANA_MINT_REGEX.search(text.strip())
  return match.group(0) if match else None


def _derive_pair_symbol(raw_symbol: str | None, mint: str) -> str:
  token = (raw_symbol or "").replace("$", "").upper()
  if not token:
    token = mint[:6].upper() if len(mint) >= 6 else mint.upper()
  if "/" not in token:
    token = f"{token}/USDC"
  return token


def _build_jupiter_spec_from_summary(
  summary: dict[str, Any],
  *,
  interval_sec: float = 2.0,
) -> dict[str, Any]:
  mint = summary.get("id")
  if not mint:
    raise ValueError("Jupiter summary missing mint id")

  pair_symbol = _derive_pair_symbol(summary.get("symbol"), mint)
  interval_value = max(1.0, interval_sec)

  source_cfg = {
    "type": "jupiter",
    "mint": mint,
    "symbol": pair_symbol,
    "token_symbol": summary.get("symbol"),
    "token_name": summary.get("name"),
    "decimals": summary.get("decimals"),
    "interval_sec": interval_value,
  }

  return {
    "symbols": [pair_symbol],
    "interval_sec": interval_value,
    "sources": [source_cfg],
  }


__all__ = [
  "PlannerResult",
  "run_multi_agent_planner",
]

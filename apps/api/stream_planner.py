"""
Rule-based multi-agent planner that builds StreamSpec fragments
from user intent and merges them into a final specification.
"""
from __future__ import annotations

import copy
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

SOLANA_MINT_REGEX = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


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
  metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlannerResult:
  handled: bool
  spec: dict[str, Any] | None
  reasoning: list[str]
  agent_outputs: list[AgentOutput]


class BaseAgent:
  name = "agent"

  async def run(self, ctx: PlannerContext) -> AgentOutput:  # pragma: no cover - interface
    raise NotImplementedError


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
    spec = build_spec_from_parsed_config(config)
    return AgentOutput(
      self.name,
      handled=bool(spec.get("sources")),
      spec=spec,
      reasoning=config.get("reasoning", "Exchange agent produced base spec."),
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
    DexAgent(),
    PolymarketAgent(),
    ExchangeAgent(parse_stream_request),
  ]

  fragments: list[dict[str, Any]] = []
  reasoning: list[str] = []
  outputs: list[AgentOutput] = []

  for agent in agents:
    try:
      result = await agent.run(ctx)
    except Exception as exc:  # pragma: no cover - defensive
      outputs.append(AgentOutput(agent.name, handled=False, reasoning=f"error: {exc}"))
      continue

    outputs.append(result)
    if result.handled and result.spec:
      fragments.append(result.spec)
      if result.reasoning:
        reasoning.append(result.reasoning)

  combined = _merge_specs(current_spec, fragments)
  handled = bool(fragments) and bool(combined.get("sources"))

  return PlannerResult(
    handled=handled,
    spec=combined if combined.get("sources") else None,
    reasoning=reasoning,
    agent_outputs=outputs,
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

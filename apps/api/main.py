"""
FastAPI control plane for bond.
REST endpoints to create and manage streams.
"""
import os
from contextlib import asynccontextmanager
from typing import Any
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query, Request, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from apps.compiler import compile_spec, generate_stream_id
from apps.runtime import StreamRuntime
from apps.api.crypto import generate_token, get_secret
from apps.api.limits import limits
from apps.api.metrics import metrics_registry
from apps.api.nlp_parser import parse_stream_request
from apps.api.polymarket import (
  build_polymarket_spec,
  extract_polymarket_query,
)
from engine.schemas import StreamSpec
from connectors.polymarket_stream import discover_polymarket_events
from supabase import create_client, Client


# Global runtime instance
runtime: StreamRuntime | None = None

# Supabase client
SUPABASE_URL = "https://eezdrsmjpycrzuriyzni.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVlemRyc21qcHljcnp1cml5em5pIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjI1MDQ1NTgsImV4cCI6MjA3ODA4MDU1OH0.kBl6L1pnX1OnNbTYURcNfijIK8Oqq4xfjXECwLLm_4o"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Tier limits
TIER_LIMITS = {
  "free": 5,
  "pro": 50,
  "enterprise": 999999  # effectively unlimited
}


@asynccontextmanager
async def lifespan(app: FastAPI):
  """Lifespan context manager for startup/shutdown."""
  global runtime
  redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
  runtime = StreamRuntime(redis_url)
  await runtime.start()
  yield
  await runtime.stop()


app = FastAPI(
  title="Bond API",
  description="Real-time market data stream platform",
  version="0.1.0",
  lifespan=lifespan,
)


# Helper function to get user ID from Authorization header
async def get_user_id_from_token(authorization: str | None = None) -> str | None:
  """Extract user ID from Bearer token."""
  if not authorization or not authorization.startswith("Bearer "):
    return None

  token = authorization.replace("Bearer ", "")
  try:
    user_response = supabase.auth.get_user(token)
    if user_response and user_response.user:
      return user_response.user.id
  except Exception as e:
    print(f"Error getting user from token: {e}")
    return None

  return None


# Helper function to record stream creation in database
async def record_stream_creation(user_id: str, stream_id: str, user_query: str):
  """Record stream in history and update user stats."""
  try:
    # Insert into stream_history
    supabase.table("stream_history").insert({
      "user_id": user_id,
      "stream_id": stream_id,
      "user_query": user_query,
      "is_active": True
    }).execute()

    # Update user_profiles counters
    supabase.rpc("increment_stream_counts", {"p_user_id": user_id}).execute()

  except Exception as e:
    print(f"Error recording stream creation: {e}")
    # Don't fail the request if DB recording fails


# Helper function to mark stream as stopped
async def mark_stream_stopped(stream_id: str):
  """Mark a stream as inactive in the database."""
  try:
    from datetime import datetime
    supabase.table("stream_history").update({
      "is_active": False,
      "stopped_at": datetime.utcnow().isoformat()
    }).eq("stream_id", stream_id).eq("is_active", True).execute()
  except Exception as e:
    print(f"Error marking stream as stopped: {e}")


# Helper function to get user stats
async def get_user_stats(user_id: str) -> dict:
  """Get user tier, limits, and usage stats."""
  try:
    result = supabase.rpc("get_user_stats", {"p_user_id": user_id}).execute()
    if result.data and len(result.data) > 0:
      stats = result.data[0]
      tier = stats.get("tier", "free")

      # Get accurate active streams count from runtime
      if runtime:
        user_streams = supabase.table("stream_history").select("stream_id").eq("user_id", user_id).eq("is_active", True).execute()
        active_stream_ids = [s["stream_id"] for s in (user_streams.data or [])]
        # Count how many are actually running
        actual_active = sum(1 for sid in active_stream_ids if runtime.is_running(sid))
      else:
        actual_active = stats.get("active_streams_count", 0)

      return {
        "tier": tier,
        "tier_limit": TIER_LIMITS.get(tier, 5),
        "active_streams": actual_active,
        "total_streams": stats.get("total_streams_created", 0),
        "monthly_streams": stats.get("streams_created_this_month", 0)
      }
  except Exception as e:
    print(f"Error getting user stats: {e}")

  # Default for non-authenticated or error
  return {
    "tier": "free",
    "tier_limit": 5,
    "active_streams": 0,
    "total_streams": 0,
    "monthly_streams": 0
  }


class CreateStreamRequest(BaseModel):
  """Request to create a new stream."""
  natural_language: str | None = Field(None, description="Natural language spec")
  spec: dict[str, Any] | None = Field(None, description="Structured spec")


class CreateStreamResponse(BaseModel):
  """Response with stream details."""
  stream_id: str
  access_token: str
  ws_url: str
  spec: dict[str, Any]


class UpdateStreamRequest(BaseModel):
  """Request payload to update a running stream."""
  spec: dict[str, Any]


class StreamInfo(BaseModel):
  """Information about an active stream."""
  stream_id: str
  running: bool


class PolymarketSearchResponse(BaseModel):
  """Response payload for Polymarket discovery."""
  events: list[dict[str, Any]]


class PolymarketPlanRequest(BaseModel):
  """Request payload to plan a Polymarket-only stream."""
  query: str


class PolymarketPlanResponse(BaseModel):
  """Response containing a spec if handled."""
  handled: bool
  reason: str | None = None
  search_query: str | None = None
  categories: list[str] = Field(default_factory=list)
  plan: dict[str, Any] | None = None


async def _build_stream_response(stream_id: str, spec: StreamSpec) -> CreateStreamResponse:
  """Generate access tokens and WS URL for a stream."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  dispatcher = runtime.dispatcher
  client = getattr(dispatcher, "client", None)
  if not client:
    raise HTTPException(500, "Dispatcher client not initialized")

  secret = get_secret()
  persistent_token = generate_token(stream_id, secret, ttl_sec=315360000)  # ~10 years
  await client.set(f"stream:{stream_id}:token", persistent_token)

  short_token = generate_token(stream_id, secret, ttl_sec=3600)
  ws_host = os.getenv("WS_HOST", "localhost:8080")
  ws_protocol = "wss" if "railway.app" in ws_host or "production" in ws_host else "ws"
  ws_url = f"{ws_protocol}://{ws_host}/ws/{stream_id}?token={short_token}"

  return CreateStreamResponse(
    stream_id=stream_id,
    access_token=persistent_token,
    ws_url=ws_url,
    spec=spec.model_dump(),
  )


@app.get("/login")
async def login_page():
  """Serve the login page."""
  return FileResponse("/app/apps/api/static/login.html")


@app.get("/")
async def root(request: Request):
  """Serve the UI - require authentication."""
  # Check for auth tokens in query params (from Echo_Website)
  access_token = request.query_params.get('access_token')
  refresh_token = request.query_params.get('refresh_token')

  if access_token and refresh_token:
    # Redirect to login page with tokens for validation
    return RedirectResponse(url=f"/login?access_token={access_token}&refresh_token={refresh_token}")

  # Allow access without auth for now
  return FileResponse("/app/apps/api/static/index.html")


@app.get("/health")
async def health():
  """Health check endpoint."""
  return {"status": "ok", "service": "bond"}


@app.get("/v1/user/stats")
async def user_stats(authorization: str | None = Header(None)):
  """Get current user's stats (tier, usage, limits)."""
  user_id = await get_user_id_from_token(authorization)

  if not user_id:
    raise HTTPException(401, "Authentication required")

  stats = await get_user_stats(user_id)
  return stats


@app.get("/v1/user/history")
async def user_history(
  authorization: str | None = Header(None),
  limit: int = Query(20, ge=1, le=100)
):
  """Get current user's stream creation history."""
  user_id = await get_user_id_from_token(authorization)

  if not user_id:
    raise HTTPException(401, "Authentication required")

  try:
    result = supabase.table("stream_history")\
      .select("*")\
      .eq("user_id", user_id)\
      .order("created_at", desc=True)\
      .limit(limit)\
      .execute()

    # Update is_active based on actual runtime status
    history = result.data or []
    if runtime:
      for item in history:
        item["is_active"] = runtime.is_running(item["stream_id"])

    return {"history": history}
  except Exception as e:
    print(f"Error fetching user history: {e}")
    raise HTTPException(500, "Failed to fetch history")


@app.post("/v1/streams", response_model=CreateStreamResponse)
async def create_stream(
  req: CreateStreamRequest,
  authorization: str | None = Header(None)
):
  """
  Create a new data stream.

  Accepts either natural language or structured spec.
  Returns stream ID and signed WebSocket URL.
  """
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  # Get user ID from auth token (optional for now)
  user_id = await get_user_id_from_token(authorization)
  user_query = req.natural_language or "Custom spec"

  # Check tier limits if authenticated
  if user_id:
    stats = await get_user_stats(user_id)
    if stats["active_streams"] >= stats["tier_limit"]:
      # Show warning but don't block
      print(f"⚠️  User {user_id} ({stats['tier']}) has {stats['active_streams']}/{stats['tier_limit']} active streams")

  # Check limits before creating
  current_count = len(runtime.list_streams())
  allowed, message = limits.check_limit(current_count)
  if not allowed:
    raise HTTPException(429, message)

  # Compile spec
  if req.natural_language:
    # Use DeepSeek NL parser instead of basic parser
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
      raise HTTPException(500, "DEEPSEEK_API_KEY not configured")

    config = await parse_stream_request(req.natural_language, api_key)
    spec_dict = {
      "sources": [],
      "symbols": [f"{sym}/USDT" for sym in config["symbols"]],
      "interval_sec": config["interval_sec"],
    }

    for exchange_cfg in config["exchanges"]:
      spec_dict["sources"].append({
        "type": "ccxt",
        "exchange": exchange_cfg["exchange"],
        "fields": exchange_cfg["fields"],
        "symbols": [f"{symbol}/USDT" for symbol in config["symbols"]],
      })

    for source in config.get("additional_sources", []):
      if source == "twitter":
        spec_dict["sources"].append({"type": "twitter"})
      elif source == "google_trends":
        spec_dict["sources"].append({
          "type": "google_trends",
          "keywords": [s.lower() for s in config["symbols"]],
          "timeframe": "now 1-d",
        })
      elif source == "polymarket":
        spec_dict["sources"].append({
          "type": "polymarket",
          "interval_sec": max(15, int(config["interval_sec"])),
        })
      elif isinstance(source, dict) and source.get("type") == "nitter":
        spec_dict["sources"].append({
          "type": "nitter",
          "username": source.get("username", "elonmusk"),
          "interval_sec": source.get("interval_sec", config["interval_sec"]),
        })
      elif isinstance(source, dict) and source.get("type") == "polymarket":
        spec_dict["sources"].append({
          "type": "polymarket",
          "event_ids": source.get("event_ids", []),
          "categories": source.get("categories", []),
          "include_closed": bool(source.get("include_closed", False)),
          "active_only": bool(source.get("active_only", True)),
          "interval_sec": source.get("interval_sec", max(15, int(config["interval_sec"]))),
          "limit": source.get("limit", 100),
        })

    spec = StreamSpec(**spec_dict)
  elif req.spec:
    spec = compile_spec(req.spec)
  else:
    raise HTTPException(400, "Must provide natural_language or spec")

  # Generate stream ID
  stream_id = generate_stream_id(spec)

  # Launch stream if not already running
  if not runtime.is_running(stream_id):
    await runtime.launch_stream(stream_id, spec)
    # Create metrics tracker
    metrics_registry.create(stream_id)

    # Record stream creation in database if user is authenticated
    if user_id:
      await record_stream_creation(user_id, stream_id, user_query)

  return await _build_stream_response(stream_id, spec)


@app.get("/v1/streams", response_model=list[StreamInfo])
async def list_streams():
  """List all active streams."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  stream_ids = runtime.list_streams()

  return [
    StreamInfo(stream_id=sid, running=True)
    for sid in stream_ids
  ]


@app.delete("/v1/streams/{stream_id}")
async def delete_stream(stream_id: str):
  """Stop and delete a stream."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  await runtime.stop_stream(stream_id)

  # Mark stream as stopped in database
  await mark_stream_stopped(stream_id)

  # Clean up metrics
  metrics_registry.delete(stream_id)

  return {"status": "deleted", "stream_id": stream_id}


@app.get("/v1/streams/{stream_id}", response_model=StreamInfo)
async def get_stream(stream_id: str):
  """Get stream information."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  running = runtime.is_running(stream_id)

  if not running:
    raise HTTPException(404, f"Stream {stream_id} not found")

  return StreamInfo(stream_id=stream_id, running=running)


@app.get("/v1/streams/{stream_id}/token")
async def get_stream_token(stream_id: str):
  """Get the persistent access token for a stream."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  # Retrieve persistent token from Redis
  token = await runtime.dispatcher.client.get(f"stream:{stream_id}:token")

  if not token:
    raise HTTPException(404, "Token not found for this stream")

  # Build WebSocket URL (use wss:// for production, ws:// for localhost)
  ws_host = os.getenv("WS_HOST", "localhost:8080")
  ws_protocol = "wss" if "railway.app" in ws_host or "production" in ws_host else "ws"
  ws_url = f"{ws_protocol}://{ws_host}/ws/{stream_id}?token={token.decode() if isinstance(token, bytes) else token}"

  return {
    "stream_id": stream_id,
    "access_token": token.decode() if isinstance(token, bytes) else token,
    "ws_url": ws_url,
  }


class TokenResponse(BaseModel):
  """Token response with WebSocket URL."""
  token: str
  ws_url: str
  expires_in_sec: int


@app.post("/v1/streams/{stream_id}/token", response_model=TokenResponse)
async def refresh_token(stream_id: str):
  """
  Generate a new access token for an existing stream.
  
  Useful when the previous token has expired or is about to expire.
  """
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  # Generate fresh token
  secret = get_secret()
  ttl_sec = int(os.getenv("BOND_TOKEN_TTL", "3600"))
  token = generate_token(stream_id, secret, ttl_sec=ttl_sec)

  # Build WebSocket URL (use wss:// for production, ws:// for localhost)
  ws_host = os.getenv("WS_HOST", "localhost:8080")
  ws_protocol = "wss" if "railway.app" in ws_host or "production" in ws_host else "ws"
  ws_url = f"{ws_protocol}://{ws_host}/ws/{stream_id}?token={token}"

  return TokenResponse(
    token=token,
    ws_url=ws_url,
    expires_in_sec=ttl_sec,
  )


@app.get("/v1/streams/{stream_id}/metrics")
async def get_stream_metrics(stream_id: str):
  """
  Get metrics for a stream.

  Returns message counts, latency percentiles, dropped events, and uptime.
  """
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  metrics = metrics_registry.get(stream_id)
  if not metrics:
    # Stream exists but no metrics yet (just started)
    return {
      "stream_id": stream_id,
      "uptime_sec": 0.0,
      "msgs_in": 0,
      "msgs_out": 0,
      "dropped": 0,
      "latency_p50_ms": 0.0,
      "latency_p95_ms": 0.0,
      "note": "Metrics collection starting",
    }

  return metrics.to_dict()


@app.patch("/v1/streams/{stream_id}/restart", response_model=CreateStreamResponse)
async def restart_stream(stream_id: str):
  """Restart a running stream by stopping and relaunching it."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  # Reset metrics for the stream so uptime/latency start fresh
  metrics_registry.delete(stream_id)

  try:
    await runtime.restart_stream(stream_id)
    spec = runtime.get_stream_spec(stream_id)
  except ValueError as exc:
    raise HTTPException(400, str(exc)) from exc

  metrics_registry.create(stream_id)
  return await _build_stream_response(stream_id, spec)


@app.put("/v1/streams/{stream_id}", response_model=CreateStreamResponse)
async def update_stream(stream_id: str, req: UpdateStreamRequest):
  """Update an existing stream with a new spec."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  try:
    spec = StreamSpec(**req.spec)
  except ValidationError as exc:
    raise HTTPException(400, f"Invalid spec: {exc}") from exc

  metrics_registry.delete(stream_id)

  try:
    await runtime.update_stream(stream_id, spec)
  except ValueError as exc:
    raise HTTPException(400, str(exc)) from exc

  metrics_registry.create(stream_id)
  return await _build_stream_response(stream_id, spec)


@app.get("/v1/streams/{stream_id}/schema")
async def get_stream_schema(stream_id: str):
  """Get schema for a stream."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  if not runtime.is_running(stream_id):
    raise HTTPException(404, f"Stream {stream_id} not found")

  # Return basic schema
  return {
    "stream_id": stream_id,
    "fields": [
      {"name": "ts", "type": "string"},
      {"name": "window_start", "type": "string"},
      {"name": "window_end", "type": "string"},
      {"name": "price_avg", "type": "float"},
      {"name": "volume_sum", "type": "float"},
      {"name": "raw_data", "type": "object"}
    ]
  }


@app.get("/v1/streams/{stream_id}/spec")
async def get_stream_spec(stream_id: str):
  """Get stream specification."""
  if not runtime:
    raise HTTPException(500, "Runtime not initialized")

  try:
    spec = runtime.get_stream_spec(stream_id)
  except ValueError as exc:
    raise HTTPException(404, str(exc)) from exc

  return spec.model_dump()


@app.get("/v1/polymarket/search", response_model=PolymarketSearchResponse)
async def polymarket_search_endpoint(
  query: str | None = Query(
    None,
    description="Keyword filter for event titles/questions",
    alias="query",
  ),
  limit: int = Query(
    12,
    ge=1,
    le=50,
    description="Maximum number of events to return",
  ),
  category: list[str] | None = Query(
    None,
    description="Optional category filters",
  ),
  tag: str | None = Query(
    None,
    description="Optional tag filter (Polymarket tag)",
  ),
  include_closed: bool = Query(
    False,
    description="Include closed events in the result set",
  ),
):
  """Lightweight Polymarket discovery endpoint for the chat assistant."""
  try:
    events = await discover_polymarket_events(
      query=query,
      categories=category,
      tags=[tag] if tag else None,
      limit=limit,
      include_closed=include_closed,
    )
  except Exception as exc:
    raise HTTPException(400, f"Failed to search Polymarket: {exc}") from exc

  return PolymarketSearchResponse(events=events)


@app.post("/v1/polymarket/plan", response_model=PolymarketPlanResponse)
async def polymarket_plan(req: PolymarketPlanRequest):
  """Extract Polymarket intent and build a starter spec."""
  query, categories = extract_polymarket_query(req.query)
  if not query:
    return PolymarketPlanResponse(
      handled=False,
      reason="No Polymarket intent detected",
    )

  spec = build_polymarket_spec(query, categories)
  plan_payload = {
    "spec": spec.model_dump(),
    "reasoning": f"Polymarket request detected for '{query}'.",
    "parsed_config": {
      "polymarket_query": query,
      "categories": categories,
    },
  }

  return PolymarketPlanResponse(
    handled=True,
    search_query=query,
    categories=categories,
    plan=plan_payload,
  )


class NLParseRequest(BaseModel):
  """Request to parse natural language into stream config."""
  query: str
  stream_id: str | None = None


class NLParseResponse(BaseModel):
  """Response with parsed stream configuration."""
  spec: dict[str, Any]
  reasoning: str
  parsed_config: dict[str, Any]


@app.post("/v1/streams/parse", response_model=NLParseResponse)
async def parse_nl_stream(req: NLParseRequest):
  """
  Parse natural language into stream configuration.

  Example: "show me live btc prices" -> structured stream spec
  """
  api_key = os.getenv("DEEPSEEK_API_KEY")
  if not api_key:
    raise HTTPException(500, "DEEPSEEK_API_KEY not configured")

  current_spec: dict[str, Any] | None = None
  if req.stream_id:
    if not runtime:
      raise HTTPException(500, "Runtime not initialized")
    if not runtime.is_running(req.stream_id):
      raise HTTPException(404, f"Stream {req.stream_id} not found")
    current_spec = runtime.get_stream_spec(req.stream_id).model_dump()

  try:
    config = await parse_stream_request(req.query, api_key, current_spec=current_spec)

    # Convert to StreamSpec format
    spec = {
      "sources": [],
      "symbols": [f"{sym}/USDT" for sym in config["symbols"]],
      "interval_sec": config["interval_sec"]
    }

    # Add exchange sources (one source per exchange with all symbols)
    for exchange_cfg in config["exchanges"]:
      exchange_name = (exchange_cfg.get("exchange") or "").lower()
      if exchange_name == "binance":
        exchange_name = "binanceus"
      elif not exchange_name:
        exchange_name = "binanceus"

      spec["sources"].append({
        "type": "ccxt",
        "exchange": exchange_name,
        "fields": exchange_cfg["fields"],
        "symbols": [f"{symbol}/USDT" for symbol in config["symbols"]]
      })

    # Add additional sources
    for source in config.get("additional_sources", []):
      if source == "twitter":
        spec["sources"].append({"type": "twitter"})
      elif source == "google_trends":
        spec["sources"].append({
          "type": "google_trends",
          "keywords": [s.lower() for s in config["symbols"]],
          "timeframe": "now 1-d"
        })
      elif source == "polymarket":
        spec["sources"].append({
          "type": "polymarket",
          "interval_sec": max(15, int(config["interval_sec"])),
        })
      elif isinstance(source, dict) and source.get("type") == "nitter":
        # Nitter source with username
        spec["sources"].append({
          "type": "nitter",
          "username": source.get("username", "elonmusk"),
          "interval_sec": source.get("interval_sec", config["interval_sec"])
        })
      elif isinstance(source, dict) and source.get("type") == "polymarket":
        spec["sources"].append({
          "type": "polymarket",
          "event_ids": source.get("event_ids", []),
          "categories": source.get("categories", []),
          "include_closed": bool(source.get("include_closed", False)),
          "active_only": bool(source.get("active_only", True)),
          "interval_sec": source.get("interval_sec", max(15, int(config["interval_sec"]))),
          "limit": source.get("limit", 100),
        })

    return NLParseResponse(
      spec=spec,
      reasoning=config.get("reasoning", ""),
      parsed_config=config
    )

  except Exception as e:
    raise HTTPException(400, f"Failed to parse request: {str(e)}")

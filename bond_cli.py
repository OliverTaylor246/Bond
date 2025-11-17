#!/usr/bin/env python3
"""
Bond CLI - Conversational terminal interface for Bond streams.
Like Claude Code, but for real-time market data streams.
"""
import asyncio
import json
import sys
from datetime import datetime
from typing import Any, Optional

import httpx
import websockets
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.prompt import Prompt, Confirm
from rich.layout import Layout
from rich.text import Text

console = Console()


class BondCLI:
  """Conversational CLI for Bond streaming platform."""

  def __init__(self, api_url: str = "http://localhost:8000", ws_url: str = "ws://localhost:8080"):
    self.api_url = api_url.rstrip("/")
    self.ws_url = ws_url.rstrip("/")
    self.conversation_history: list[str] = []
    self.active_streams: dict[str, dict[str, Any]] = {}
    self.current_stream_task: Optional[asyncio.Task] = None
    self.created_streams: set[str] = set()  # Track streams created in this session

  async def run(self) -> None:
    """Main CLI loop."""
    self.print_welcome()

    while True:
      try:
        user_input = await self.get_input()

        if not user_input:
          continue

        # Check for commands
        if user_input.startswith("/"):
          await self.handle_command(user_input)
          continue

        # Check for exit
        if user_input.lower() in ["exit", "quit", "bye"]:
          if Confirm.ask("Are you sure you want to exit?"):
            await self.cleanup()
            console.print("[yellow]Goodbye! 👋[/]")
            break
          continue

        # Process natural language query
        await self.process_query(user_input)

      except KeyboardInterrupt:
        console.print("\n[yellow]Use 'exit' or Ctrl+D to quit[/]")
      except EOFError:
        await self.cleanup()
        console.print("\n[yellow]Goodbye! 👋[/]")
        break
      except Exception as e:
        console.print(f"[red]Error: {e}[/]")

  def print_welcome(self) -> None:
    """Print welcome message."""
    welcome = """
# 🚀 Bond Terminal

Welcome to Bond - your conversational data streaming assistant!

**What I can do:**
- Stream crypto prices from exchanges (Binance, Kraken, etc.)
- Track Twitter/X sentiment and mentions
- Monitor Google Trends for keywords
- Watch Polymarket prediction markets
- Stream Solana on-chain data via Jupiter DEX
- Combine multiple data sources in real-time

**How to use:**
- Just ask me in natural language: "show me BTC prices"
- I'll create a stream and give you the WebSocket URL
- Type `/help` for commands

**Examples:**
- "BTC price every 5 seconds"
- "ETH + SOL prices with tweets"
- "Show me bitcoin prices from Kraken"
- "I want Polymarket events about crypto"
"""
    console.print(Panel(Markdown(welcome), border_style="cyan", title="Welcome"))

  async def get_input(self) -> str:
    """Get user input with prompt."""
    return await asyncio.to_thread(
      Prompt.ask,
      "\n[bold green]You[/]"
    )

  async def process_query(self, query: str) -> None:
    """Process natural language query."""
    self.conversation_history.append(query)

    console.print(f"[dim]Thinking...[/]")

    try:
      async with httpx.AsyncClient(timeout=30.0) as client:
        # Call Bond's stream planner
        response = await client.post(
          f"{self.api_url}/v1/streams/parse",
          json={
            "query": query,
            "context": self.conversation_history[-5:]  # Last 5 messages
          }
        )

        if response.status_code != 200:
          console.print(f"[red]API Error: {response.status_code}[/]")
          console.print(f"[red]{response.text}[/]")
          return

        result = response.json()

        # Handle based on intent
        if result.get("intent") == "conversation":
          self.print_bot_message(result.get("response", result.get("message", "I understand.")))

        elif result.get("intent") == "clarification":
          self.print_bot_message(result.get("question", result.get("message", "Can you clarify?")))

        elif result.get("intent") == "action" or "spec" in result:
          await self.handle_stream_creation(result)

        else:
          # Fallback: try to create stream directly
          await self.create_stream_direct(query)

    except httpx.ConnectError:
      console.print("[red]❌ Cannot connect to Bond API at {self.api_url}[/]")
      console.print("[yellow]Make sure Bond is running: `cd infra && docker compose up -d`[/]")
    except Exception as e:
      console.print(f"[red]Error processing query: {e}[/]")

  async def handle_stream_creation(self, result: dict[str, Any]) -> None:
    """Handle stream creation from planner result."""
    spec = result.get("spec")
    confidence = result.get("confidence", 1.0)
    summary = result.get("spec_summary", result.get("message", ""))

    if not spec:
      console.print("[red]No stream specification generated[/]")
      return

    # Show what we're creating
    if summary:
      self.print_bot_message(f"I'll create: {summary}")

    # Ask for confirmation if low confidence
    if confidence < 0.8:
      console.print(f"[yellow]Confidence: {confidence:.0%}[/]")
      if not Confirm.ask("Create this stream?"):
        console.print("[yellow]Cancelled[/]")
        return

    # Create the stream
    await self.create_stream(spec)

  async def create_stream_direct(self, query: str) -> None:
    """Create stream directly from natural language."""
    try:
      async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
          f"{self.api_url}/v1/streams",
          json={"natural_language": query}
        )

        if response.status_code == 429:
          console.print("[red]❌ Stream limit reached![/]")
          console.print("[yellow]Delete some streams first: /list then /stop <id>[/]")
          return

        if response.status_code != 200:
          console.print(f"[red]Failed to create stream: {response.status_code}[/]")
          console.print(f"[red]{response.text}[/]")
          return

        stream_data = response.json()
        await self.show_stream_created(stream_data)

    except Exception as e:
      console.print(f"[red]Error creating stream: {e}[/]")

  async def create_stream(self, spec: dict[str, Any]) -> None:
    """Create stream from spec."""
    try:
      async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
          f"{self.api_url}/v1/streams",
          json={"spec": spec}
        )

        if response.status_code == 429:
          console.print("[red]❌ Stream limit reached![/]")
          console.print("[yellow]Delete some streams first: /list then /stop <id>[/]")
          return

        if response.status_code != 200:
          console.print(f"[red]Failed to create stream: {response.status_code}[/]")
          console.print(f"[red]{response.text}[/]")
          return

        stream_data = response.json()
        await self.show_stream_created(stream_data)

    except Exception as e:
      console.print(f"[red]Error creating stream: {e}[/]")

  async def show_stream_created(self, stream_data: dict[str, Any]) -> None:
    """Show stream creation success and offer to watch."""
    stream_id = stream_data.get("stream_id")
    ws_url = stream_data.get("ws_url")
    spec = stream_data.get("spec", {})

    # Format stream info
    sources = spec.get("sources", [])
    interval = spec.get("interval_sec", 5)
    symbols = spec.get("symbols", [])

    info_text = f"""
[bold green]✅ Stream Created![/]

[bold]Stream ID:[/] [cyan]{stream_id}[/]
[bold]Symbols:[/] {', '.join(symbols) if symbols else 'N/A'}
[bold]Interval:[/] {interval}s
[bold]Sources:[/] {len(sources)} source(s)

[bold]WebSocket URL:[/]
[cyan]{ws_url}[/]
"""

    console.print(Panel(info_text, border_style="green", title="Stream Ready"))

    # Store stream info
    self.active_streams[stream_id] = {
      "stream_id": stream_id,
      "ws_url": ws_url,
      "spec": spec,
      "created_at": datetime.now()
    }

    # Track that we created this stream
    self.created_streams.add(stream_id)

    # Offer to watch
    if Confirm.ask("Watch this stream now?", default=True):
      await self.watch_stream(stream_id, ws_url)

  async def watch_stream(self, stream_id: str, ws_url: str) -> None:
    """Watch a stream in real-time."""
    console.print(f"\n[cyan]Connecting to stream {stream_id}...[/]")
    console.print("[bold yellow]Press Ctrl+C to stop watching and return to chat[/]\n")

    try:
      async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
        event_count = 0
        console.print("[dim]Waiting for events... (stream may take a few seconds to start producing data)[/]")

        # Use Live display with proper updating
        initial_table = Table(title="Waiting for first event...")

        with Live(initial_table, console=console, refresh_per_second=1) as live:
          async for message in ws:
            try:
              event = json.loads(message)
              event_count += 1

              # Build display table
              table = self.build_event_table(event, event_count)

              # Wrap in panel with header and footer
              display = Panel(
                table,
                title=f"[bold yellow]📊 Stream {stream_id}[/]",
                subtitle="[dim]Press Ctrl+C to stop watching[/]",
                border_style="yellow"
              )

              live.update(display)
            except json.JSONDecodeError as e:
              console.print(f"[yellow]Warning: Invalid JSON received: {message[:100]}[/]")
              continue

    except KeyboardInterrupt:
      console.print("\n[green]✓ Stopped watching. You can continue chatting or type /list to see active streams.[/]")
    except websockets.exceptions.ConnectionClosed:
      console.print("\n[red]Stream connection closed[/]")
    except asyncio.TimeoutError:
      console.print("\n[red]Connection timed out. The stream may not be producing data yet.[/]")
    except Exception as e:
      console.print(f"\n[red]Error watching stream: {type(e).__name__}: {e}[/]")

  def build_event_table(self, event: dict[str, Any], event_count: int) -> Table:
    """Build rich table for event display."""
    table = Table(
      title=f"Live Stream (Event #{event_count})",
      show_header=True,
      header_style="bold cyan"
    )

    table.add_column("Field", style="cyan", width=20)
    table.add_column("Value", style="white")

    # Timestamp
    ts = event.get("ts", "")
    if ts:
      table.add_row("Timestamp", str(ts))

    # Price data
    if event.get("price_avg"):
      price = event["price_avg"]
      table.add_row("Price (avg)", f"[bold green]${price:,.2f}[/]")

    if event.get("price_high"):
      table.add_row("Price (high)", f"${event['price_high']:,.2f}")

    if event.get("price_low"):
      table.add_row("Price (low)", f"${event['price_low']:,.2f}")

    if event.get("bid_avg"):
      table.add_row("Bid", f"${event['bid_avg']:,.2f}")

    if event.get("ask_avg"):
      table.add_row("Ask", f"${event['ask_avg']:,.2f}")

    # Volume
    if event.get("volume_sum"):
      table.add_row("Volume", f"{event['volume_sum']:,.2f}")

    # Symbol/Exchange
    if event.get("symbol"):
      table.add_row("Symbol", event["symbol"])

    if event.get("exchange"):
      table.add_row("Exchange", event["exchange"])

    # Social/Custom
    if event.get("tweets", 0) > 0:
      table.add_row("Tweets", f"[yellow]{event['tweets']}[/]")

    if event.get("onchain_count", 0) > 0:
      table.add_row("On-chain Events", f"[magenta]{event['onchain_count']}[/]")

    if event.get("custom_count", 0) > 0:
      table.add_row("Custom Events", f"{event['custom_count']}")

    # Raw data preview
    raw = event.get("raw_data", {})
    if raw.get("trades"):
      table.add_row("Trades", f"{raw['trades']}")

    if raw.get("sources"):
      sources = ", ".join(raw["sources"])
      table.add_row("Sources", sources)

    return table

  async def handle_command(self, cmd: str) -> None:
    """Handle slash commands."""
    parts = cmd.split()
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    if command == "/help":
      self.print_help()
    elif command == "/list":
      await self.list_streams()
    elif command == "/stop":
      if args:
        await self.stop_stream(args[0])
      else:
        console.print("[yellow]Usage: /stop <stream_id>[/]")
    elif command == "/watch":
      if args:
        await self.watch_stream_by_id(args[0])
      else:
        console.print("[yellow]Usage: /watch <stream_id>[/]")
    elif command == "/url":
      if args:
        await self.show_url(args[0])
      else:
        console.print("[yellow]Usage: /url <stream_id>[/]")
    elif command == "/clear":
      console.clear()
    elif command == "/history":
      self.show_history()
    else:
      console.print(f"[red]Unknown command: {command}[/]")
      console.print("[yellow]Type /help for available commands[/]")

  def print_help(self) -> None:
    """Print help message."""
    help_text = """
# Commands

- `/help` - Show this help message
- `/list` - List all active streams
- `/stop <id>` - Stop a stream
- `/watch <id>` - Watch a stream in real-time
- `/url <id>` - Get WebSocket URL for a stream
- `/clear` - Clear the screen
- `/history` - Show conversation history
- `exit` or `quit` - Exit the CLI

# Natural Language

Just ask me what you want:
- "show me BTC prices"
- "ETH + SOL every 3 seconds"
- "bitcoin with tweets from Kraken"
- "what data sources do you support?"
"""
    console.print(Panel(Markdown(help_text), border_style="blue", title="Help"))

  async def list_streams(self) -> None:
    """List all active streams."""
    try:
      async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{self.api_url}/v1/streams")

        if response.status_code != 200:
          console.print(f"[red]Failed to list streams: {response.status_code}[/]")
          return

        streams = response.json()

        if not streams:
          console.print("[yellow]No active streams[/]")
          return

        table = Table(title="Active Streams", show_header=True, header_style="bold cyan")
        table.add_column("Stream ID", style="cyan")
        table.add_column("Symbols", style="white")
        table.add_column("Interval", style="green")
        table.add_column("Sources", style="yellow")

        for stream in streams:
          stream_id = stream.get("stream_id", "N/A")
          spec = stream.get("spec", {})
          symbols = ", ".join(spec.get("symbols", []))
          interval = f"{spec.get('interval_sec', 5)}s"
          sources = len(spec.get("sources", []))

          table.add_row(stream_id, symbols, interval, str(sources))

        console.print(table)

    except Exception as e:
      console.print(f"[red]Error listing streams: {e}[/]")

  async def stop_stream(self, stream_id: str) -> None:
    """Stop a stream."""
    try:
      async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.delete(f"{self.api_url}/v1/streams/{stream_id}")

        if response.status_code == 404:
          console.print(f"[red]Stream {stream_id} not found[/]")
          return

        if response.status_code != 200:
          console.print(f"[red]Failed to stop stream: {response.status_code}[/]")
          return

        console.print(f"[green]✅ Stopped stream {stream_id}[/]")

        if stream_id in self.active_streams:
          del self.active_streams[stream_id]

    except Exception as e:
      console.print(f"[red]Error stopping stream: {e}[/]")

  async def watch_stream_by_id(self, stream_id: str) -> None:
    """Watch a stream by ID."""
    try:
      # Get stream info first
      async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{self.api_url}/v1/streams/{stream_id}")

        if response.status_code == 404:
          console.print(f"[red]Stream {stream_id} not found[/]")
          return

        if response.status_code != 200:
          console.print(f"[red]Failed to get stream info: {response.status_code}[/]")
          return

        stream_data = response.json()
        ws_url = stream_data.get("ws_url")

        if not ws_url:
          console.print("[red]No WebSocket URL in stream data[/]")
          return

        await self.watch_stream(stream_id, ws_url)

    except Exception as e:
      console.print(f"[red]Error: {e}[/]")

  async def show_url(self, stream_id: str) -> None:
    """Show WebSocket URL for a stream."""
    try:
      async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{self.api_url}/v1/streams/{stream_id}")

        if response.status_code == 404:
          console.print(f"[red]Stream {stream_id} not found[/]")
          return

        if response.status_code != 200:
          console.print(f"[red]Failed to get stream info: {response.status_code}[/]")
          return

        stream_data = response.json()
        ws_url = stream_data.get("ws_url")

        console.print(f"\n[bold]WebSocket URL:[/]")
        console.print(f"[cyan]{ws_url}[/]\n")

    except Exception as e:
      console.print(f"[red]Error: {e}[/]")

  def show_history(self) -> None:
    """Show conversation history."""
    if not self.conversation_history:
      console.print("[yellow]No conversation history[/]")
      return

    console.print("\n[bold cyan]Conversation History:[/]")
    for i, msg in enumerate(self.conversation_history, 1):
      console.print(f"[dim]{i}.[/] {msg}")
    console.print()

  def print_bot_message(self, message: str) -> None:
    """Print bot message with formatting."""
    console.print(f"\n[bold blue]Bond:[/] {message}\n")

  async def cleanup(self) -> None:
    """Cleanup before exit - delete all streams created in this session."""
    if self.current_stream_task and not self.current_stream_task.done():
      self.current_stream_task.cancel()

    # Delete all streams created in this session
    if self.created_streams:
      console.print(f"\n[yellow]Cleaning up {len(self.created_streams)} stream(s)...[/]")

      async with httpx.AsyncClient(timeout=10.0) as client:
        for stream_id in self.created_streams:
          try:
            await client.delete(f"{self.api_url}/v1/streams/{stream_id}")
            console.print(f"[dim]Deleted stream {stream_id}[/]")
          except Exception as e:
            console.print(f"[dim red]Failed to delete {stream_id}: {e}[/]")


async def main():
  """Main entry point."""
  import argparse

  parser = argparse.ArgumentParser(description="Bond CLI - Conversational data streaming")
  parser.add_argument("--api", default="http://localhost:8000", help="Bond API URL")
  parser.add_argument("--ws", default="ws://localhost:8080", help="WebSocket URL")
  args = parser.parse_args()

  cli = BondCLI(api_url=args.api, ws_url=args.ws)
  await cli.run()


if __name__ == "__main__":
  asyncio.run(main())

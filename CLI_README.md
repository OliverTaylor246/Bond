# Bond CLI

Conversational terminal interface for Bond - like Claude Code, but for real-time market data streams.

## Features

- 🤖 **Conversational AI** - Chat naturally: "show me BTC prices"
- 🔄 **Live Stream Display** - Real-time price/volume/tweet updates
- 📊 **Rich Terminal UI** - Beautiful tables and formatting
- 🎯 **Multi-Stream Management** - Create, list, watch, stop streams
- 🔗 **WebSocket URLs** - Get shareable WebSocket URLs for any stream
- ⚡ **Fast & Lightweight** - Pure Python, minimal dependencies

## Installation

### Local Development

```bash
# Install dependencies
pip install httpx websockets rich

# Run the CLI
python bond_cli.py
```

### System-Wide Installation

```bash
# Install globally
pip install -e .

# Run from anywhere
bond
```

### Custom API URL

```bash
# Connect to production API
bond --api https://api.yourdomain.com --ws wss://ws.yourdomain.com

# Or set environment variables
export BOND_API_URL=https://api.yourdomain.com
export BOND_WS_URL=wss://ws.yourdomain.com
bond
```

## Usage

### Natural Language Queries

Just ask for what you want:

```
You: show me BTC prices
Bond: I'll create: BTC/USDT price from Binance every 5 seconds

✅ Stream Created!
Stream ID: 8f2a1c9d4b3e7a10
WebSocket URL: ws://localhost:8080/ws/8f2a1c9d...
Watch this stream now? (y/n): y
```

### More Examples

```
You: ETH + SOL prices every 3 seconds
You: bitcoin with tweets from Kraken
You: I want Polymarket events about crypto
You: show me PEPE prices from KuCoin
You: what data sources do you support?
```

### Commands

- `/help` - Show help message
- `/list` - List all active streams
- `/stop <id>` - Stop a stream
- `/watch <id>` - Watch a stream in real-time
- `/url <id>` - Get WebSocket URL for a stream
- `/clear` - Clear the screen
- `/history` - Show conversation history
- `exit` or `quit` - Exit the CLI

### Live Stream Display

When watching a stream, you'll see real-time updates:

```
┌─ Live Stream (Event #142) ─────────────┐
│ Field            │ Value                │
├──────────────────┼──────────────────────┤
│ Timestamp        │ 2025-11-14T19:30:05Z │
│ Price (avg)      │ $68,234.50           │
│ Price (high)     │ $68,250.00           │
│ Price (low)      │ $68,200.00           │
│ Bid              │ $68,230.00           │
│ Ask              │ $68,235.00           │
│ Volume           │ 1,234.50             │
│ Symbol           │ BTC/USDT             │
│ Exchange         │ binance              │
│ Tweets           │ 12                   │
│ Trades           │ 45                   │
│ Sources          │ binance, kraken      │
└──────────────────────────────────────────┘
```

Press `Ctrl+C` to stop watching.

## Workflow Example

```bash
$ bond

# Start chatting
You: hi
Bond: Hello! I can help you create real-time data streams. What would you like to track?

You: what can you do?
Bond: I can stream:
- Crypto prices from exchanges (Binance, Kraken, etc.)
- Twitter sentiment
- Google Trends
- Polymarket prediction markets
- Solana on-chain data
- Jupiter DEX prices

You: show me bitcoin and ethereum prices
Bond: I'll create: BTC/USDT + ETH/USDT price from Binance every 5 seconds

✅ Stream Created!
Stream ID: a4f8e2c91bd33a12
Watch this stream now? (y/n): y

[Live stream display starts]

# Later, check all streams
You: /list

Active Streams
┌──────────────────┬──────────┬──────────┬─────────┐
│ Stream ID        │ Symbols  │ Interval │ Sources │
├──────────────────┼──────────┼──────────┼─────────┤
│ a4f8e2c91bd33a12 │ BTC,ETH  │ 5s       │ 1       │
└──────────────────┴──────────┴──────────┴─────────┘

# Stop a stream
You: /stop a4f8e2c91bd33a12
Bond: ✅ Stopped stream a4f8e2c91bd33a12

You: exit
Bond: Goodbye! 👋
```

## Architecture

```
┌─────────────────────────────────────┐
│        Bond CLI (Terminal UI)      │
│  - Conversational chat interface    │
│  - Live stream display              │
│  - Command system                   │
└─────────────┬───────────────────────┘
              │
              │ HTTP/WebSocket
              ▼
┌─────────────────────────────────────┐
│        Bond API (Backend)           │
│  - Natural language parsing         │
│  - Stream creation/management       │
│  - Multi-source aggregation         │
└─────────────────────────────────────┘
```

The CLI is just a **thin client** - all the heavy lifting (NL parsing, data aggregation, connectors) happens on the Bond backend.

## Requirements

- Python 3.11+
- Bond API running (default: `http://localhost:8000`)
- Bond WebSocket broker running (default: `ws://localhost:8080`)

## Starting Bond Backend

```bash
# Start Bond services
cd infra
docker compose up -d

# Verify services are running
curl http://localhost:8000/v1/streams
```

## Features in Detail

### Conversational AI
- Uses Bond's existing `/v1/streams/parse` endpoint
- Multi-agent intent classification (conversation/clarification/action)
- Context-aware responses
- Confidence-based confirmation

### Live Display
- Real-time updates with `rich.live.Live`
- Auto-formatting of prices, volumes, counts
- Shows all event fields dynamically
- Smooth refresh rate (4 FPS)

### Stream Management
- Create streams with natural language
- List all active streams
- Stop streams by ID
- Watch any stream in real-time
- Get WebSocket URLs for external use

### Error Handling
- Graceful connection failures
- Stream limit detection (HTTP 429)
- Clear error messages
- Automatic cleanup on exit

## Distribution

### For End Users (PyPI)

Once published to PyPI:

```bash
pip install bond-cli
bond
```

### For Developers

```bash
git clone <repo>
cd Bond
pip install -e .
bond
```

### Custom Deployment

Users can point to their own Bond API:

```bash
bond --api https://api.mybondinstance.com
```

## License

Apache 2.0 - See LICENSE file for details.

## Support

- Issues: https://github.com/yourusername/bond/issues
- Docs: https://docs.yourdomain.com

#!/bin/bash
# Quick test script for Bond CLI

echo "🧪 Testing Bond CLI..."
echo ""

# Check if Bond API is running
echo "1️⃣  Checking Bond API..."
if curl -s http://localhost:8000/v1/streams > /dev/null 2>&1; then
    echo "✅ Bond API is running at http://localhost:8000"
else
    echo "❌ Bond API is NOT running at http://localhost:8000"
    echo ""
    echo "Start Bond with: cd infra && docker compose up -d"
    exit 1
fi

echo ""
echo "2️⃣  Checking Python dependencies..."
if python3 -c "import httpx, websockets, rich" 2>/dev/null; then
    echo "✅ All dependencies installed"
else
    echo "❌ Missing dependencies"
    echo "Install with: pip install httpx websockets rich"
    exit 1
fi

echo ""
echo "3️⃣  Testing CLI script..."
if [ -f "bond_cli.py" ]; then
    echo "✅ bond_cli.py found"
else
    echo "❌ bond_cli.py not found"
    exit 1
fi

echo ""
echo "✅ All checks passed!"
echo ""
echo "Run the CLI with:"
echo "  python3 bond_cli.py"
echo ""
echo "Or install it globally with:"
echo "  pip install -e ."
echo "  bond"
echo ""

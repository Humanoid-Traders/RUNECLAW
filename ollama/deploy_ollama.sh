#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# RUNECLAW → Ollama Deployment Script (Option A)
# ══════════════════════════════════════════════════════════════════
# This deploys the RUNECLAW trading assistant as an Ollama model
# using the Modelfile system prompt approach.
#
# Prerequisites:
#   - Ollama installed: https://ollama.com/download
#   - Logged in: ollama login (if pushing to registry)
#
# Usage:
#   chmod +x deploy_ollama.sh
#   ./deploy_ollama.sh
# ══════════════════════════════════════════════════════════════════

set -euo pipefail

MODEL_NAME="pbdes2022/HUMANOID-TRADERS"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELFILE="$SCRIPT_DIR/Modelfile"

echo "══════════════════════════════════════════════════════════════"
echo "RUNECLAW Ollama Deployment"
echo "══════════════════════════════════════════════════════════════"

# Step 1: Check Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "ERROR: Ollama is not installed."
    echo "Install it from: https://ollama.com/download"
    echo ""
    echo "  Linux:   curl -fsSL https://ollama.com/install.sh | sh"
    echo "  macOS:   brew install ollama"
    echo "  Windows: Download from https://ollama.com/download"
    exit 1
fi

# Step 2: Check Ollama is running
if ! ollama list &> /dev/null 2>&1; then
    echo "Starting Ollama service..."
    ollama serve &
    sleep 3
fi

# Step 3: Pull base model if needed
echo ""
echo "[1/4] Checking base model (llama3.2)..."
if ollama list | grep -q "llama3.2"; then
    echo "  Base model llama3.2 already available."
else
    echo "  Pulling llama3.2 (2GB download)..."
    ollama pull llama3.2
fi

# Step 4: Create the RUNECLAW model
echo ""
echo "[2/4] Creating model: $MODEL_NAME"
echo "  Using Modelfile: $MODELFILE"
ollama create "$MODEL_NAME" -f "$MODELFILE"
echo "  Model created successfully."

# Step 5: Test the model
echo ""
echo "[3/4] Testing model with a sample prompt..."
echo ""
RESPONSE=$(ollama run "$MODEL_NAME" "Analyze BTC/USDT. RSI is 28, MACD histogram is positive, price is at the 61.8% Fibonacci retracement level. ADX is 32 with +DI > -DI. What do you see?" 2>&1)
echo "$RESPONSE"
echo ""

# Step 6: Push to Ollama registry
echo "[4/4] Push to Ollama registry?"
echo "  This makes the model available at: https://ollama.com/$MODEL_NAME"
echo ""
read -p "  Push now? (y/N): " PUSH_CONFIRM
if [[ "$PUSH_CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Pushing $MODEL_NAME..."
    ollama push "$MODEL_NAME"
    echo "  Pushed successfully!"
    echo "  URL: https://ollama.com/$MODEL_NAME"
else
    echo "  Skipped. Push later with: ollama push $MODEL_NAME"
fi

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "DONE"
echo ""
echo "Run your model:"
echo "  ollama run $MODEL_NAME"
echo ""
echo "Use via API:"
echo "  curl http://localhost:11434/api/generate -d '{"
echo "    \"model\": \"$MODEL_NAME\","
echo "    \"prompt\": \"Scan BTC/USDT for trade setups\""
echo "  }'"
echo "══════════════════════════════════════════════════════════════"

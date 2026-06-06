#!/bin/bash
# ══════════════════════════════════════════════════════════════
# RUNECLAW Local Training Setup — Linux/WSL
# ══════════════════════════════════════════════════════════════
# Run this script ONCE to set up your training environment.
# After setup, run: python train_local.py
# ══════════════════════════════════════════════════════════════

set -euo pipefail

echo "══════════════════════════════════════════════════════════════"
echo "RUNECLAW Local Training Setup"
echo "══════════════════════════════════════════════════════════════"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found."
    echo "  Ubuntu: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi
echo "[OK] $(python3 --version)"

# Check NVIDIA GPU
if ! command -v nvidia-smi &> /dev/null; then
    echo "ERROR: nvidia-smi not found."
    echo "  Install NVIDIA drivers: sudo apt install nvidia-driver-535"
    exit 1
fi
echo "[OK] $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
echo ""

# Create virtual environment
echo "[1/4] Creating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  Created venv/"
else
    echo "  venv/ already exists, skipping."
fi

source venv/bin/activate
echo "[OK] Virtual environment activated"
echo ""

# Install PyTorch with CUDA
echo "[2/4] Installing PyTorch with CUDA 12.1..."
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 -q

# Install training dependencies
echo "[3/4] Installing unsloth and training libraries..."
pip install "unsloth[cu121-torch250] @ git+https://github.com/unslothai/unsloth.git" -q
pip install datasets trl peft accelerate bitsandbytes -q

# Verify
echo ""
echo "[4/4] Verifying installation..."
python -c "import torch; print(f'PyTorch {torch.__version__}, CUDA: {torch.cuda.is_available()}, GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"None\"}')"

echo ""
echo "══════════════════════════════════════════════════════════════"
echo "Setup complete!"
echo ""
echo "To train, run:"
echo "  source venv/bin/activate"
echo "  python train_local.py"
echo ""
echo "Make sure combined_training.jsonl is in this directory"
echo "or in a training_data/ subdirectory."
echo "══════════════════════════════════════════════════════════════"

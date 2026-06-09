#!/usr/bin/env python3
"""
RUNECLAW - Import into Ollama Directly from Safetensors
========================================================
Skips GGUF conversion entirely — Ollama handles it internally.
This is the simplest and most reliable import method.

Usage:
  python create_ollama_direct.py

Requires:
  - ./runeclaw-model-merged/  (from export_model.py)
  - Ollama installed and running

What it does:
  1. Creates a Modelfile pointing to the merged safetensors directory
  2. Runs 'ollama create' which converts internally
  3. Tests the model
"""

import os
import sys
import subprocess

MERGED_DIR = "./runeclaw-model-merged"
MODEL_TAG = "runeclaw"

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)


def main():
    print("=" * 60)
    print("RUNECLAW - Direct Ollama Import (No GGUF needed)")
    print("=" * 60)

    # Check merged model exists
    if not os.path.isdir(MERGED_DIR):
        print(f"\nERROR: {MERGED_DIR} not found!")
        print("Run export_model.py first.")
        sys.exit(1)

    # Verify required files
    config_path = os.path.join(MERGED_DIR, "config.json")
    if not os.path.exists(config_path):
        print(f"ERROR: {config_path} not found!")
        sys.exit(1)

    st_files = [f for f in os.listdir(MERGED_DIR) if f.endswith(".safetensors")]
    print(f"\nMerged model: {MERGED_DIR}")
    print(f"Safetensors files: {len(st_files)}")

    # Check Ollama is available
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
        print(f"Ollama: {result.stdout.strip()}")
    except FileNotFoundError:
        print("\nERROR: 'ollama' not found in PATH!")
        print("Install from https://ollama.com")
        sys.exit(1)

    # Step 1: Remove old model if exists
    print(f"\n[1/3] Removing old model (if any)...")
    subprocess.run(["ollama", "rm", MODEL_TAG], capture_output=True, text=True)

    # Step 2: Create Modelfile pointing to safetensors directory
    print(f"\n[2/3] Creating Modelfile...")
    modelfile_path = os.path.join(MERGED_DIR, "Modelfile")
    with open(modelfile_path, "w") as f:
        f.write(f"FROM {MERGED_DIR}\n\n")
        f.write("PARAMETER temperature 0.3\n")
        f.write("PARAMETER top_p 0.9\n")
        f.write("PARAMETER num_ctx 4096\n")
        f.write('PARAMETER stop "<|eot_id|>"\n')
        f.write('PARAMETER stop "<|end|>"\n\n')
        f.write(f'SYSTEM """{SYSTEM_PROMPT}"""\n')

    print(f"  Modelfile: {modelfile_path}")
    print(f"  FROM: {MERGED_DIR} (safetensors — Ollama converts internally)")

    # Step 3: Run ollama create
    print(f"\n[3/3] Creating Ollama model '{MODEL_TAG}'...")
    print("  (Ollama will convert safetensors → GGUF internally)")
    print("  This may take a few minutes...\n")

    result = subprocess.run(
        ["ollama", "create", MODEL_TAG, "-f", modelfile_path],
        text=True,
    )

    if result.returncode != 0:
        print(f"\n  ERROR: ollama create failed (exit code {result.returncode})")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print("SUCCESS! Model imported into Ollama.")
    print(f"{'=' * 60}")
    print(f"""
Test it now:

  ollama run {MODEL_TAG} "Scan BTC/USDT for trade setups"

To push to Ollama registry under your namespace:

  ollama cp {MODEL_TAG} pbdes2022/humanoid-traders
  ollama push pbdes2022/humanoid-traders
""")


if __name__ == "__main__":
    main()

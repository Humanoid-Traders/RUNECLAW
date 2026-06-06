#!/usr/bin/env python3
r"""
RUNECLAW - Export Merged Model from Checkpoint
================================================
Run this FIRST after training completes.
Saves the LoRA-merged model as safetensors (no build tools needed).
Uses transformers + peft directly (does NOT require unsloth).

Usage:
  venv\Scripts\activate
  python export_model.py

Output:
  ./runeclaw-model-merged/   (safetensors + tokenizer)
"""

import os
import sys
import glob
import json
import torch


def find_checkpoint():
    """Find the latest checkpoint directory."""
    patterns = [
        "./runeclaw-checkpoints/checkpoint-*",
        "./runeclaw-model/checkpoint-*",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def find_base_model(checkpoint_dir):
    """Read the base model name from the adapter config."""
    config_path = os.path.join(checkpoint_dir, "adapter_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
        base = config.get("base_model_name_or_path", "")
        if base:
            return base

    # Fallback: check for common locations
    for name in ["adapter_model.safetensors", "adapter_model.bin"]:
        if os.path.exists(os.path.join(checkpoint_dir, name)):
            # Has adapter but no config — use default base
            return "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"

    return None


def main():
    print("=" * 60)
    print("RUNECLAW - Export Merged Model")
    print("=" * 60)

    checkpoint = find_checkpoint()
    if not checkpoint:
        print("\nERROR: No checkpoint directory found!")
        print("Expected: ./runeclaw-checkpoints/checkpoint-*/")
        sys.exit(1)

    print(f"\nFound checkpoint: {checkpoint}")

    # Detect base model
    base_model = find_base_model(checkpoint)
    if base_model:
        print(f"Base model: {base_model}")
    else:
        print("WARNING: Could not detect base model, using default")
        base_model = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"

    # ── Step 1: Load base model + LoRA adapter ─────────────────
    print("\n[1/3] Loading base model + LoRA adapter...")

    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    # Load in 4-bit (same as training)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    print("  Loading base model (4-bit)...")
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=bnb_config,
        device_map="auto",
        torch_dtype=torch.float16,
    )

    print("  Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"  Loading LoRA adapter from {checkpoint}...")
    model = PeftModel.from_pretrained(base, checkpoint)
    print("  Model loaded.")

    # ── Step 2: Merge and save ─────────────────────────────────
    print("\n[2/3] Merging LoRA weights and saving...")

    output_dir = "./runeclaw-model-merged"
    os.makedirs(output_dir, exist_ok=True)

    print("  Merging LoRA into base model...")
    model = model.merge_and_unload()

    print(f"  Saving to {output_dir}/ ...")
    model.save_pretrained(output_dir, safe_serialization=True)
    tokenizer.save_pretrained(output_dir)
    print("  Saved.")

    # ── Step 3: Verify ─────────────────────────────────────────
    print("\n[3/3] Verifying output...")

    total_size = 0
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))
    size_gb = total_size / 1024**3
    print(f"  Total size: {size_gb:.1f} GB")

    required = ["config.json", "tokenizer.json"]
    for fname in required:
        path = os.path.join(output_dir, fname)
        status = "OK" if os.path.exists(path) else "MISSING"
        print(f"  {status}: {fname}")

    safetensors = [f for f in os.listdir(output_dir) if f.endswith(".safetensors")]
    print(f"  Safetensors files: {len(safetensors)}")

    print(f"\n{'=' * 60}")
    print("Merged model saved successfully!")
    print(f"\nNext step:")
    print(f"  python convert_to_gguf.py")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

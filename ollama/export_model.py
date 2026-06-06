#!/usr/bin/env python3
"""
RUNECLAW — Export Merged Model from Checkpoint
================================================
Run this FIRST after training completes.
Saves the LoRA-merged model as safetensors (no build tools needed).

Usage:
  venv\Scripts\activate
  python export_model.py

Output:
  ./runeclaw-model-merged/   (safetensors + tokenizer)
"""

import os
import sys
import glob

def find_checkpoint():
    """Find the latest checkpoint directory."""
    patterns = [
        "./runeclaw-checkpoints/checkpoint-*",
        "./runeclaw-model/checkpoint-*",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]  # latest checkpoint
    return None

def main():
    print("=" * 60)
    print("RUNECLAW — Export Merged Model")
    print("=" * 60)

    checkpoint = find_checkpoint()
    if checkpoint:
        print(f"\nFound checkpoint: {checkpoint}")
    else:
        print("\nNo checkpoint found. Using base model + adapter...")
        checkpoint = None

    print("\n[1/3] Loading model from checkpoint...")
    from unsloth import FastLanguageModel

    if checkpoint:
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=checkpoint,
            max_seq_length=1024,
            load_in_4bit=True,
        )
    else:
        print("ERROR: No checkpoint directory found!")
        print("Expected: ./runeclaw-checkpoints/checkpoint-*/")
        sys.exit(1)

    print("  Model loaded.")

    print("\n[2/3] Merging LoRA weights into base model...")
    output_dir = "./runeclaw-model-merged"
    model.save_pretrained_merged(
        output_dir,
        tokenizer,
        save_method="merged_16bit",
    )
    print(f"  Saved to: {output_dir}")

    # Calculate size
    total_size = 0
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))
    size_gb = total_size / 1024**3
    print(f"  Total size: {size_gb:.1f} GB")

    print("\n[3/3] Verifying output...")
    required = ["config.json", "tokenizer.json"]
    for fname in required:
        path = os.path.join(output_dir, fname)
        if os.path.exists(path):
            print(f"  OK: {fname}")
        else:
            print(f"  MISSING: {fname}")

    safetensors = [f for f in os.listdir(output_dir) if f.endswith(".safetensors")]
    print(f"  Safetensors files: {len(safetensors)}")

    print(f"\n{'=' * 60}")
    print("Merged model saved successfully!")
    print(f"\nNext step:")
    print(f"  python convert_to_gguf.py")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

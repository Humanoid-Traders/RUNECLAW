r"""
RUNECLAW - Export Merged Model from Checkpoint
================================================
Uses transformers + peft (NOT unsloth).
If torch DLL fails, run this first:
  pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --force-reinstall

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
    return "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"


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

    base_model = find_base_model(checkpoint)
    print(f"Base model: {base_model}")

    # ── Test torch first ──────────────────────────────────────
    print("\n[0/3] Testing PyTorch...")
    try:
        import torch
        print(f"  PyTorch {torch.__version__}")
        if torch.cuda.is_available():
            print(f"  CUDA: {torch.cuda.get_device_name(0)}")
        else:
            print("  CUDA not available (will use CPU for merge - slower but works)")
    except ImportError as e:
        print(f"\n  ERROR: Cannot import torch: {e}")
        print("\n  FIX: Run this command first:")
        print("  pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --force-reinstall")
        sys.exit(1)

    # ── Step 1: Load base model + LoRA adapter ────────────────
    print("\n[1/3] Loading base model + LoRA adapter...")

    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import PeftModel

    device_map = "auto"
    load_kwargs = {}

    if torch.cuda.is_available():
        print("  Using GPU (4-bit loading)...")
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True,
        )
        load_kwargs["quantization_config"] = bnb_config
        load_kwargs["torch_dtype"] = torch.float16
    else:
        print("  Using CPU (16-bit loading, this will use ~6GB RAM)...")
        device_map = "cpu"
        load_kwargs["torch_dtype"] = torch.float16

    print(f"  Loading base: {base_model}")
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        device_map=device_map,
        **load_kwargs,
    )

    print("  Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"  Loading LoRA from {checkpoint}...")
    model = PeftModel.from_pretrained(base, checkpoint)
    print("  Loaded.")

    # ── Step 2: Merge and save ────────────────────────────────
    print("\n[2/3] Merging LoRA weights...")
    model = model.merge_and_unload()

    output_dir = "./runeclaw-model-merged"
    os.makedirs(output_dir, exist_ok=True)

    print(f"  Saving to {output_dir}/ ...")
    model.save_pretrained(output_dir, safe_serialization=True)
    tokenizer.save_pretrained(output_dir)
    print("  Saved.")

    # ── Step 3: Verify ────────────────────────────────────────
    print("\n[3/3] Verifying output...")

    total_size = 0
    for root, dirs, files in os.walk(output_dir):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))
    size_gb = total_size / 1024**3
    print(f"  Total size: {size_gb:.1f} GB")

    for fname in ["config.json", "tokenizer.json"]:
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

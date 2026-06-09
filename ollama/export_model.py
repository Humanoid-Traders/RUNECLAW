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
        "./runeclaw-8b-max-checkpoints/final-adapter",
        "./runeclaw-8b-max-checkpoints/checkpoint-*",
        "./runeclaw-8b-checkpoints/final-adapter",
        "./runeclaw-8b-checkpoints/checkpoint-*",
        "./runeclaw-checkpoints/final-adapter",
        "./runeclaw-checkpoints/checkpoint-*",
        "./runeclaw-model/checkpoint-*",
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


def find_base_model(checkpoint_dir):
    """Read the base model name from the adapter config.
    Maps quantized model names to full-precision equivalents
    (4-bit weights can't be loaded as float16 for export).
    """
    config_path = os.path.join(checkpoint_dir, "adapter_config.json")
    base = "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
        base = config.get("base_model_name_or_path", base)

    # Map quantized models to full-precision equivalents
    # LoRA adapters are compatible — they only contain delta weights
    QUANT_TO_FULL = {
        "unsloth/Llama-3.2-3B-Instruct-bnb-4bit": "unsloth/Llama-3.2-3B-Instruct",
        "unsloth/Llama-3.2-1B-Instruct-bnb-4bit": "unsloth/Llama-3.2-1B-Instruct",
        "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit": "unsloth/Meta-Llama-3.1-8B-Instruct",
        "unsloth/Llama-3.1-8B-Instruct-bnb-4bit": "unsloth/Meta-Llama-3.1-8B-Instruct",
    }

    if base in QUANT_TO_FULL:
        full = QUANT_TO_FULL[base]
        print(f"  Adapter trained on: {base}")
        print(f"  Using full-precision: {full}")
        print(f"  (4-bit weights can't be exported — full-precision is compatible)")
        return full

    # Generic pattern: strip -bnb-4bit suffix
    if "-bnb-4bit" in base:
        full = base.replace("-bnb-4bit", "")
        print(f"  Adapter trained on: {base}")
        print(f"  Using full-precision: {full}")
        return full

    return base


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
    print("  Loading in float16 (NOT 4-bit) so weights can be saved.")
    print("  This uses ~6GB RAM for 3B model — your 64GB handles it fine.")

    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    # Must load in float16 (NOT 4-bit) — 4-bit weights can't be saved back
    print(f"  Loading base: {base_model}")
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16,
        device_map="cpu",  # CPU to avoid VRAM limits, uses RAM instead
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

    # Save state dict directly (bypasses broken revert_weight_conversion)
    from safetensors.torch import save_file
    state_dict = model.state_dict()

    # Split into shards if large (>2GB per shard)
    shard_size = 2 * 1024**3  # 2GB
    total_bytes = sum(v.numel() * v.element_size() for v in state_dict.values())

    if total_bytes > shard_size:
        # Multi-shard save
        current_shard = {}
        current_bytes = 0
        shard_idx = 1
        shard_files = []
        weight_map = {}

        for key, tensor in state_dict.items():
            tensor_bytes = tensor.numel() * tensor.element_size()
            if current_bytes + tensor_bytes > shard_size and current_shard:
                fname = f"model-{shard_idx:05d}-of-PLACEHOLDER.safetensors"
                shard_files.append(fname)
                save_file(current_shard, os.path.join(output_dir, fname))
                for k in current_shard:
                    weight_map[k] = fname
                print(f"    Saved shard {shard_idx} ({current_bytes / 1024**3:.1f} GB)")
                current_shard = {}
                current_bytes = 0
                shard_idx += 1
            current_shard[key] = tensor.contiguous().to(torch.float16)
            current_bytes += tensor_bytes

        if current_shard:
            fname = f"model-{shard_idx:05d}-of-PLACEHOLDER.safetensors"
            shard_files.append(fname)
            save_file(current_shard, os.path.join(output_dir, fname))
            for k in current_shard:
                weight_map[k] = fname
            print(f"    Saved shard {shard_idx} ({current_bytes / 1024**3:.1f} GB)")

        # Fix shard filenames (use os.replace for Windows compatibility)
        total_shards = len(shard_files)
        for i, old_name in enumerate(shard_files):
            new_name = old_name.replace("PLACEHOLDER", f"{total_shards:05d}")
            old_path = os.path.join(output_dir, old_name)
            new_path = os.path.join(output_dir, new_name)
            if os.path.exists(new_path):
                os.remove(new_path)
            os.replace(old_path, new_path)
            for k in weight_map:
                if weight_map[k] == old_name:
                    weight_map[k] = new_name

        # Write index
        index = {"metadata": {"total_size": total_bytes}, "weight_map": weight_map}
        with open(os.path.join(output_dir, "model.safetensors.index.json"), "w") as f:
            json.dump(index, f, indent=2)
    else:
        # Single file save
        clean_dict = {k: v.contiguous().to(torch.float16) for k, v in state_dict.items()}
        save_file(clean_dict, os.path.join(output_dir, "model.safetensors"))
        print(f"    Saved single file ({total_bytes / 1024**3:.1f} GB)")

    # Save config and tokenizer
    model.config.save_pretrained(output_dir)
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

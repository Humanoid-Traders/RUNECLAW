#!/usr/bin/env python3
r"""
RUNECLAW - Convert Merged Model to GGUF (Fully Offline)
=========================================================
Self-contained converter — no internet downloads needed.
Uses only pip packages already installed (gguf, safetensors, torch).

Usage:
  python convert_to_gguf.py

Requires:
  - ./runeclaw-model-merged/  (from export_model.py)
  - pip packages: gguf, safetensors, torch, numpy, sentencepiece

Output:
  ./runeclaw-model/unsloth.Q4_K_M.gguf
  ./runeclaw-model/Modelfile
"""

import os
import sys
import json
import struct
import shutil
import platform
import subprocess
import numpy as np

MODEL_DIR = "./runeclaw-model-merged"
OUTPUT_DIR = "./runeclaw-model"
LLAMA_CPP_DIR = "./llama-cpp-tools"

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)

# Llama HF → GGUF tensor name mapping
TENSOR_MAP = {
    "model.embed_tokens.weight": "token_embd.weight",
    "model.norm.weight": "output_norm.weight",
    "lm_head.weight": "output.weight",
}

LAYER_TENSOR_MAP = {
    "model.layers.{}.self_attn.q_proj.weight": "blk.{}.attn_q.weight",
    "model.layers.{}.self_attn.k_proj.weight": "blk.{}.attn_k.weight",
    "model.layers.{}.self_attn.v_proj.weight": "blk.{}.attn_v.weight",
    "model.layers.{}.self_attn.o_proj.weight": "blk.{}.attn_output.weight",
    "model.layers.{}.mlp.gate_proj.weight": "blk.{}.ffn_gate.weight",
    "model.layers.{}.mlp.up_proj.weight": "blk.{}.ffn_up.weight",
    "model.layers.{}.mlp.down_proj.weight": "blk.{}.ffn_down.weight",
    "model.layers.{}.input_layernorm.weight": "blk.{}.attn_norm.weight",
    "model.layers.{}.post_attention_layernorm.weight": "blk.{}.ffn_norm.weight",
}


def map_tensor_name(hf_name):
    """Map HuggingFace tensor name to GGUF name."""
    if hf_name in TENSOR_MAP:
        return TENSOR_MAP[hf_name]

    # Try layer patterns
    for hf_pattern, gguf_pattern in LAYER_TENSOR_MAP.items():
        # Extract layer number
        parts = hf_name.split(".")
        try:
            layer_idx = None
            for i, p in enumerate(parts):
                if p == "layers" and i + 1 < len(parts):
                    layer_idx = int(parts[i + 1])
                    break
            if layer_idx is not None:
                hf_test = hf_pattern.format(layer_idx)
                if hf_test == hf_name:
                    return gguf_pattern.format(layer_idx)
        except (ValueError, IndexError):
            continue

    return None


def convert_hf_to_gguf():
    """Convert HuggingFace safetensors model to F16 GGUF."""
    from gguf import GGUFWriter, GGMLQuantizationType
    from safetensors import safe_open

    print("\n[1/3] Reading model config...")

    config_path = os.path.join(MODEL_DIR, "config.json")
    with open(config_path) as f:
        config = json.load(f)

    # Extract architecture params
    arch = "llama"
    vocab_size = config.get("vocab_size", 128256)
    hidden_size = config.get("hidden_size", 3072)
    intermediate_size = config.get("intermediate_size", 8192)
    num_layers = config.get("num_hidden_layers", 28)
    num_heads = config.get("num_attention_heads", 24)
    num_kv_heads = config.get("num_key_value_heads", 8)
    head_dim = hidden_size // num_heads
    rms_eps = config.get("rms_norm_eps", 1e-5)
    rope_theta = config.get("rope_theta", 500000.0)
    max_pos = config.get("max_position_embeddings", 131072)
    bos_id = config.get("bos_token_id", 128000)
    eos_id = config.get("eos_token_id", 128001)

    print(f"  Architecture: {arch}")
    print(f"  Layers: {num_layers}, Hidden: {hidden_size}")
    print(f"  Heads: {num_heads}, KV Heads: {num_kv_heads}")
    print(f"  Vocab: {vocab_size}")

    # ── Set up GGUF writer ────────────────────────────────
    print("\n[2/3] Creating GGUF file...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    f16_path = os.path.join(OUTPUT_DIR, "model-f16.gguf")

    writer = GGUFWriter(f16_path, arch)

    # Write metadata
    writer.add_name("runeclaw-3b")
    writer.add_block_count(num_layers)
    writer.add_context_length(max_pos)
    writer.add_embedding_length(hidden_size)
    writer.add_feed_forward_length(intermediate_size)
    writer.add_head_count(num_heads)
    writer.add_head_count_kv(num_kv_heads)
    writer.add_layer_norm_rms_epsilon(rms_eps)
    writer.add_rope_freq_base(rope_theta)
    writer.add_file_type(GGMLQuantizationType.F16)

    # Write tokenizer
    print("  Loading tokenizer...")
    tokenizer_path = os.path.join(MODEL_DIR, "tokenizer.json")
    if os.path.exists(tokenizer_path):
        with open(tokenizer_path, "r", encoding="utf-8") as f:
            tokenizer_data = json.load(f)

        # Extract tokens
        model_data = tokenizer_data.get("model", {})
        vocab = model_data.get("vocab", {})

        if vocab:
            tokens = [""] * vocab_size
            scores = [0.0] * vocab_size
            token_types = [0] * vocab_size  # 0=normal, 1=unknown, 2=control, 3=user_defined

            for token_str, token_id in vocab.items():
                if token_id < vocab_size:
                    tokens[token_id] = token_str.encode("utf-8", errors="replace")
                    # Mark special tokens
                    if token_str.startswith("<|") or token_str.startswith("<s>") or token_str == "</s>":
                        token_types[token_id] = 2  # control

            writer.add_tokenizer_model("gpt2")
            writer.add_token_list(tokens)
            writer.add_token_scores(scores)
            writer.add_token_types(token_types)
            writer.add_bos_token_id(bos_id)
            writer.add_eos_token_id(eos_id)
            print(f"  Tokenizer: {len(vocab)} tokens")
        else:
            print("  WARNING: No vocab found in tokenizer.json")
            writer.add_tokenizer_model("gpt2")
    else:
        print("  WARNING: tokenizer.json not found")
        writer.add_tokenizer_model("gpt2")

    # ── Write tensors ─────────────────────────────────────
    print("  Loading and converting tensors...")

    # Find all safetensors files
    st_files = sorted([f for f in os.listdir(MODEL_DIR) if f.endswith(".safetensors")])
    print(f"  Found {len(st_files)} safetensors files")

    tensor_count = 0
    skipped = []

    for st_file in st_files:
        st_path = os.path.join(MODEL_DIR, st_file)
        print(f"  Processing {st_file}...")

        with safe_open(st_path, framework="numpy") as f:
            for hf_name in f.keys():
                gguf_name = map_tensor_name(hf_name)
                if gguf_name is None:
                    skipped.append(hf_name)
                    continue

                tensor = f.get_tensor(hf_name)

                # Convert to float16
                if tensor.dtype != np.float16:
                    tensor = tensor.astype(np.float16)

                writer.add_tensor(gguf_name, tensor)
                tensor_count += 1

                if tensor_count % 20 == 0:
                    print(f"    {tensor_count} tensors written...")

    if skipped:
        print(f"  Skipped {len(skipped)} unmapped tensors: {skipped[:5]}...")

    print(f"  Total tensors: {tensor_count}")

    # ── Finalize ──────────────────────────────────────────
    print("  Writing GGUF file (this takes a minute)...")
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_tensors_to_file()
    writer.close()

    size_gb = os.path.getsize(f16_path) / 1024**3
    print(f"  F16 GGUF: {f16_path} ({size_gb:.1f} GB)")

    return f16_path


def quantize(f16_path):
    """Quantize F16 → Q4_K_M using pre-built binary."""
    print("\n[3/3] Quantizing to Q4_K_M...")

    quantize_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    quantize_bin = None

    for root, dirs, files in os.walk(LLAMA_CPP_DIR):
        for f in files:
            if f == quantize_name:
                quantize_bin = os.path.join(root, f)
                break
        if quantize_bin:
            break

    q4_path = os.path.join(OUTPUT_DIR, "unsloth.Q4_K_M.gguf")

    if not quantize_bin or not os.path.exists(quantize_bin):
        print(f"  WARNING: {quantize_name} not found in {LLAMA_CPP_DIR}/")
        print(f"  Keeping F16 GGUF (usable but larger).")
        shutil.copy2(f16_path, q4_path)
        return q4_path

    if platform.system() != "Windows":
        os.chmod(quantize_bin, 0o755)

    print(f"  Using: {quantize_bin}")
    print(f"  Quantizing {f16_path} → {q4_path}...")

    result = subprocess.run(
        [quantize_bin, f16_path, q4_path, "Q4_K_M"],
        text=True,
    )

    if result.returncode == 0 and os.path.exists(q4_path):
        size_gb = os.path.getsize(q4_path) / 1024**3
        print(f"  Q4_K_M: {size_gb:.1f} GB")
        print(f"  Removing intermediate F16...")
        os.remove(f16_path)
        return q4_path

    print("  Quantization failed, keeping F16.")
    if not os.path.exists(q4_path):
        shutil.copy2(f16_path, q4_path)
    return q4_path


def create_modelfile(gguf_filename):
    """Create Ollama Modelfile."""
    path = os.path.join(OUTPUT_DIR, "Modelfile")
    with open(path, "w") as f:
        f.write(f'FROM ./{gguf_filename}\n\n')
        f.write('PARAMETER temperature 0.3\n')
        f.write('PARAMETER top_p 0.9\n')
        f.write('PARAMETER num_ctx 4096\n')
        f.write('PARAMETER stop "<|eot_id|>"\n')
        f.write('PARAMETER stop "<|end|>"\n\n')
        f.write(f'SYSTEM """{SYSTEM_PROMPT}"""\n')
    print(f"  Modelfile: {path}")


def main():
    print("=" * 60)
    print("RUNECLAW - Convert to GGUF (Fully Offline)")
    print("=" * 60)

    if not os.path.exists(MODEL_DIR):
        print(f"\nERROR: {MODEL_DIR} not found!")
        print("Run export_model.py first.")
        sys.exit(1)

    # Check deps
    print("\nChecking dependencies...")
    try:
        import gguf
        print(f"  gguf: OK")
    except ImportError:
        print("  Installing gguf...")
        subprocess.run([sys.executable, "-m", "pip", "install", "gguf", "-q"])

    try:
        from safetensors import safe_open
        print(f"  safetensors: OK")
    except ImportError:
        print("  Installing safetensors...")
        subprocess.run([sys.executable, "-m", "pip", "install", "safetensors", "-q"])

    print(f"\nInput:  {MODEL_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    # Convert
    f16_path = convert_hf_to_gguf()

    # Quantize
    final_path = quantize(f16_path)
    final_name = os.path.basename(final_path)

    # Modelfile
    create_modelfile(final_name)

    print(f"\n{'=' * 60}")
    print("DONE! GGUF conversion complete.")
    print(f"{'=' * 60}")
    print(f"""
Next steps:

  cd {OUTPUT_DIR}
  ollama create pbdes2022/HUMANOID-TRADERS -f Modelfile
  ollama run pbdes2022/HUMANOID-TRADERS "Scan BTC/USDT for trade setups"
  ollama push pbdes2022/HUMANOID-TRADERS
""")


if __name__ == "__main__":
    main()

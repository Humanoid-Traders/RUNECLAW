#!/usr/bin/env python3
"""
RUNECLAW — Merge Two Fine-Tuned Models
Merges Colab (Sonnet-trained) + Local (Haiku-trained) safetensors
into one combined model using mergekit SLERP interpolation.

Prerequisites:
  pip install mergekit torch safetensors

Usage:
  python merge_models.py --colab ./runeclaw-model-v2 --local ./runeclaw-local --output ./runeclaw-merged

Then convert to GGUF:
  python merge_models.py --convert ./runeclaw-merged
"""

import argparse
import os
import sys
import subprocess
import json


def check_deps():
    """Check and install required packages."""
    try:
        import mergekit
        print("  mergekit: OK")
    except ImportError:
        print("  Installing mergekit...")
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "mergekit", "--quiet"])

    try:
        import yaml
        print("  pyyaml: OK")
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "pyyaml", "--quiet"])


def find_model_files(path):
    """Check what model files exist in a directory."""
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        print(f"  ERROR: {path} is not a directory")
        return None

    files = os.listdir(path)
    safetensors = [f for f in files if f.endswith('.safetensors')]
    gguf = [f for f in files if f.endswith('.gguf')]
    has_config = 'config.json' in files
    has_adapter = 'adapter_config.json' in files

    print(f"\n  Path: {path}")
    print(f"  Safetensors: {len(safetensors)} files")
    print(f"  GGUF: {len(gguf)} files")
    print(f"  config.json: {'YES' if has_config else 'NO'}")
    print(f"  adapter_config.json: {'YES (LoRA adapter)' if has_adapter else 'NO (full model)'}")

    total_size = sum(os.path.getsize(os.path.join(path, f))
                     for f in safetensors) / 1024**3
    print(f"  Total size: {total_size:.2f} GB")

    return {
        "path": path,
        "safetensors": safetensors,
        "gguf": gguf,
        "has_config": has_config,
        "is_adapter": has_adapter,
        "size_gb": total_size,
    }


def create_merge_config(colab_path, local_path, output_path, method="slerp",
                         colab_weight=0.6):
    """Create mergekit YAML config.

    Default: 60% Colab (Sonnet) / 40% Local (Haiku)
    Sonnet-trained data is generally higher quality, so it gets more weight.
    """
    local_weight = round(1.0 - colab_weight, 2)

    if method == "slerp":
        config = {
            "slices": [{
                "sources": [
                    {"model": colab_path, "layer_range": [0, 32]},
                    {"model": local_path, "layer_range": [0, 32]},
                ]
            }],
            "merge_method": "slerp",
            "base_model": colab_path,
            "parameters": {
                "t": colab_weight,  # interpolation factor toward first model
            },
            "dtype": "bfloat16",
        }
    elif method == "linear":
        config = {
            "models": [
                {"model": colab_path, "parameters": {"weight": colab_weight}},
                {"model": local_path, "parameters": {"weight": local_weight}},
            ],
            "merge_method": "linear",
            "dtype": "bfloat16",
        }
    elif method == "ties":
        config = {
            "models": [
                {"model": colab_path, "parameters": {"weight": colab_weight, "density": 0.5}},
                {"model": local_path, "parameters": {"weight": local_weight, "density": 0.5}},
            ],
            "merge_method": "ties",
            "base_model": "unsloth/Meta-Llama-3.1-8B-Instruct",
            "parameters": {"normalize": True},
            "dtype": "bfloat16",
        }
    else:
        raise ValueError(f"Unknown method: {method}")

    config_path = os.path.join(output_path, "merge_config.yml")
    os.makedirs(output_path, exist_ok=True)

    import yaml
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    print(f"\n  Merge config saved: {config_path}")
    print(f"  Method: {method.upper()}")
    print(f"  Colab (Sonnet): {colab_weight*100:.0f}%")
    print(f"  Local (Haiku):  {local_weight*100:.0f}%")

    return config_path


def run_merge(config_path, output_path):
    """Run mergekit-yaml merge."""
    print(f"\n{'='*55}")
    print(f"  Starting model merge...")
    print(f"{'='*55}")
    print(f"  This may take 10-30 minutes depending on your RAM.\n")

    cmd = [
        sys.executable, "-m", "mergekit.scripts.yamlmerge",
        config_path,
        output_path,
        "--allow-crimes",  # allow merging different fine-tunes
        "--copy-tokenizer",
        "--lazy-unpickle",
    ]

    try:
        result = subprocess.run(cmd, capture_output=False, text=True)
        if result.returncode == 0:
            print(f"\n  Merge complete! Output: {output_path}")
            return True
        else:
            print(f"\n  Merge failed with code {result.returncode}")
            # Try alternative command format
            cmd_alt = [
                "mergekit-yaml",
                config_path,
                output_path,
                "--allow-crimes",
                "--copy-tokenizer",
                "--lazy-unpickle",
            ]
            print("  Trying alternative command...")
            result2 = subprocess.run(cmd_alt, capture_output=False, text=True)
            return result2.returncode == 0
    except Exception as e:
        print(f"  Error: {e}")
        return False


def convert_to_gguf(model_path):
    """Convert merged model to GGUF using llama.cpp."""
    print(f"\n{'='*55}")
    print(f"  Converting to GGUF...")
    print(f"{'='*55}")

    # Check for llama.cpp convert script
    convert_paths = [
        os.path.expanduser("~/llama.cpp/convert_hf_to_gguf.py"),
        os.path.expanduser("~/llama.cpp/convert.py"),
        "convert_hf_to_gguf.py",
    ]

    convert_script = None
    for p in convert_paths:
        if os.path.exists(p):
            convert_script = p
            break

    if not convert_script:
        print("  llama.cpp not found. Install it first:")
        print("    git clone https://github.com/ggerganov/llama.cpp")
        print("    cd llama.cpp && make")
        print("  Then run:")
        print(f"    python convert_hf_to_gguf.py {model_path}")
        print(f"    ./llama-quantize {model_path}/ggml-model-f16.gguf "
              f"{model_path}/runeclaw-merged.Q4_K_M.gguf Q4_K_M")
        return False

    # Convert to f16
    cmd = [sys.executable, convert_script, model_path, "--outtype", "f16"]
    subprocess.run(cmd)

    # Quantize to Q4_K_M
    quantize_bin = os.path.join(os.path.dirname(convert_script), "llama-quantize")
    if not os.path.exists(quantize_bin):
        quantize_bin = os.path.join(os.path.dirname(convert_script), "quantize")

    f16_gguf = os.path.join(model_path, "ggml-model-f16.gguf")
    q4_gguf = os.path.join(model_path, "runeclaw-merged.Q4_K_M.gguf")

    if os.path.exists(quantize_bin) and os.path.exists(f16_gguf):
        subprocess.run([quantize_bin, f16_gguf, q4_gguf, "Q4_K_M"])
        print(f"\n  Q4_K_M GGUF: {q4_gguf}")
        if os.path.exists(q4_gguf):
            size = os.path.getsize(q4_gguf) / 1024**3
            print(f"  Size: {size:.2f} GB")
    return True


def create_modelfile(output_path, gguf_name="runeclaw-merged.Q4_K_M.gguf"):
    """Create Ollama Modelfile for the merged model."""
    modelfile = f"""FROM ./{gguf_name}

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
PARAMETER stop "<|eot_id|>"
PARAMETER stop "<|end|>"

SYSTEM \"\"\"You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency markets using the GetClaw Confluence Engine (12 weighted indicators: RSI-14 w=1.5, MACD w=1.0, Bollinger w=1.2, EMA Cross w=1.0, Volume Profile w=1.3, OBV w=0.8, Stoch RSI w=0.7, ADX w=1.1, Ichimoku w=0.9, VWAP w=1.4, ATR w=0.8, Fibonacci w=1.0), enforce strict risk management through 22 automated checks (all must pass, fail-closed), and generate structured trade ideas. You never execute without human confirmation. Capital preservation above all.\"\"\"
"""
    mf_path = os.path.join(output_path, "Modelfile")
    with open(mf_path, "w") as f:
        f.write(modelfile)
    print(f"  Modelfile: {mf_path}")


def main():
    parser = argparse.ArgumentParser(description="RUNECLAW Model Merger")
    parser.add_argument("--colab", help="Path to Colab (Sonnet) model safetensors")
    parser.add_argument("--local", help="Path to local (Haiku) model safetensors")
    parser.add_argument("--output", default="./runeclaw-merged",
                        help="Output directory (default: ./runeclaw-merged)")
    parser.add_argument("--method", default="slerp",
                        choices=["slerp", "linear", "ties"],
                        help="Merge method (default: slerp)")
    parser.add_argument("--colab-weight", type=float, default=0.6,
                        help="Weight for Colab model (default: 0.6 = 60%%)")
    parser.add_argument("--convert", metavar="PATH",
                        help="Convert a model directory to GGUF (skip merge)")
    parser.add_argument("--scan", metavar="PATH",
                        help="Scan a directory to check model files")
    args = parser.parse_args()

    print("=" * 55)
    print("  RUNECLAW — Model Merger")
    print("=" * 55)

    # Scan mode
    if args.scan:
        find_model_files(args.scan)
        return

    # Convert-only mode
    if args.convert:
        convert_to_gguf(args.convert)
        create_modelfile(args.convert)
        return

    # Merge mode
    if not args.colab or not args.local:
        print("\n  Usage:")
        print("    python merge_models.py --colab ./runeclaw-model-v2 --local ./runeclaw-local")
        print("\n  First, scan your model directories:")
        print("    python merge_models.py --scan ./runeclaw-model-v2")
        print("    python merge_models.py --scan ./runeclaw-local")
        return

    check_deps()

    print("\n  Scanning models...")
    colab_info = find_model_files(args.colab)
    local_info = find_model_files(args.local)

    if not colab_info or not local_info:
        print("\n  ERROR: Cannot find model files. Check paths.")
        return

    if colab_info["is_adapter"] or local_info["is_adapter"]:
        print("\n  WARNING: One or both models are LoRA adapters.")
        print("  For mergekit, you need the FULL merged safetensors (not just adapters).")
        print("  If you have the adapter, merge it with the base model first:")
        print("    from peft import PeftModel")
        print("    model = PeftModel.from_pretrained(base_model, adapter_path)")
        print("    model = model.merge_and_unload()")
        print("    model.save_pretrained('./full_model')")
        return

    # Create config and run merge
    config_path = create_merge_config(
        args.colab, args.local, args.output,
        method=args.method,
        colab_weight=args.colab_weight,
    )

    success = run_merge(config_path, args.output)

    if success:
        print(f"\n{'='*55}")
        print(f"  MERGE COMPLETE")
        print(f"{'='*55}")

        # List output files
        for fn in sorted(os.listdir(args.output)):
            fp = os.path.join(args.output, fn)
            if os.path.isfile(fp):
                sz = os.path.getsize(fp)
                if sz > 1024**2:
                    print(f"  {fn}: {sz/1024**3:.2f} GB")
                else:
                    print(f"  {fn}: {sz/1024:.1f} KB")

        print(f"\n  Next steps:")
        print(f"  1. Convert to GGUF:")
        print(f"     python merge_models.py --convert {args.output}")
        print(f"  2. Or use Unsloth in Python:")
        print(f"     model = FastLanguageModel.from_pretrained('{args.output}')")
        print(f"     model.save_pretrained_gguf('./gguf', tokenizer, 'q4_k_m')")
        print(f"  3. Load in Ollama:")
        print(f"     ollama create runeclaw-merged -f Modelfile")
    else:
        print("\n  Merge failed. Check errors above.")


if __name__ == "__main__":
    main()

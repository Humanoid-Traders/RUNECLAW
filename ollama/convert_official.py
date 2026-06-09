#!/usr/bin/env python3
"""
RUNECLAW - Convert using llama.cpp's Official Script
=====================================================
Clones llama.cpp repo (shallow) and runs convert_hf_to_gguf.py — the official,
battle-tested converter guaranteed to work with Ollama.

Usage:
  python convert_official.py

Requires:
  - ./runeclaw-model-merged/  (from export_model.py)
  - Internet access (to clone llama.cpp repo)
  - git installed
  - pip packages: gguf, safetensors, torch, numpy, sentencepiece
"""

import os
import sys
import subprocess
import shutil

MODEL_DIR = "./runeclaw-model-merged"
OUTPUT_DIR = "./runeclaw-model"
LLAMA_CPP_DIR = "./llama-cpp-tools"
LLAMA_CPP_REPO = "./llama-cpp-repo"

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)

CONVERT_SCRIPT = "convert_hf_to_gguf.py"


def setup_converter():
    """Clone llama.cpp repo (shallow) to get the full converter with dependencies."""
    print("\n[1/4] Setting up llama.cpp converter...")

    script_path = os.path.join(LLAMA_CPP_REPO, CONVERT_SCRIPT)

    if os.path.exists(script_path):
        print(f"  llama.cpp repo already cloned, pulling latest...")
        subprocess.run(["git", "-C", LLAMA_CPP_REPO, "pull"], check=False)
        return script_path

    print("  Cloning llama.cpp repo (shallow — only latest commit)...")
    result = subprocess.run(
        [
            "git", "clone", "--depth", "1",
            "https://github.com/ggerganov/llama.cpp.git",
            LLAMA_CPP_REPO,
        ],
        text=True,
    )

    if result.returncode != 0:
        print("  ERROR: git clone failed!")
        print("  Make sure git is installed and you have internet access.")
        return None

    if not os.path.exists(script_path):
        print(f"  ERROR: {CONVERT_SCRIPT} not found in cloned repo!")
        return None

    print(f"  Cloned successfully.")
    return script_path


def install_deps():
    """Install required packages for the converter."""
    print("\n[2/4] Installing dependencies...")

    # Install llama.cpp repo's own requirements first
    req_file = os.path.join(LLAMA_CPP_REPO, "requirements.txt")
    if os.path.exists(req_file):
        print("  Installing llama.cpp requirements.txt...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req_file, "-q"],
            check=False,
        )

    # Also install convert-specific requirements if they exist
    for req_name in ["requirements", "requirements-convert"]:
        rpath = os.path.join(LLAMA_CPP_REPO, f"{req_name}.txt")
        if os.path.exists(rpath) and rpath != req_file:
            print(f"  Installing {req_name}.txt...")
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", rpath, "-q"],
                check=False,
            )

    # Ensure key packages are present
    deps = ["gguf", "safetensors", "numpy", "sentencepiece", "transformers"]
    for dep in deps:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", dep, "--upgrade", "-q"],
            check=False,
        )
    print("  Dependencies ready.")


def convert(script_path):
    """Run the official converter from within the llama.cpp repo."""
    print(f"\n[3/4] Converting {MODEL_DIR} to GGUF...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    f16_path = os.path.join(OUTPUT_DIR, "model-f16.gguf")

    # Use absolute paths since we'll cd into the repo
    abs_model_dir = os.path.abspath(MODEL_DIR)
    abs_f16_path = os.path.abspath(f16_path)
    abs_repo_dir = os.path.abspath(LLAMA_CPP_REPO)

    # Remove old files
    for old in ["model-f16.gguf", "unsloth.Q4_K_M.gguf"]:
        old_path = os.path.join(OUTPUT_DIR, old)
        if os.path.exists(old_path):
            os.remove(old_path)

    # Clean old Ollama model
    subprocess.run(["ollama", "rm", "runeclaw"], capture_output=True, text=True)

    # Run the official converter FROM the repo directory
    # so that 'from conversion import ...' resolves correctly
    cmd = [
        sys.executable, CONVERT_SCRIPT,
        abs_model_dir,
        "--outfile", abs_f16_path,
        "--outtype", "f16",
    ]

    print(f"  Running from: {abs_repo_dir}")
    print(f"  Command: {' '.join(cmd)}")
    result = subprocess.run(cmd, text=True, cwd=abs_repo_dir)

    if result.returncode != 0:
        print(f"\n  ERROR: Converter failed (exit code {result.returncode})")
        return None

    if not os.path.exists(f16_path):
        print(f"  ERROR: {f16_path} not created!")
        return None

    size_gb = os.path.getsize(f16_path) / 1024**3
    print(f"  F16 GGUF: {f16_path} ({size_gb:.1f} GB)")
    return f16_path


def quantize(f16_path):
    """Quantize F16 → Q4_K_M."""
    print(f"\n[4/4] Quantizing to Q4_K_M...")

    import platform
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
        print(f"  {quantize_name} not found, using F16 directly.")
        return f16_path

    if platform.system() != "Windows":
        os.chmod(quantize_bin, 0o755)

    print(f"  {f16_path} -> {q4_path}...")
    result = subprocess.run(
        [quantize_bin, f16_path, q4_path, "Q4_K_M"],
        text=True,
    )

    if result.returncode == 0 and os.path.exists(q4_path):
        size_gb = os.path.getsize(q4_path) / 1024**3
        print(f"  Q4_K_M: {size_gb:.1f} GB")
        os.remove(f16_path)
        return q4_path

    print("  Quantization failed, using F16.")
    return f16_path


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
    print("RUNECLAW - Convert using Official llama.cpp Script")
    print("=" * 60)

    if not os.path.exists(MODEL_DIR):
        print(f"\nERROR: {MODEL_DIR} not found!")
        print("Run export_model.py first.")
        sys.exit(1)

    # Step 1: Clone llama.cpp repo
    script_path = setup_converter()
    if not script_path:
        print("\nERROR: Failed to set up converter.")
        print("Make sure git is installed and you have internet access.")
        sys.exit(1)

    # Step 2: Install dependencies
    install_deps()

    # Step 3: Convert
    f16_path = convert(script_path)
    if not f16_path:
        sys.exit(1)

    # Step 4: Quantize
    final_path = quantize(f16_path)
    final_name = os.path.basename(final_path)

    # Modelfile
    create_modelfile(final_name)

    print(f"\n{'=' * 60}")
    print("DONE!")
    print(f"{'=' * 60}")
    print(f"""
Next steps:

  cd {OUTPUT_DIR}
  ollama create runeclaw -f Modelfile
  ollama run runeclaw "Scan BTC/USDT for trade setups"

To push to registry:
  ollama cp runeclaw pbdes2022/humanoid-traders
  ollama push pbdes2022/humanoid-traders
""")


if __name__ == "__main__":
    main()

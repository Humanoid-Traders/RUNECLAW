#!/usr/bin/env python3
r"""
RUNECLAW - Convert Merged Model to GGUF (No Build Tools)
==========================================================
Downloads pre-built llama.cpp and converts the merged
safetensors model to GGUF Q4_K_M format.

NO cmake, Visual Studio, or build tools required.

Usage:
  venv\Scripts\activate
  python convert_to_gguf.py

Requires:
  - ./runeclaw-model-merged/  (from export_model.py)
  - Internet connection (downloads llama.cpp source + binaries)

Output:
  ./runeclaw-model/unsloth.Q4_K_M.gguf
  ./runeclaw-model/Modelfile
"""

import os
import sys
import json
import shutil
import zipfile
import platform
import subprocess
import urllib.request

MODEL_MERGED_DIR = "./runeclaw-model-merged"
OUTPUT_DIR = "./runeclaw-model"
LLAMA_CPP_DIR = "./llama-cpp-tools"
LLAMA_CPP_SRC = "./llama-cpp-src"

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators), "
    "enforce strict risk management through 23 automated checks, and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)


def download_file(url, dest, desc=""):
    """Download a file with progress."""
    print(f"  Downloading {desc or url}...")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        print(f"\r    {downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB ({pct:.0f}%)", end="", flush=True)
            print()
        return True
    except Exception as e:
        print(f"\n  ERROR downloading: {e}")
        return False


def get_latest_release_url():
    """Get the latest llama.cpp release binary URL."""
    print("  Finding latest llama.cpp release...")
    api_url = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR: {e}")
        return None, None

    tag = data.get("tag_name", "")
    print(f"  Latest release: {tag}")

    is_win = platform.system() == "Windows"
    for asset in data.get("assets", []):
        name = asset["name"].lower()
        url = asset["browser_download_url"]
        if is_win and "win" in name and "x64" in name and name.endswith(".zip"):
            if "cuda" not in name and "vulkan" not in name and "opencl" not in name:
                return url, asset["name"]
        if not is_win and "linux" in name and "x64" in name and name.endswith(".zip"):
            if "cuda" not in name and "vulkan" not in name and "opencl" not in name:
                return url, asset["name"]

    # Fallback
    for asset in data.get("assets", []):
        name = asset["name"].lower()
        if is_win and "win" in name and name.endswith(".zip"):
            return asset["browser_download_url"], asset["name"]
    return None, None


def setup_binaries():
    """Download pre-built llama-quantize binary."""
    print("\n[1/5] Downloading pre-built llama.cpp binaries...")
    os.makedirs(LLAMA_CPP_DIR, exist_ok=True)

    quantize_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    quantize_path = os.path.join(LLAMA_CPP_DIR, quantize_name)
    if os.path.exists(quantize_path):
        print(f"  Already have {quantize_name}.")
        return True

    url, filename = get_latest_release_url()
    if not url:
        print("  ERROR: Could not find release binary.")
        return False

    zip_path = os.path.join(LLAMA_CPP_DIR, filename)
    if not download_file(url, zip_path, f"llama.cpp binaries"):
        return False

    print("  Extracting...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(LLAMA_CPP_DIR)

    # Find quantize binary
    for root, dirs, files in os.walk(LLAMA_CPP_DIR):
        for f in files:
            if f == quantize_name:
                src = os.path.join(root, f)
                if src != quantize_path:
                    shutil.copy2(src, quantize_path)
                print(f"  Found {quantize_name}")
                break

    os.remove(zip_path)
    return os.path.exists(quantize_path)


def setup_converter():
    """Download llama.cpp source (as zip, no git needed) for convert script."""
    print("\n[2/5] Downloading llama.cpp conversion scripts...")

    convert_script = os.path.join(LLAMA_CPP_SRC, "convert_hf_to_gguf.py")
    if os.path.exists(convert_script):
        print("  Already have conversion scripts.")
        return True

    # Download repo as zip (no git required!)
    src_url = "https://github.com/ggerganov/llama.cpp/archive/refs/heads/master.zip"
    zip_path = "./llama-cpp-master.zip"

    if not download_file(src_url, zip_path, "llama.cpp source"):
        return False

    print("  Extracting conversion scripts...")
    os.makedirs(LLAMA_CPP_SRC, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Extract only what we need
        for info in zf.infolist():
            # We need: convert_hf_to_gguf.py, gguf-py/, and requirements
            name = info.filename
            if name.startswith("llama.cpp-master/"):
                rel = name[len("llama.cpp-master/"):]
                if not rel:
                    continue
                # Key files and directories
                keep = (
                    rel == "convert_hf_to_gguf.py" or
                    rel.startswith("gguf-py/") or
                    rel == "requirements.txt" or
                    rel.startswith("convert_hf_to_gguf_update.py") or
                    rel.startswith("scripts/") or
                    rel == "requirements/requirements-convert_hf_to_gguf.txt"
                )
                if keep:
                    # Extract to LLAMA_CPP_SRC with correct relative path
                    target = os.path.join(LLAMA_CPP_SRC, rel)
                    if info.is_dir():
                        os.makedirs(target, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target), exist_ok=True)
                        with zf.open(info) as src, open(target, "wb") as dst:
                            dst.write(src.read())

    os.remove(zip_path)

    if os.path.exists(convert_script):
        print("  Conversion scripts ready.")
        return True
    else:
        print("  WARNING: convert_hf_to_gguf.py not found in extraction.")
        return False


def install_deps():
    """Install Python dependencies for conversion."""
    print("\n[3/5] Installing conversion dependencies...")

    # Install gguf from the llama.cpp source (has the right version)
    gguf_dir = os.path.join(LLAMA_CPP_SRC, "gguf-py")
    if os.path.exists(gguf_dir):
        print("  Installing gguf from llama.cpp source...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", gguf_dir, "-q"],
            capture_output=True, text=True,
        )
    else:
        print("  Installing gguf from PyPI...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "gguf", "-q"],
            capture_output=True, text=True,
        )

    # Install other deps
    req_file = os.path.join(LLAMA_CPP_SRC, "requirements", "requirements-convert_hf_to_gguf.txt")
    if os.path.exists(req_file):
        print(f"  Installing from {req_file}...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req_file, "-q"],
            capture_output=True, text=True,
        )

    # Also ensure numpy and sentencepiece
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "numpy", "sentencepiece", "-q"],
        capture_output=True, text=True,
    )
    print("  Dependencies installed.")
    return True


def convert_to_f16():
    """Convert safetensors to F16 GGUF using llama.cpp's converter."""
    print("\n[4/5] Converting to F16 GGUF...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    f16_path = os.path.join(OUTPUT_DIR, "model-f16.gguf")

    convert_script = os.path.join(LLAMA_CPP_SRC, "convert_hf_to_gguf.py")

    if not os.path.exists(convert_script):
        print("  ERROR: convert_hf_to_gguf.py not found!")
        return None

    abs_model = os.path.abspath(MODEL_MERGED_DIR)
    abs_output = os.path.abspath(f16_path)

    print(f"  Input:  {abs_model}")
    print(f"  Output: {abs_output}")
    print("  Converting (this takes a few minutes)...")

    # Run from the llama.cpp source directory so imports work
    result = subprocess.run(
        [sys.executable, "convert_hf_to_gguf.py", abs_model,
         "--outfile", abs_output, "--outtype", "f16"],
        cwd=os.path.abspath(LLAMA_CPP_SRC),
        text=True,
    )

    if result.returncode == 0 and os.path.exists(f16_path):
        size_gb = os.path.getsize(f16_path) / 1024**3
        print(f"  F16 GGUF created: {size_gb:.1f} GB")
        return f16_path

    print(f"  First attempt failed (exit code {result.returncode}).")
    print("  Trying with --bigendian=False flag...")

    result = subprocess.run(
        [sys.executable, "convert_hf_to_gguf.py", abs_model,
         "--outfile", abs_output, "--outtype", "f16", "--verbose"],
        cwd=os.path.abspath(LLAMA_CPP_SRC),
        text=True,
    )

    if os.path.exists(f16_path):
        size_gb = os.path.getsize(f16_path) / 1024**3
        print(f"  F16 GGUF created: {size_gb:.1f} GB")
        return f16_path

    print("  ERROR: Conversion failed.")
    return None


def quantize(f16_path):
    """Quantize F16 GGUF to Q4_K_M."""
    print("\n[5/5] Quantizing to Q4_K_M...")

    quantize_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    quantize_bin = None

    # Search for binary
    for root, dirs, files in os.walk(LLAMA_CPP_DIR):
        for f in files:
            if f == quantize_name:
                quantize_bin = os.path.join(root, f)
                break
        if quantize_bin:
            break

    if not quantize_bin:
        print(f"  WARNING: {quantize_name} not found.")
        print(f"  The F16 GGUF is usable as-is (just larger).")
        # Rename f16 to final name
        final = os.path.join(OUTPUT_DIR, "unsloth.Q4_K_M.gguf")
        shutil.copy2(f16_path, final)
        return final

    if platform.system() != "Windows":
        os.chmod(quantize_bin, 0o755)

    q4_path = os.path.join(OUTPUT_DIR, "unsloth.Q4_K_M.gguf")
    print(f"  Using: {quantize_bin}")
    print(f"  Input:  {f16_path}")
    print(f"  Output: {q4_path}")
    print("  Quantizing...")

    result = subprocess.run(
        [quantize_bin, f16_path, q4_path, "Q4_K_M"],
        text=True,
    )

    if result.returncode == 0 and os.path.exists(q4_path):
        size_gb = os.path.getsize(q4_path) / 1024**3
        print(f"  Q4_K_M: {q4_path} ({size_gb:.1f} GB)")
        # Remove intermediate F16
        print(f"  Removing intermediate F16...")
        os.remove(f16_path)
        return q4_path

    print("  Quantization failed. Using F16 instead.")
    final = os.path.join(OUTPUT_DIR, "unsloth.Q4_K_M.gguf")
    if f16_path != final:
        shutil.copy2(f16_path, final)
    return final


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
    print(f"  Modelfile created: {path}")


def main():
    print("=" * 60)
    print("RUNECLAW - Convert to GGUF (No Build Tools)")
    print("=" * 60)

    if not os.path.exists(MODEL_MERGED_DIR):
        print(f"\nERROR: {MODEL_MERGED_DIR} not found!")
        print("Run export_model.py first.")
        sys.exit(1)

    print(f"\nInput:  {MODEL_MERGED_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    # Step 1: Get pre-built quantize binary
    setup_binaries()

    # Step 2: Get conversion scripts (full source, no git)
    if not setup_converter():
        print("\nERROR: Could not download conversion scripts.")
        sys.exit(1)

    # Step 3: Install Python deps
    install_deps()

    # Step 4: Convert to F16 GGUF
    f16_path = convert_to_f16()
    if not f16_path:
        print("\nERROR: Could not convert to F16 GGUF.")
        print("Check the error messages above.")
        sys.exit(1)

    # Step 5: Quantize to Q4_K_M
    final_path = quantize(f16_path)
    final_filename = os.path.basename(final_path)

    # Create Modelfile
    create_modelfile(final_filename)

    print(f"\n{'=' * 60}")
    print("GGUF conversion complete!")
    print(f"{'=' * 60}")
    print(f"""
Files in {OUTPUT_DIR}/:
  {final_filename}  - your fine-tuned model
  Modelfile          - Ollama configuration

Next steps:

  cd {OUTPUT_DIR}
  ollama create pbdes2022/HUMANOID-TRADERS -f Modelfile
  ollama run pbdes2022/HUMANOID-TRADERS "Scan BTC/USDT for trade setups"
  ollama push pbdes2022/HUMANOID-TRADERS
""")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
RUNECLAW — Convert Merged Model to GGUF (No Build Tools)
==========================================================
Downloads pre-built llama.cpp binaries and converts the merged
safetensors model to GGUF Q4_K_M format.

NO cmake, Visual Studio, or build tools required.

Usage:
  venv\Scripts\activate
  python convert_to_gguf.py

Requires:
  - ./runeclaw-model-merged/  (from export_model.py)
  - Internet connection (downloads ~30MB of pre-built binaries)

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
import tempfile

MODEL_MERGED_DIR = "./runeclaw-model-merged"
OUTPUT_DIR = "./runeclaw-model"
LLAMA_CPP_DIR = "./llama-cpp-tools"

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
                    chunk = response.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        print(f"\r    {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB ({pct:.0f}%)", end="", flush=True)
            print()
        return True
    except Exception as e:
        print(f"\n  ERROR downloading: {e}")
        return False


def get_latest_llama_cpp_release():
    """Get the latest llama.cpp release URL for Windows."""
    print("  Finding latest llama.cpp release...")
    api_url = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR fetching releases: {e}")
        return None, None

    tag = data.get("tag_name", "")
    print(f"  Latest release: {tag}")

    # Find Windows binary
    is_windows = platform.system() == "Windows"
    is_linux = platform.system() == "Linux"

    for asset in data.get("assets", []):
        name = asset["name"].lower()
        url = asset["browser_download_url"]

        if is_windows and "win" in name and "x64" in name and name.endswith(".zip"):
            if "cuda" not in name and "vulkan" not in name and "opencl" not in name:
                print(f"  Found: {asset['name']}")
                return url, asset["name"]

        if is_linux and "linux" in name and "x64" in name and name.endswith(".zip"):
            if "cuda" not in name and "vulkan" not in name and "opencl" not in name:
                print(f"  Found: {asset['name']}")
                return url, asset["name"]

    # Fallback: try any windows/linux zip
    for asset in data.get("assets", []):
        name = asset["name"].lower()
        url = asset["browser_download_url"]
        if is_windows and "win" in name and name.endswith(".zip"):
            print(f"  Found (fallback): {asset['name']}")
            return url, asset["name"]
        if is_linux and "linux" in name and name.endswith(".zip"):
            print(f"  Found (fallback): {asset['name']}")
            return url, asset["name"]

    return None, None


def setup_llama_cpp():
    """Download and extract pre-built llama.cpp binaries."""
    print("\n[1/4] Setting up llama.cpp (pre-built, no compilation)...")

    os.makedirs(LLAMA_CPP_DIR, exist_ok=True)

    # Check if already downloaded
    quantize_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    quantize_path = os.path.join(LLAMA_CPP_DIR, quantize_name)
    if os.path.exists(quantize_path):
        print(f"  Already have {quantize_name}, skipping download.")
        return True

    url, filename = get_latest_llama_cpp_release()
    if not url:
        print("  ERROR: Could not find llama.cpp release for your platform.")
        print(f"  Platform: {platform.system()} {platform.machine()}")
        return False

    zip_path = os.path.join(LLAMA_CPP_DIR, filename)
    if not download_file(url, zip_path, f"llama.cpp ({filename})"):
        return False

    # Extract
    print("  Extracting...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(LLAMA_CPP_DIR)

    # Find the quantize binary in extracted files
    for root, dirs, files in os.walk(LLAMA_CPP_DIR):
        for f in files:
            if f == quantize_name or f == "llama-quantize":
                src = os.path.join(root, f)
                dst = os.path.join(LLAMA_CPP_DIR, quantize_name)
                if src != dst:
                    shutil.copy2(src, dst)
                print(f"  Found: {quantize_name}")
                break

    # Also find convert script
    for root, dirs, files in os.walk(LLAMA_CPP_DIR):
        for f in files:
            if f == "convert_hf_to_gguf.py":
                src = os.path.join(root, f)
                dst = os.path.join(LLAMA_CPP_DIR, f)
                if src != dst:
                    shutil.copy2(src, dst)
                print(f"  Found: {f}")

    # Clean up zip
    os.remove(zip_path)
    print("  Setup complete.")
    return True


def install_gguf_package():
    """Install the gguf Python package needed for conversion."""
    print("\n[2/4] Installing gguf Python package...")
    try:
        import gguf
        print(f"  gguf already installed (v{getattr(gguf, '__version__', '?')})")
        return True
    except ImportError:
        pass

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "gguf", "-q"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: pip install gguf failed: {result.stderr}")
        return False
    print("  Installed gguf package.")
    return True


def convert_to_f16_gguf():
    """Convert safetensors model to F16 GGUF."""
    print("\n[3/4] Converting model to F16 GGUF...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    f16_path = os.path.join(OUTPUT_DIR, "model-f16.gguf")

    # Try using the convert script from llama.cpp
    convert_script = os.path.join(LLAMA_CPP_DIR, "convert_hf_to_gguf.py")

    if not os.path.exists(convert_script):
        # Download just the convert script from llama.cpp repo
        print("  Downloading convert_hf_to_gguf.py from llama.cpp...")
        script_url = "https://raw.githubusercontent.com/ggerganov/llama.cpp/master/convert_hf_to_gguf.py"
        if not download_file(script_url, convert_script, "convert script"):
            # Try alternative conversion using gguf package directly
            print("  Trying alternative conversion method...")
            return convert_to_f16_gguf_alternative()

    print(f"  Input:  {MODEL_MERGED_DIR}")
    print(f"  Output: {f16_path}")
    print("  This may take a few minutes...")

    result = subprocess.run(
        [sys.executable, convert_script, MODEL_MERGED_DIR,
         "--outfile", f16_path, "--outtype", "f16"],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"  Convert script failed, trying with --vocab-type...")
        # Some models need explicit vocab type
        result = subprocess.run(
            [sys.executable, convert_script, MODEL_MERGED_DIR,
             "--outfile", f16_path, "--outtype", "f16"],
            text=True,
        )
        if result.returncode != 0:
            print("  Trying alternative conversion...")
            return convert_to_f16_gguf_alternative()

    if os.path.exists(f16_path):
        size_gb = os.path.getsize(f16_path) / 1024**3
        print(f"  F16 GGUF created: {size_gb:.1f} GB")
        return f16_path
    else:
        print("  ERROR: F16 GGUF file not created")
        return None


def convert_to_f16_gguf_alternative():
    """Alternative conversion using transformers + gguf directly."""
    print("  Using alternative conversion (transformers export)...")
    f16_path = os.path.join(OUTPUT_DIR, "model-f16.gguf")

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer

        print("  Loading model for conversion...")
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_MERGED_DIR,
            torch_dtype="float16",
            device_map="cpu",
        )
        tokenizer = AutoTokenizer.from_pretrained(MODEL_MERGED_DIR)

        print("  Exporting to GGUF (this takes a few minutes)...")
        model.save_pretrained(
            OUTPUT_DIR,
            gguf_file="model-f16.gguf",
        )

        if os.path.exists(f16_path):
            size_gb = os.path.getsize(f16_path) / 1024**3
            print(f"  F16 GGUF created: {size_gb:.1f} GB")
            return f16_path
    except Exception as e:
        print(f"  Alternative conversion also failed: {e}")

    return None


def quantize_to_q4km(f16_path):
    """Quantize F16 GGUF to Q4_K_M using pre-built binary."""
    print("\n[4/4] Quantizing to Q4_K_M...")

    quantize_name = "llama-quantize.exe" if platform.system() == "Windows" else "llama-quantize"
    quantize_bin = os.path.join(LLAMA_CPP_DIR, quantize_name)

    # Also check in subdirectories
    if not os.path.exists(quantize_bin):
        for root, dirs, files in os.walk(LLAMA_CPP_DIR):
            for f in files:
                if f == quantize_name:
                    quantize_bin = os.path.join(root, f)
                    break

    if not os.path.exists(quantize_bin):
        print(f"  ERROR: {quantize_name} not found in {LLAMA_CPP_DIR}")
        print(f"  The F16 GGUF is still usable but large.")
        print(f"  You can quantize later with: llama-quantize {f16_path} output.gguf Q4_K_M")
        return f16_path

    # Make executable on Linux
    if platform.system() != "Windows":
        os.chmod(quantize_bin, 0o755)

    q4_path = os.path.join(OUTPUT_DIR, "unsloth.Q4_K_M.gguf")
    print(f"  Input:  {f16_path}")
    print(f"  Output: {q4_path}")
    print("  Quantizing (this takes 2-5 minutes)...")

    result = subprocess.run(
        [quantize_bin, f16_path, q4_path, "Q4_K_M"],
        text=True,
    )

    if result.returncode != 0:
        print("  ERROR: Quantization failed.")
        print(f"  The F16 GGUF at {f16_path} is still usable (but larger).")
        return f16_path

    size_gb = os.path.getsize(q4_path) / 1024**3
    print(f"  Q4_K_M GGUF created: {q4_path} ({size_gb:.1f} GB)")

    # Clean up F16 (it's huge)
    if os.path.exists(q4_path) and os.path.exists(f16_path):
        print(f"  Removing intermediate F16 file ({os.path.getsize(f16_path)/1024**3:.1f} GB)...")
        os.remove(f16_path)

    return q4_path


def create_modelfile(gguf_filename):
    """Create the Ollama Modelfile."""
    modelfile_path = os.path.join(OUTPUT_DIR, "Modelfile")
    with open(modelfile_path, "w") as f:
        f.write(f"""FROM ./{gguf_filename}

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
PARAMETER stop "<|eot_id|>"
PARAMETER stop "<|end|>"

SYSTEM \"\"\"{SYSTEM_PROMPT}\"\"\"
""")
    print(f"  Modelfile created: {modelfile_path}")


def main():
    print("=" * 60)
    print("RUNECLAW — Convert to GGUF (No Build Tools)")
    print("=" * 60)

    # Check merged model exists
    if not os.path.exists(MODEL_MERGED_DIR):
        print(f"\nERROR: {MODEL_MERGED_DIR} not found!")
        print("Run export_model.py first.")
        sys.exit(1)

    print(f"\nInput:  {MODEL_MERGED_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    # Step 1: Get pre-built llama.cpp
    if not setup_llama_cpp():
        print("\nFailed to set up llama.cpp binaries.")
        print("Trying alternative method (Python-only conversion)...")

    # Step 2: Install gguf package
    install_gguf_package()

    # Step 3: Convert to F16 GGUF
    f16_path = convert_to_f16_gguf()
    if not f16_path:
        print("\nERROR: Could not convert model to GGUF.")
        print("Try uploading runeclaw-model-merged/ to Google Colab")
        print("and running the GGUF export there instead.")
        sys.exit(1)

    # Step 4: Quantize to Q4_K_M
    final_path = quantize_to_q4km(f16_path)
    final_filename = os.path.basename(final_path)

    # Create Modelfile
    create_modelfile(final_filename)

    print(f"\n{'=' * 60}")
    print("GGUF conversion complete!")
    print(f"{'=' * 60}")
    print(f"""
Files in {OUTPUT_DIR}/:
  {final_filename}  — your fine-tuned model
  Modelfile          — Ollama configuration

Next steps:

  1. Import into Ollama:
     cd {OUTPUT_DIR}
     ollama create pbdes2022/HUMANOID-TRADERS -f Modelfile

  2. Test it:
     ollama run pbdes2022/HUMANOID-TRADERS "Scan BTC/USDT for trade setups"

  3. Push to Ollama registry:
     ollama push pbdes2022/HUMANOID-TRADERS
""")


if __name__ == "__main__":
    main()

@echo off
REM ══════════════════════════════════════════════════════════════
REM RUNECLAW Local Training Setup — Windows (ASUS ProArt P16)
REM RTX 5090 Laptop (Blackwell / sm_120) Edition
REM ══════════════════════════════════════════════════════════════
REM Run this script ONCE to set up your training environment.
REM After setup, run: python train_local.py
REM ══════════════════════════════════════════════════════════════

echo ==============================================================
echo RUNECLAW Local Training Setup (RTX 5090 Blackwell)
echo ==============================================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Download Python 3.10+ from https://python.org/downloads
    echo IMPORTANT: Check "Add to PATH" during install!
    pause
    exit /b 1
)
echo [OK] Python found
python --version

REM Check NVIDIA GPU
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo ERROR: nvidia-smi not found.
    echo Install NVIDIA drivers from https://www.nvidia.com/Download/index.aspx
    pause
    exit /b 1
)
echo [OK] NVIDIA GPU found
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
echo.

REM Delete old venv if it has wrong PyTorch
if exist "venv" (
    echo [INFO] Removing old venv to install correct PyTorch...
    rmdir /s /q venv
)

REM Create virtual environment
echo [1/6] Creating virtual environment...
python -m venv venv
echo   Created venv/

REM Activate
call venv\Scripts\activate.bat
echo [OK] Virtual environment activated
echo.

REM Upgrade pip first
echo [2/6] Upgrading pip...
python -m pip install --upgrade pip -q
echo.

REM Install unsloth and deps FIRST (before PyTorch CUDA)
REM This prevents unsloth from overwriting PyTorch CUDA with CPU version
echo [3/6] Installing unsloth and training libraries...
pip install unsloth -q
pip install datasets trl peft accelerate bitsandbytes -q
echo.

REM NOW install PyTorch CUDA on top — this overwrites the CPU version unsloth pulled in
echo [4/6] Installing PyTorch Nightly with CUDA 12.8 (Blackwell support)...
echo   The RTX 5090 (sm_120) requires PyTorch nightly builds.
echo   This MUST run AFTER unsloth to avoid being overwritten.
pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128 --force-reinstall --no-deps
echo.

REM Also reinstall torch deps that --no-deps skipped
pip install --pre torch torchvision torchaudio --index-url https://download.pytorch.org/whl/nightly/cu128
echo.

REM Verify
echo [5/6] Verifying installation...
python -c "import torch; print(f'PyTorch {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"None\"}'); print(f'Compute Capability: {torch.cuda.get_device_capability(0) if torch.cuda.is_available() else \"N/A\"}')"
echo.

REM Quick compatibility test
echo [6/6] Checking GPU compute kernel compatibility...
python -c "import torch; x = torch.randn(100,100, device='cuda'); y = x @ x; print(f'[OK] GPU compute test passed: {y.shape}')"
if errorlevel 1 (
    echo [ERROR] GPU compute test failed!
    echo   Your RTX 5090 may need a newer PyTorch nightly.
    echo   Try: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128 --force-reinstall
    pause
    exit /b 1
)
echo.

echo ==============================================================
echo Setup complete!
echo.
echo To train, run:
echo   venv\Scripts\activate.bat
echo   python train_local.py
echo.
echo Make sure combined_training.jsonl is in this directory
echo or in a training_data\ subdirectory.
echo ==============================================================
pause

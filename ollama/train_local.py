#!/usr/bin/env python3
"""
RUNECLAW Local LoRA Fine-Tuning Script
=======================================
For ASUS ProArt P16 (RTX 4060 8GB / RTX 4070 8GB)

Prerequisites (run once):
  1. Install Python 3.10+ from python.org
  2. Install CUDA Toolkit 12.1+: https://developer.nvidia.com/cuda-downloads
  3. Install PyTorch with CUDA:
       pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
  4. Install dependencies:
       pip install unsloth[cu121] datasets trl peft accelerate bitsandbytes

Usage:
  python train_local.py

Output:
  ./runeclaw-model/unsloth.Q4_K_M.gguf  (import into Ollama)
"""

import os
import sys
import torch

# ── Pre-flight checks ────────────────────────────────────────────

def preflight():
    print("=" * 60)
    print("RUNECLAW Local Fine-Tuning")
    print("=" * 60)

    # Check CUDA
    if not torch.cuda.is_available():
        print("\nERROR: CUDA not available!")
        print("Install CUDA Toolkit: https://developer.nvidia.com/cuda-downloads")
        print("Install PyTorch CUDA: pip install torch --index-url https://download.pytorch.org/whl/cu121")
        sys.exit(1)

    gpu_name = torch.cuda.get_device_name(0)
    props = torch.cuda.get_device_properties(0)
    vram_gb = getattr(props, 'total_memory', getattr(props, 'total_mem', 0)) / 1024**3
    print(f"\nGPU:  {gpu_name}")
    print(f"VRAM: {vram_gb:.1f} GB")
    print(f"CUDA: {torch.version.cuda}")
    print(f"PyTorch: {torch.__version__}")

    if vram_gb < 6:
        print(f"\nWARNING: {vram_gb:.1f}GB VRAM is tight. Training may be slow or OOM.")
        print("Consider using Colab (free T4 with 15GB) instead.")

    return vram_gb


def get_training_config(vram_gb):
    """Adjust batch size and settings based on available VRAM."""
    if vram_gb >= 15:
        # RTX 5090 Laptop (16GB), RTX 4090, A100, etc.
        return {"batch_size": 4, "grad_accum": 2, "max_seq_length": 4096}
    elif vram_gb >= 12:
        return {"batch_size": 4, "grad_accum": 2, "max_seq_length": 4096}
    elif vram_gb >= 8:
        return {"batch_size": 2, "grad_accum": 4, "max_seq_length": 2048}
    else:
        return {"batch_size": 1, "grad_accum": 8, "max_seq_length": 1024}


# ── Main Training Pipeline ───────────────────────────────────────

def main():
    vram_gb = preflight()
    config = get_training_config(vram_gb)

    print(f"\nConfig for {vram_gb:.0f}GB VRAM:")
    print(f"  Batch size:       {config['batch_size']}")
    print(f"  Grad accumulation: {config['grad_accum']}")
    print(f"  Max seq length:   {config['max_seq_length']}")
    print(f"  Effective batch:  {config['batch_size'] * config['grad_accum']}")

    # ── Step 1: Load Model ────────────────────────────────────
    print("\n[1/6] Loading Llama 3.2 3B (4-bit quantized)...")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
        max_seq_length=config["max_seq_length"],
        dtype=None,
        load_in_4bit=True,
    )
    print("  Model loaded.")

    # ── Step 2: Apply LoRA ────────────────────────────────────
    print("\n[2/6] Applying LoRA adapters...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    model.print_trainable_parameters()

    # ── Step 3: Load Dataset ──────────────────────────────────
    print("\n[3/6] Loading training data...")
    import json as _json

    # Look for the training data file
    data_paths = [
        "./training_data/combined_training.jsonl",
        "./combined_training.jsonl",
        "../training_data/combined_training.jsonl",
    ]
    data_file = None
    for p in data_paths:
        if os.path.exists(p):
            data_file = p
            break

    if data_file is None:
        print("  ERROR: combined_training.jsonl not found!")
        print("  Place it in the same directory as this script,")
        print("  or in a 'training_data' subdirectory.")
        sys.exit(1)

    # Load JSONL manually (avoids Python 3.14 dill/pickle issues)
    rows = []
    with open(data_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(_json.loads(line))
    print(f"  Loaded {len(rows)} samples from {data_file}")

    # ── Step 4: Format for Llama 3.2 ─────────────────────────
    print("\n[4/6] Formatting dataset...")

    SYSTEM_PROMPT = (
        "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
        "markets using the GetClaw Confluence Engine (12 weighted indicators), "
        "enforce strict risk management through 23 automated checks, and generate "
        "structured trade ideas. You never execute without human confirmation. "
        "Capital preservation above all."
    )

    formatted_texts = []
    for example in rows:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        user_msg = example["instruction"]
        if example.get("input") and example["input"].strip():
            user_msg += "\n\n" + example["input"]

        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": example["output"]})

        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        formatted_texts.append(text)

    print(f"  Formatted {len(formatted_texts)} samples")

    # ── Step 5: Train ─────────────────────────────────────────
    print("\n[5/6] Starting training...")
    print("  This will take 1-3 hours depending on your GPU.")
    print("  You can safely minimize this window.\n")

    # Build a pure PyTorch dataset to completely bypass HF datasets/dill
    # (Python 3.14 broke dill serialization)
    from torch.utils.data import Dataset as TorchDataset

    class TextDataset(TorchDataset):
        def __init__(self, texts, tokenizer, max_length):
            self.encodings = []
            print(f"  Tokenizing {len(texts)} samples...")
            for i, text in enumerate(texts):
                enc = tokenizer(
                    text,
                    truncation=True,
                    max_length=max_length,
                    padding="max_length",
                    return_tensors="pt",
                )
                self.encodings.append({
                    "input_ids": enc["input_ids"].squeeze(),
                    "attention_mask": enc["attention_mask"].squeeze(),
                    "labels": enc["input_ids"].squeeze().clone(),
                })
                if (i + 1) % 1000 == 0:
                    print(f"    {i + 1}/{len(texts)} tokenized...")
            print(f"  Tokenization complete.")

        def __len__(self):
            return len(self.encodings)

        def __getitem__(self, idx):
            return self.encodings[idx]

    train_dataset = TextDataset(formatted_texts, tokenizer, config["max_seq_length"])

    from transformers import Trainer, TrainingArguments

    training_args = TrainingArguments(
        per_device_train_batch_size=config["batch_size"],
        gradient_accumulation_steps=config["grad_accum"],
        warmup_steps=50,
        num_train_epochs=3,
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=25,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=42,
        output_dir="./runeclaw-checkpoints",
        save_strategy="epoch",
        report_to="none",
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
    )

    stats = trainer.train()

    print(f"\n  Training complete!")
    print(f"  Final loss: {stats.training_loss:.4f}")
    print(f"  Runtime: {stats.metrics['train_runtime']:.0f}s")

    # ── Step 6: Export to GGUF ────────────────────────────────
    print("\n[6/6] Exporting to GGUF for Ollama...")

    output_dir = "./runeclaw-model"
    model.save_pretrained_gguf(
        output_dir,
        tokenizer,
        quantization_method="q4_k_m",
    )

    gguf_path = os.path.join(output_dir, "unsloth.Q4_K_M.gguf")
    size_gb = os.path.getsize(gguf_path) / 1024**3

    # Create Modelfile next to GGUF
    modelfile_path = os.path.join(output_dir, "Modelfile")
    with open(modelfile_path, "w") as f:
        f.write(f"""FROM ./unsloth.Q4_K_M.gguf

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 4096
PARAMETER stop "<|eot_id|>"
PARAMETER stop "<|end|>"

SYSTEM \"\"\"{SYSTEM_PROMPT}\"\"\"
""")

    print(f"\n  GGUF exported: {gguf_path} ({size_gb:.1f} GB)")
    print(f"  Modelfile:     {modelfile_path}")

    # ── Done ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DONE! Your fine-tuned RUNECLAW model is ready.")
    print("=" * 60)
    print(f"""
Next steps:

  1. Import into Ollama:
     cd {output_dir}
     ollama create pbdes2022/HUMANOID-TRADERS -f Modelfile

  2. Test it:
     ollama run pbdes2022/HUMANOID-TRADERS "Scan BTC/USDT for trade setups"

  3. Push to Ollama registry:
     ollama push pbdes2022/HUMANOID-TRADERS
""")


# ── Quick Test Mode ───────────────────────────────────────────────

def test_model():
    """Test an already-trained model."""
    print("Loading model for testing...")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        "runeclaw-checkpoints/checkpoint-*",
        max_seq_length=2048,
        load_in_4bit=True,
    )
    FastLanguageModel.for_inference(model)

    prompt = """Analyze BTC/USDT for a potential trade.

Market data:
- Price: $67,432.10
- RSI-14: 28.5 (oversold)
- MACD Histogram: 125.3 (positive)
- Bollinger %B: 0.15
- ADX: 34, +DI > -DI (TREND_UP)
- Price is at 61.8% Fibonacci retracement
- Volume spike with price increase"""

    messages = [
        {"role": "system", "content": "You are RUNECLAW, an AI trading analyst."},
        {"role": "user", "content": prompt},
    ]

    inputs = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True,
        return_tensors="pt"
    ).to("cuda")

    outputs = model.generate(
        input_ids=inputs, max_new_tokens=1024,
        temperature=0.3, top_p=0.9,
    )

    response = tokenizer.decode(outputs[0][inputs.shape[-1]:], skip_special_tokens=True)
    print("=" * 60)
    print("RUNECLAW Response:")
    print("=" * 60)
    print(response)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_model()
    else:
        main()

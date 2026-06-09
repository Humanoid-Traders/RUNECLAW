#!/usr/bin/env python3
"""
RUNECLAW MAX - Maxed-Out 8B Training for RTX 5090
===================================================
Pushes hardware to its limits for best possible model quality.

Hardware target:
  - RTX 5090 Laptop GPU (24GB VRAM)
  - AMD Ryzen AI 9 HX 370
  - 64 GB RAM

Key improvements over train_local_8b.py:
  - LoRA rank 64 (vs 32) — 2x more trainable parameters
  - 3 epochs (vs 1) — model sees data 3x
  - Longer context 2048 (vs 1024) — captures full trade analyses
  - Cosine schedule with proper warmup ratio
  - Gradient checkpointing to fit rank-64 in VRAM

Usage:
  python generate_training_data_v3.py   (first — generates 30K+ samples)
  python train_max_8b.py                (then — trains the model)
  python export_model.py                (export merged model)
  python convert_official.py            (convert to GGUF for Ollama)
"""

import os
import sys
import torch

# ── Configuration ───────────────────────────────────────────────

BASE_MODEL = "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit"
MODEL_NAME = "runeclaw-8b-max"

# MAXED hyperparameters for RTX 5090 (24GB VRAM) + 64GB RAM
MAX_SEQ = 2048          # longer context for detailed analyses
BATCH_SIZE = 2          # smaller batch to fit rank-64 + 2048 ctx
GRAD_ACCUM = 8          # effective batch = 2 * 8 = 16
LEARNING_RATE = 5e-5    # lower LR for 3 epochs (prevents overfitting)
NUM_EPOCHS = 3          # 3 full passes over data
LORA_RANK = 64          # 2x more capacity than rank-32
LORA_ALPHA = 64         # match rank
WARMUP_RATIO = 0.05     # 5% of total steps for warmup

SYSTEM_PROMPT = (
    "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
    "markets using the GetClaw Confluence Engine (12 weighted indicators: "
    "RSI-14 w=1.5, MACD w=1.0, Bollinger w=1.2, EMA Cross w=1.0, Volume "
    "Profile w=1.3, OBV w=0.8, Stoch RSI w=0.7, ADX w=1.1, Ichimoku w=0.9, "
    "VWAP w=1.4, ATR w=0.8, Fibonacci w=1.0), enforce strict risk management "
    "through 22 automated checks (all must pass, fail-closed), and generate "
    "structured trade ideas. You never execute without human confirmation. "
    "Capital preservation above all."
)


# ── Pre-flight checks ──────────────────────────────────────────

def preflight():
    print("=" * 60)
    print("RUNECLAW MAX - Maxed-Out 8B Training")
    print("=" * 60)

    if not torch.cuda.is_available():
        print("\nERROR: CUDA not available!")
        sys.exit(1)

    gpu_name = torch.cuda.get_device_name(0)
    props = torch.cuda.get_device_properties(0)
    vram_gb = getattr(props, 'total_memory', getattr(props, 'total_mem', 0)) / 1024**3
    print(f"\nGPU:  {gpu_name}")
    print(f"VRAM: {vram_gb:.1f} GB")
    print(f"CUDA: {torch.version.cuda}")
    print(f"PyTorch: {torch.__version__}")

    import psutil
    ram_gb = psutil.virtual_memory().total / 1024**3
    print(f"RAM:  {ram_gb:.1f} GB")

    print(f"\n--- MAX Configuration ---")
    print(f"Model:     {BASE_MODEL}")
    print(f"LoRA:      rank={LORA_RANK}, alpha={LORA_ALPHA}")
    print(f"Context:   {MAX_SEQ} tokens")
    print(f"Batch:     {BATCH_SIZE} x {GRAD_ACCUM} accum = {BATCH_SIZE * GRAD_ACCUM} effective")
    print(f"Epochs:    {NUM_EPOCHS}")
    print(f"LR:        {LEARNING_RATE}")
    print(f"Warmup:    {WARMUP_RATIO*100:.0f}% of steps")
    return vram_gb


# ── Main Training Pipeline ────────────────────────────────────

def main():
    vram_gb = preflight()

    # ── Step 1: Load Model ────────────────────────────────
    print(f"\n[1/6] Loading Llama 3.1 8B (4-bit quantized)...")
    from unsloth import FastLanguageModel

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=BASE_MODEL,
        max_seq_length=MAX_SEQ,
        dtype=None,
        load_in_4bit=True,
    )
    tokenizer.padding_side = "right"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    print("  Model loaded.")

    # ── Step 2: Apply LoRA (rank 64) ─────────────────────
    print(f"\n[2/6] Applying LoRA adapters (rank={LORA_RANK})...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_RANK,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )
    model.print_trainable_parameters()

    # ── Step 3: Load Dataset ──────────────────────────────
    print("\n[3/6] Loading training data...")
    import json as _json

    # Try merged dataset first (all data), then fall back
    data_paths = [
        "./training_data/combined_training_all.jsonl",
        "./training_data/combined_training_claude.jsonl",
        "./training_data/combined_training_v3.jsonl",
        "./training_data/combined_training.jsonl",
        "./combined_training_all.jsonl",
        "./combined_training.jsonl",
        "../training_data/combined_training.jsonl",
    ]
    data_file = None
    for p in data_paths:
        if os.path.exists(p):
            data_file = p
            break

    if data_file is None:
        print("  ERROR: No training data found!")
        print("  Run generate_training_data_v3.py (or v2) first.")
        sys.exit(1)

    rows = []
    with open(data_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(_json.loads(line))
    print(f"  Loaded {len(rows)} samples from {data_file}")

    # ── Step 4: Format and tokenize ──────────────────────
    print("\n[4/6] Formatting and tokenizing...")

    formatted_texts = []
    for example in rows:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        user_msg = example["instruction"]
        if example.get("input") and example["input"].strip():
            user_msg += "\n\n" + example["input"]
        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": example["output"]})
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        formatted_texts.append(text)

    # Token length check
    print("  Checking token lengths...")
    sample_lengths = []
    for t in formatted_texts[:500]:
        toks = tokenizer(t, truncation=False)["input_ids"]
        sample_lengths.append(len(toks))
    avg_len = sum(sample_lengths) / len(sample_lengths)
    max_len = max(sample_lengths)
    under_limit = sum(1 for l in sample_lengths if l <= MAX_SEQ)
    print(f"  Avg tokens: {avg_len:.0f}, Max: {max_len}, Under {MAX_SEQ}: {under_limit}/{len(sample_lengths)}")

    # Dynamic dataset
    from torch.utils.data import Dataset as TorchDataset
    from torch.nn.utils.rnn import pad_sequence

    class DynamicDataset(TorchDataset):
        def __init__(self, texts, tokenizer, max_length):
            self.items = []
            print(f"  Tokenizing {len(texts)} samples...")
            for i, text in enumerate(texts):
                enc = tokenizer(
                    text,
                    truncation=True,
                    max_length=max_length,
                    padding=False,
                    return_tensors="pt",
                )
                ids = enc["input_ids"].squeeze()
                self.items.append({
                    "input_ids": ids,
                    "attention_mask": torch.ones_like(ids),
                    "labels": ids.clone(),
                })
                if (i + 1) % 5000 == 0:
                    print(f"    {i + 1}/{len(texts)}...")
            print("  Tokenization complete.")

        def __len__(self):
            return len(self.items)

        def __getitem__(self, idx):
            return self.items[idx]

    class DynamicPadCollator:
        def __init__(self, pad_id):
            self.pad_id = pad_id

        def __call__(self, batch):
            input_ids = pad_sequence(
                [item["input_ids"] for item in batch],
                batch_first=True, padding_value=self.pad_id,
            )
            labels = pad_sequence(
                [item["labels"] for item in batch],
                batch_first=True, padding_value=-100,
            )
            attention_mask = pad_sequence(
                [item["attention_mask"] for item in batch],
                batch_first=True, padding_value=0,
            )
            return {
                "input_ids": input_ids,
                "labels": labels,
                "attention_mask": attention_mask,
            }

    train_dataset = DynamicDataset(formatted_texts, tokenizer, MAX_SEQ)
    collator = DynamicPadCollator(tokenizer.pad_token_id)

    # ── Step 5: Train ─────────────────────────────────────
    print("\n[5/6] Starting MAX training...")
    from transformers import Trainer, TrainingArguments

    total_samples = len(train_dataset)
    steps_per_epoch = total_samples // (BATCH_SIZE * GRAD_ACCUM)
    total_steps = steps_per_epoch * NUM_EPOCHS
    warmup_steps = int(total_steps * WARMUP_RATIO)

    print(f"  Samples:     {total_samples}")
    print(f"  Epochs:      {NUM_EPOCHS}")
    print(f"  Steps/epoch: {steps_per_epoch}")
    print(f"  Total steps: {total_steps}")
    print(f"  Warmup:      {warmup_steps} steps")
    print(f"  LoRA rank:   {LORA_RANK} (167M+ trainable params)")
    print(f"  Context:     {MAX_SEQ} tokens")
    print()

    training_args = TrainingArguments(
        per_device_train_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        warmup_steps=warmup_steps,
        num_train_epochs=NUM_EPOCHS,
        learning_rate=LEARNING_RATE,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=42,
        output_dir=f"./{MODEL_NAME}-checkpoints",
        save_strategy="epoch",
        save_total_limit=2,
        report_to="none",
        dataloader_pin_memory=True,
        dataloader_num_workers=0,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        data_collator=collator,
    )

    stats = trainer.train()

    print(f"\n  Training complete!")
    print(f"  Final loss: {stats.training_loss:.4f}")
    print(f"  Runtime: {stats.metrics['train_runtime']:.0f}s")

    # ── Step 6: Save LoRA Adapter ─────────────────────────
    print("\n[6/6] Saving LoRA adapter...")

    adapter_dir = f"./{MODEL_NAME}-checkpoints/final-adapter"
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    print(f"\n  Adapter saved: {adapter_dir}")

    print("\n" + "=" * 60)
    print("MAX TRAINING COMPLETE!")
    print("=" * 60)
    print(f"""
Next steps — use the proven export pipeline:

  1. Export merged model:
     python export_model.py

  2. Convert to GGUF:
     python convert_official.py

  3. Import and test:
     cd runeclaw-model
     ollama create runeclaw -f Modelfile
     ollama run runeclaw "Scan BTC/USDT for trade setups"
""")


if __name__ == "__main__":
    main()

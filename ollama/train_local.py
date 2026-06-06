#!/usr/bin/env python3
"""
RUNECLAW Local LoRA Fine-Tuning Script (Optimized)
===================================================
For ASUS ProArt P16 with RTX 5090 Laptop GPU

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
    print("RUNECLAW Local Fine-Tuning (Optimized)")
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
    return vram_gb


# ── Main Training Pipeline ───────────────────────────────────────

def main():
    vram_gb = preflight()

    # ── Step 1: Load Model ────────────────────────────────────
    print("\n[1/6] Loading Llama 3.2 3B (4-bit quantized)...")
    from unsloth import FastLanguageModel

    MAX_SEQ = 1024  # Most samples are 200-600 tokens, 1024 covers 99%+

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
        max_seq_length=MAX_SEQ,
        dtype=None,
        load_in_4bit=True,
    )
    tokenizer.padding_side = "right"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
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
        sys.exit(1)

    rows = []
    with open(data_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(_json.loads(line))
    print(f"  Loaded {len(rows)} samples from {data_file}")

    # ── Step 4: Format and tokenize ──────────────────────────
    print("\n[4/6] Formatting and tokenizing...")

    SYSTEM_PROMPT = (
        "You are RUNECLAW, an AI trading analyst. You analyze cryptocurrency "
        "markets using the GetClaw Confluence Engine (12 weighted indicators), "
        "enforce strict risk management through 23 automated checks, and generate "
        "structured trade ideas. You never execute without human confirmation. "
        "Capital preservation above all."
    )

    # Format all texts
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

    # Check token length distribution
    print(f"  Checking token lengths...")
    sample_lengths = []
    for t in formatted_texts[:500]:
        toks = tokenizer(t, truncation=False)["input_ids"]
        sample_lengths.append(len(toks))
    avg_len = sum(sample_lengths) / len(sample_lengths)
    max_len = max(sample_lengths)
    under_1024 = sum(1 for l in sample_lengths if l <= 1024)
    print(f"  Avg tokens: {avg_len:.0f}, Max: {max_len}, Under 1024: {under_1024}/{len(sample_lengths)}")
    print(f"  Using max_seq_length={MAX_SEQ} (vs 4096 before = {4096/MAX_SEQ:.0f}x faster)")

    # Tokenize with DYNAMIC length (no padding to max!)
    from torch.utils.data import Dataset as TorchDataset
    from torch.nn.utils.rnn import pad_sequence
    import torch

    class DynamicDataset(TorchDataset):
        """Tokenizes to actual length, no padding waste."""
        def __init__(self, texts, tokenizer, max_length):
            self.items = []
            print(f"  Tokenizing {len(texts)} samples...")
            for i, text in enumerate(texts):
                enc = tokenizer(
                    text,
                    truncation=True,
                    max_length=max_length,
                    padding=False,  # NO PADDING - key optimization
                    return_tensors="pt",
                )
                ids = enc["input_ids"].squeeze()
                self.items.append({
                    "input_ids": ids,
                    "attention_mask": torch.ones_like(ids),
                    "labels": ids.clone(),
                })
                if (i + 1) % 2000 == 0:
                    print(f"    {i + 1}/{len(texts)}...")
            print(f"  Tokenization complete.")

        def __len__(self):
            return len(self.items)

        def __getitem__(self, idx):
            return self.items[idx]

    # Custom collator that pads to batch max (not global max)
    class DynamicPadCollator:
        def __init__(self, pad_id):
            self.pad_id = pad_id

        def __call__(self, batch):
            input_ids = [item["input_ids"] for item in batch]
            labels = [item["labels"] for item in batch]
            attention_mask = [item["attention_mask"] for item in batch]

            input_ids = pad_sequence(input_ids, batch_first=True, padding_value=self.pad_id)
            labels = pad_sequence(labels, batch_first=True, padding_value=-100)
            attention_mask = pad_sequence(attention_mask, batch_first=True, padding_value=0)

            return {
                "input_ids": input_ids,
                "labels": labels,
                "attention_mask": attention_mask,
            }

    train_dataset = DynamicDataset(formatted_texts, tokenizer, MAX_SEQ)
    collator = DynamicPadCollator(tokenizer.pad_token_id)

    # ── Step 5: Train ─────────────────────────────────────────
    print("\n[5/6] Starting training...")

    from transformers import Trainer, TrainingArguments

    # 1 epoch with 10K samples is plenty for LoRA fine-tuning
    num_epochs = 1
    total_steps = len(train_dataset) // (8)  # batch 4 * grad_accum 2
    print(f"  Epochs: {num_epochs}")
    print(f"  Estimated steps: {total_steps}")
    print(f"  Dynamic padding = each batch padded to its own max length")
    print(f"  Expected speedup: ~8-10x vs fixed 4096 padding\n")

    training_args = TrainingArguments(
        per_device_train_batch_size=8,       # larger batch, smaller sequences
        gradient_accumulation_steps=1,        # no accumulation needed
        warmup_steps=30,
        num_train_epochs=num_epochs,
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        seed=42,
        output_dir="./runeclaw-checkpoints",
        save_strategy="epoch",
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


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
RUNECLAW — Merge Training Datasets
Combines combined_training_claude.jsonl + opus_training.jsonl
into one master dataset with deduplication.

Usage:
  python merge_opus_claude.py

Output:
  ./training_data/combined_training_all.jsonl
"""

import json
import os
import random
import hashlib

random.seed(42)

INPUT_DIR = "./training_data"
OUTPUT_FILE = os.path.join(INPUT_DIR, "combined_training_all.jsonl")

# Files to merge (in order of priority)
SOURCES = [
    ("combined_training_claude.jsonl", "claude"),
    ("opus_training.jsonl", "opus_4.8"),
]

def make_key(sample):
    """Create a dedup key from instruction + first 200 chars of output."""
    inst = sample.get("instruction", "").strip().lower()
    out = sample.get("output", "").strip()[:200].lower()
    raw = f"{inst}||{out}"
    return hashlib.md5(raw.encode()).hexdigest()


def main():
    print("=" * 55)
    print("  RUNECLAW — Merge Training Datasets")
    print("=" * 55)

    all_samples = []
    seen_keys = set()
    duplicates = 0

    for filename, source_label in SOURCES:
        path = os.path.join(INPUT_DIR, filename)
        if not os.path.exists(path):
            print(f"\n  SKIP: {filename} not found")
            continue

        count = 0
        dups = 0
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    sample = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if "instruction" not in sample or "output" not in sample:
                    continue

                # Dedup check
                key = make_key(sample)
                if key in seen_keys:
                    dups += 1
                    duplicates += 1
                    continue
                seen_keys.add(key)

                # Normalize to 3 fields only
                clean = {
                    "instruction": sample["instruction"],
                    "input": sample.get("input", ""),
                    "output": sample["output"],
                }
                all_samples.append(clean)
                count += 1

        size_mb = os.path.getsize(path) / 1024 / 1024
        print(f"\n  {filename} ({size_mb:.1f} MB)")
        print(f"    Loaded:     {count:,} samples")
        if dups:
            print(f"    Duplicates: {dups:,} (skipped)")

    # Shuffle
    random.shuffle(all_samples)

    # Write
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    out_size = os.path.getsize(OUTPUT_FILE) / 1024 / 1024

    # Stats
    approved = sum(1 for s in all_samples if "APPROVED" in s.get("output", "").upper())
    rejected = sum(1 for s in all_samples if "REJECTED" in s.get("output", "").upper())
    json_fmt = sum(1 for s in all_samples if s.get("output", "").strip().startswith("{"))
    avg_len = sum(len(s.get("output", "")) for s in all_samples) / len(all_samples) if all_samples else 0

    print(f"\n{'='*55}")
    print(f"  MERGED DATASET")
    print(f"{'='*55}")
    print(f"  Total samples: {len(all_samples):,}")
    print(f"  Duplicates removed: {duplicates:,}")
    print(f"  File size: {out_size:.1f} MB")
    print(f"  Output: {OUTPUT_FILE}")
    print(f"\n  Breakdown:")
    print(f"    Approved trades: {approved:,}")
    print(f"    Rejected trades: {rejected:,}")
    print(f"    JSON format:     {json_fmt:,}")
    print(f"    Avg output len:  {avg_len:.0f} chars")
    print(f"\n  Upload this file to Colab V3 notebook.")
    print()


if __name__ == "__main__":
    main()

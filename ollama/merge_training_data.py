#!/usr/bin/env python3
"""
RUNECLAW - Training Data Merge & Normalize
============================================
Merges multiple JSONL training data sources, normalizes schema drift
between Claude Sonnet/Haiku outputs, and deduplicates.

Usage:
  python merge_training_data.py colab_data.jsonl local_haiku.jsonl
  python merge_training_data.py colab_data.jsonl local_haiku.jsonl --output training_data/combined_training_claude.jsonl
  python merge_training_data.py *.jsonl --stats

Output:
  ./training_data/combined_training_claude.jsonl  (default)
"""

import json
import hashlib
import re
import sys
import argparse
from pathlib import Path
from collections import Counter


# ── Normalization rules ──────────────────────────────────────────────────────
# Fix common schema drift between Sonnet and Haiku outputs

def normalize_output(text: str) -> str:
    """Normalize output text to canonical RUNECLAW format."""
    # Standardize field labels (Sonnet vs Haiku drift)
    replacements = [
        # Take Profit variants → canonical
        (r"Take Profit Target[:\s]", "Take Profit 1: "),
        (r"TP1[:\s]", "Take Profit 1: "),
        (r"TP 1[:\s]", "Take Profit 1: "),
        (r"Take-Profit[:\s]", "Take Profit 1: "),
        (r"TP2[:\s]", "Take Profit 2: "),
        (r"TP 2[:\s]", "Take Profit 2: "),
        # Stop Loss variants
        (r"SL[:\s](?=\d)", "Stop Loss: "),
        (r"Stop-Loss[:\s]", "Stop Loss: "),
        (r"Stoploss[:\s]", "Stop Loss: "),
        # Risk:Reward variants
        (r"R/R[:\s]", "Risk:Reward: "),
        (r"RR[:\s](?=\d)", "Risk:Reward: "),
        (r"Risk-Reward[:\s]", "Risk:Reward: "),
        (r"Risk to Reward[:\s]", "Risk:Reward: "),
        # Confluence variants
        (r"Confluence Score[:\s]", "Confluence: "),
        (r"GetClaw Score[:\s]", "Confluence: "),
        (r"Confluence Rating[:\s]", "Confluence: "),
        # Direction variants
        (r"Bias[:\s]+(LONG|SHORT|Long|Short)", r"Direction: \1"),
        (r"Trade Direction[:\s]", "Direction: "),
        # Status/Verdict variants
        (r"Verdict[:\s]+(APPROVED|REJECTED)", r"Status: \1"),
        (r"Decision[:\s]+(APPROVED|REJECTED)", r"Status: \1"),
        (r"REQUIRES.REVIEW", "REQUIRES_REVIEW"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Normalize direction to uppercase
    text = re.sub(r"Direction:\s*(long|short)", lambda m: f"Direction: {m.group(1).upper()}", text)

    # Normalize whitespace: collapse multiple blank lines to one
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Strip trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    return text.strip()


def normalize_instruction(text: str) -> str:
    """Normalize instruction text for consistency."""
    # Strip trailing periods inconsistency
    text = text.strip().rstrip(".")
    # Normalize common phrasing
    text = re.sub(r"Please analyze", "Analyze", text, flags=re.IGNORECASE)
    text = re.sub(r"Can you analyze", "Analyze", text, flags=re.IGNORECASE)
    text = re.sub(r"Generate a trade idea for", "Analyze", text, flags=re.IGNORECASE)
    return text.strip()


def normalize_sample(sample: dict) -> dict:
    """Normalize a single training sample."""
    return {
        "instruction": normalize_instruction(sample.get("instruction", "")),
        "input": sample.get("input", "").strip(),
        "output": normalize_output(sample.get("output", "")),
    }


# ── Deduplication ────────────────────────────────────────────────────────────

def sample_hash(sample: dict) -> str:
    """Hash based on instruction text for exact-match dedup."""
    key = sample.get("instruction", "").strip().lower()
    return hashlib.md5(key.encode()).hexdigest()


def sample_content_hash(sample: dict) -> str:
    """Hash based on instruction + first 200 chars of output for near-dedup."""
    key = (
        sample.get("instruction", "").strip().lower() +
        "|" +
        sample.get("output", "")[:200].strip().lower()
    )
    return hashlib.md5(key.encode()).hexdigest()


# ── Statistics ───────────────────────────────────────────────────────────────

def compute_stats(samples: list[dict]) -> dict:
    """Compute dataset statistics."""
    stats = {
        "total": len(samples),
        "avg_output_len": 0,
        "direction_dist": Counter(),
        "verdict_dist": Counter(),
        "has_entry": 0,
        "has_stop": 0,
        "has_tp": 0,
        "has_confluence": 0,
        "has_regime": 0,
    }

    output_lens = []
    for s in samples:
        out = s.get("output", "")
        output_lens.append(len(out))

        out_upper = out.upper()
        # Direction
        if "DIRECTION: LONG" in out_upper or "LONG" in out_upper[:100]:
            stats["direction_dist"]["LONG"] += 1
        elif "DIRECTION: SHORT" in out_upper or "SHORT" in out_upper[:100]:
            stats["direction_dist"]["SHORT"] += 1
        else:
            stats["direction_dist"]["NONE/OTHER"] += 1

        # Verdict
        if "APPROVED" in out_upper:
            stats["verdict_dist"]["APPROVED"] += 1
        elif "REJECTED" in out_upper:
            stats["verdict_dist"]["REJECTED"] += 1
        elif "REQUIRES_REVIEW" in out_upper:
            stats["verdict_dist"]["REQUIRES_REVIEW"] += 1
        else:
            stats["verdict_dist"]["NONE"] += 1

        # Field presence
        if re.search(r"Entry[:\s]+[\d.]", out):
            stats["has_entry"] += 1
        if re.search(r"Stop Loss[:\s]+[\d.]", out):
            stats["has_stop"] += 1
        if re.search(r"Take Profit[:\s]+[\d.]", out):
            stats["has_tp"] += 1
        if re.search(r"Confluence[:\s]+[\d.]", out):
            stats["has_confluence"] += 1
        if any(r in out_upper for r in ["TRENDING", "RANGING", "CHOPPY", "VOLATILE",
                                         "ACCUMULATION", "DISTRIBUTION"]):
            stats["has_regime"] += 1

    stats["avg_output_len"] = sum(output_lens) / len(output_lens) if output_lens else 0
    stats["min_output_len"] = min(output_lens) if output_lens else 0
    stats["max_output_len"] = max(output_lens) if output_lens else 0

    return stats


def print_stats(stats: dict, label: str = ""):
    """Print dataset statistics."""
    print(f"\n{'='*50}")
    if label:
        print(f"  {label}")
        print(f"{'='*50}")
    print(f"  Total samples:     {stats['total']:,}")
    print(f"  Output length:     avg={stats['avg_output_len']:.0f}, "
          f"min={stats['min_output_len']}, max={stats['max_output_len']}")
    print(f"\n  Direction distribution:")
    for k, v in stats["direction_dist"].most_common():
        pct = v / stats["total"] * 100
        bar = "#" * int(pct / 2)
        print(f"    {k:15s} {v:6,} ({pct:5.1f}%) {bar}")
    print(f"\n  Verdict distribution:")
    for k, v in stats["verdict_dist"].most_common():
        pct = v / stats["total"] * 100
        bar = "#" * int(pct / 2)
        print(f"    {k:15s} {v:6,} ({pct:5.1f}%) {bar}")
    print(f"\n  Field coverage:")
    for field in ["has_entry", "has_stop", "has_tp", "has_confluence", "has_regime"]:
        v = stats[field]
        pct = v / stats["total"] * 100
        name = field.replace("has_", "")
        print(f"    {name:15s} {v:6,} ({pct:5.1f}%)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Merge and normalize RUNECLAW training data")
    parser.add_argument("files", nargs="+", help="JSONL files to merge")
    parser.add_argument("--output", default="./training_data/combined_training_claude.jsonl",
                        help="Output file path")
    parser.add_argument("--stats", action="store_true", help="Print dataset statistics")
    parser.add_argument("--no-normalize", action="store_true", help="Skip normalization")
    parser.add_argument("--no-dedup", action="store_true", help="Skip deduplication")
    parser.add_argument("--dedup-mode", choices=["exact", "near"], default="near",
                        help="Dedup by instruction only (exact) or instruction+output (near)")
    args = parser.parse_args()

    # Load all files
    all_samples = []
    file_counts = {}
    for filepath in args.files:
        p = Path(filepath)
        if not p.exists():
            print(f"WARNING: {filepath} not found, skipping")
            continue
        count = 0
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        sample = json.loads(line)
                        # Validate required fields
                        if "instruction" in sample and "output" in sample:
                            all_samples.append(sample)
                            count += 1
                        else:
                            pass  # skip malformed
                    except json.JSONDecodeError:
                        pass  # skip bad lines
        file_counts[str(p)] = count
        print(f"  Loaded {count:,} samples from {p.name}")

    if not all_samples:
        print("ERROR: No valid samples found in any input file.")
        sys.exit(1)

    print(f"\nTotal loaded: {len(all_samples):,} samples from {len(file_counts)} files")

    # Normalize
    if not args.no_normalize:
        print("Normalizing schema...")
        all_samples = [normalize_sample(s) for s in all_samples]
        print(f"  Normalized {len(all_samples):,} samples")

    # Dedup
    if not args.no_dedup:
        hash_fn = sample_content_hash if args.dedup_mode == "near" else sample_hash
        seen = set()
        deduped = []
        dupes = 0
        for s in all_samples:
            h = hash_fn(s)
            if h not in seen:
                seen.add(h)
                deduped.append(s)
            else:
                dupes += 1
        print(f"  Deduplication ({args.dedup_mode}): removed {dupes:,} duplicates")
        all_samples = deduped

    # Stats
    if args.stats:
        stats = compute_stats(all_samples)
        print_stats(stats, f"Merged dataset ({len(all_samples):,} samples)")

        # Check for approval bias
        approved = stats["verdict_dist"].get("APPROVED", 0)
        rejected = stats["verdict_dist"].get("REJECTED", 0) + stats["verdict_dist"].get("REQUIRES_REVIEW", 0)
        if approved > 0 and rejected > 0:
            ratio = approved / rejected
            if ratio > 3.0:
                print(f"\n  WARNING: Approval bias detected ({ratio:.1f}:1 approve:reject)")
                print(f"  Consider adding more rejection examples to balance.")
            elif ratio < 0.5:
                print(f"\n  NOTE: Rejection-heavy dataset ({1/ratio:.1f}:1 reject:approve)")
        print()

    # Save
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Saved {len(all_samples):,} samples to {out_path}")
    print(f"\nNext: python train_max_8b.py")


if __name__ == "__main__":
    main()

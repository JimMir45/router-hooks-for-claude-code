#!/usr/bin/env python3
"""
sample_router_log.py — 从 router.log 分层抽 50 条真实 prompt,产生 baseline 测试集

输入: ~/.claude/router-logs/router.log
输出: tests/cases/real-world-replay.jsonl

抽样规则:
- 按 framework_primary 分层: GS 17 / CC 14 / ECC 12 / SP 7 = 50
- prompt_hash 去重
- 80% 抽 confidence >= 0.7 的常规 case, 20% 抽 confidence < 0.6 的难 case
- 噪声过滤: <task-notification>, <system-reminder>, <<autonomous-loop, <command-name>,
           PreToolUse:, 短于 10 字符, error decision
"""

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROUTER_LOG = Path.home() / ".claude/router-logs/router.log"
OUTPUT_PATH = Path(__file__).parent / "cases/real-world-replay.jsonl"

NOISE_MARKERS = (
    "<task-notification>",
    "<system-reminder>",
    "<<autonomous-loop",
    "<command-name>",
    "PreToolUse:",
)

QUOTAS = {"GS": 17, "CC": 14, "ECC": 12, "SP": 7}
HARD_CASE_RATIO = 0.20  # 20% 难 case (confidence < 0.6)
SEED = 42


def is_noise(prompt: str) -> bool:
    if len(prompt) < 10:
        return True
    return any(m in prompt for m in NOISE_MARKERS)


def load_records():
    records = []
    with ROUTER_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            decision = rec.get("decision") or {}
            if "error" in decision:
                continue
            prompt = rec.get("prompt_preview", "")
            if is_noise(prompt):
                continue
            fw = decision.get("framework_primary")
            if fw not in QUOTAS:
                continue
            records.append(rec)
    return records


def dedupe_by_hash(records):
    seen = {}
    for rec in records:
        h = rec.get("prompt_hash")
        if not h:
            continue
        if h not in seen:
            seen[h] = rec
    return list(seen.values())


def stratified_sample(records, rng):
    by_fw = defaultdict(list)
    for rec in records:
        fw = rec["decision"]["framework_primary"]
        by_fw[fw].append(rec)

    sampled = []
    stats = {}
    for fw, quota in QUOTAS.items():
        pool = by_fw.get(fw, [])
        n_hard = max(1, int(round(quota * HARD_CASE_RATIO)))
        n_easy = quota - n_hard

        easy_pool = [r for r in pool if r["decision"].get("confidence", 0) >= 0.7]
        hard_pool = [r for r in pool if r["decision"].get("confidence", 0) < 0.6]

        easy_pick = rng.sample(easy_pool, min(n_easy, len(easy_pool)))
        hard_pick = rng.sample(hard_pool, min(n_hard, len(hard_pool)))

        # 如果 pool 不够,从另一边补
        deficit = quota - len(easy_pick) - len(hard_pick)
        if deficit > 0:
            remaining = [r for r in pool if r not in easy_pick and r not in hard_pick]
            extra = rng.sample(remaining, min(deficit, len(remaining)))
            easy_pick.extend(extra)

        stats[fw] = {
            "quota": quota,
            "pool_total": len(pool),
            "pool_easy": len(easy_pool),
            "pool_hard": len(hard_pool),
            "picked_easy": len(easy_pick),
            "picked_hard": len(hard_pick),
        }
        sampled.extend(easy_pick + hard_pick)

    return sampled, stats


def write_output(sampled):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w") as f:
        for i, rec in enumerate(sampled, 1):
            d = rec["decision"]
            out = {
                "id": f"real_{i:03d}",
                "prompt": rec["prompt_preview"],
                "v3.1_decision": d,
                "framework": d.get("framework_primary"),
                "confidence": d.get("confidence"),
                "category": "real-replay",
                "prompt_hash": rec.get("prompt_hash"),
                "ts": rec.get("ts"),
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


def main():
    rng = random.Random(SEED)
    raw = load_records()
    deduped = dedupe_by_hash(raw)
    print(f"loaded raw={len(raw)} after_dedupe={len(deduped)}")

    by_fw = defaultdict(int)
    for r in deduped:
        by_fw[r["decision"]["framework_primary"]] += 1
    print("framework distribution (deduped):")
    for fw, n in sorted(by_fw.items(), key=lambda x: -x[1]):
        print(f"  {fw}: {n}")

    sampled, stats = stratified_sample(deduped, rng)
    write_output(sampled)

    print(f"\nsampled total={len(sampled)} -> {OUTPUT_PATH}")
    for fw, s in stats.items():
        print(
            f"  {fw}: pool={s['pool_total']} (easy={s['pool_easy']} hard={s['pool_hard']}) "
            f"picked={s['picked_easy']}+{s['picked_hard']}/{s['quota']}"
        )


if __name__ == "__main__":
    main()

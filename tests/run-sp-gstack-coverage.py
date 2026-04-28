#!/usr/bin/env python3
"""Run SP+gstack skill coverage. Uses minimal reconstructed case set (~35 cases)
since original v3.0 SP+gstack run was inline (no case file persisted).

This is a directional check, not a full v3.0 reproduction. Results give
indication whether v3.1 SP keyword expansion + ECC fallback hurt or helped
SP/gstack routing.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
HOOK_DIR = Path(os.environ.get("ROUTER_HOOK_DIR", Path.home() / ".router-hook"))
CASES_FILE = HERE / "cases" / "sp-gstack-coverage.jsonl"
RESULTS_FILE = HERE / "results" / "sp-gstack-coverage.json"
JSONL_FILE = HERE / "results" / "sp-gstack-coverage.jsonl"
SUMMARY_FILE = HERE / "results" / "sp-gstack-coverage-summary.md"
ROUTER_PY = HOOK_DIR / "router.py"
TIMEOUT = 25


def call_router(prompt: str) -> dict:
    payload = json.dumps({"prompt": prompt, "session_id": "sp-gs-coverage", "cwd": "/tmp"},
                         ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    try:
        proc = subprocess.run([sys.executable, str(ROUTER_PY)],
                              input=payload, capture_output=True, timeout=TIMEOUT)
        return {"stdout": proc.stdout.decode("utf-8", errors="replace").strip(),
                "exit_code": proc.returncode,
                "latency_ms": int((time.time() - t0) * 1000)}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "exit_code": -1, "latency_ms": int((time.time() - t0) * 1000)}


def parse(stdout: str) -> dict:
    if not stdout:
        return {"actual_fw": "CC", "actual_role": None, "actual_sub": None, "confidence": None}
    m = re.search(r"🧭 Router → ([\w\-\(\) ]+?)\s+\(conf ([\d.]+)\)", stdout)
    routed = m.group(1).strip() if m else "unknown"
    conf = float(m.group(2)) if m else None
    if "Superpowers" in routed: fw = "SP"
    elif "gstack" in routed: fw = "GS"
    elif "ECC" in routed: fw = "ECC"
    elif "CC" in routed or "原生" in routed: fw = "CC"
    else: fw = "unknown"
    role = None; sub = None
    if fw == "GS":
        m2 = re.search(r"gstack-(\w+)", routed)
        role = m2.group(1) if m2 else None
    if fw == "ECC":
        m3 = re.search(r"ECC-(\w+)", routed)
        sub = m3.group(1) if m3 else None
    return {"actual_fw": fw, "actual_role": role, "actual_sub": sub, "confidence": conf}


def evaluate(case, parsed):
    expected = case["expected_fw"]
    actual = parsed["actual_fw"]
    fw_match = (actual == expected)
    # reasonable: SP→GS or GS→SP (cross-framework but valid)
    reasonable = fw_match
    if not fw_match:
        if expected in ("SP", "GS") and actual in ("SP", "GS"): reasonable = True
        elif expected == "GS" and actual == "ECC": reasonable = True  # ECC is fine for tech work
    return {"fw_match": fw_match, "reasonable": reasonable}


def main():
    if not ROUTER_PY.exists():
        print(f"ERROR: router not found at {ROUTER_PY}", file=sys.stderr); sys.exit(1)
    cases = [json.loads(l) for l in open(CASES_FILE) if l.strip()]
    print(f"Running {len(cases)} SP+gstack coverage cases...")
    mode_file = Path.home() / ".config" / "router-hook" / "mode"
    orig_mode = None
    try:
        if mode_file.exists(): orig_mode = mode_file.read_text().strip()
        mode_file.parent.mkdir(parents=True, exist_ok=True)
        mode_file.write_text("auto")
    except Exception: pass

    results = []
    try:
        for i, case in enumerate(cases, 1):
            raw = call_router(case["prompt"])
            parsed = parse(raw["stdout"])
            ev = evaluate(case, parsed)
            results.append({**case, **parsed, **ev, "latency_ms": raw["latency_ms"]})
            mark = "✓" if ev["fw_match"] else ("~" if ev["reasonable"] else "✗")
            print(f"  [{i}/{len(cases)}] {mark} {case['skill']:25s} expect={case['expected_fw']:3s} → {parsed['actual_fw']}")
    finally:
        try:
            if orig_mode is not None: mode_file.write_text(orig_mode)
            elif mode_file.exists(): mode_file.write_text("silent")
        except Exception: pass

    total = len(results)
    fw_match = sum(1 for r in results if r["fw_match"])
    reasonable = sum(1 for r in results if r["reasonable"])
    by_fw = {}
    for r in results:
        by_fw.setdefault(r["framework"], []).append(r)
    summary = {
        "total_cases": total,
        "fw_match_count": fw_match,
        "reasonable_count": reasonable,
        "fw_match_accuracy": round(fw_match/total, 3),
        "reasonable_accuracy": round(reasonable/total, 3),
        "by_framework": {k: {"total": len(v),
                              "fw_match": sum(1 for r in v if r["fw_match"]),
                              "reasonable": sum(1 for r in v if r["reasonable"])}
                          for k,v in by_fw.items()},
        "results": results,
    }
    JSONL_FILE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n")
    RESULTS_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nResults: {JSONL_FILE}\nSummary: {RESULTS_FILE}")
    print(f"FW match: {fw_match}/{total} = {fw_match/total*100:.1f}%")
    print(f"Reasonable: {reasonable}/{total} = {reasonable/total*100:.1f}%")
    for fw, stats in summary["by_framework"].items():
        print(f"  {fw}: fw_match {stats['fw_match']}/{stats['total']}, reasonable {stats['reasonable']}/{stats['total']}")


if __name__ == "__main__":
    main()

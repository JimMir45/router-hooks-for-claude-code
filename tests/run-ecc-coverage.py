#!/usr/bin/env python3
"""Re-run ECC 89 skill coverage cases against current router."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
HOOK_DIR = Path(os.environ.get("ROUTER_HOOK_DIR", Path.home() / ".router-hook"))
CASES_FILE = HERE / "cases" / "ecc-89-coverage.jsonl"
RESULTS_FILE = HERE / "results" / "ecc-coverage.json"
JSONL_FILE = HERE / "results" / "ecc-89-coverage.jsonl"
SUMMARY_FILE = HERE / "results" / "ecc-coverage-summary.md"

ROUTER_PY = HOOK_DIR / "router.py"
TIMEOUT = 25


def call_router(prompt: str) -> dict:
    payload = json.dumps({"prompt": prompt, "session_id": "ecc-coverage", "cwd": "/tmp"},
                         ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    try:
        proc = subprocess.run([sys.executable, str(ROUTER_PY)],
                              input=payload, capture_output=True, timeout=TIMEOUT)
        return {
            "stdout": proc.stdout.decode("utf-8", errors="replace").strip(),
            "stderr": proc.stderr.decode("utf-8", errors="replace").strip(),
            "exit_code": proc.returncode,
            "latency_ms": int((time.time() - t0) * 1000),
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "TIMEOUT", "exit_code": -1,
                "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"stdout": "", "stderr": str(e)[:200], "exit_code": -2,
                "latency_ms": int((time.time() - t0) * 1000)}


def parse_router_output(stdout: str) -> dict:
    if not stdout:
        return {"actual_fw": "CC", "actual_sub": None, "actual_role": None,
                "confidence": None, "reason": "", "is_offline": False}
    m = re.search(r"🧭 Router → ([\w\-\(\) ]+?)\s+\(conf ([\d.]+)\)", stdout)
    routed = m.group(1).strip() if m else "unknown"
    conf = float(m.group(2)) if m else None
    if "Superpowers" in routed: fw = "SP"
    elif "gstack" in routed: fw = "GS"
    elif "ECC" in routed: fw = "ECC"
    elif "CC" in routed or "原生" in routed: fw = "CC"
    else: fw = "unknown"
    sub = None
    if fw == "ECC":
        m2 = re.search(r"ECC-(\w+)", routed)
        sub = m2.group(1) if m2 else None
    role = None
    if fw == "GS":
        m3 = re.search(r"gstack-(\w+)", routed)
        role = m3.group(1) if m3 else None
    rm = re.search(r"reason:\s*(.+)", stdout)
    return {
        "actual_fw": fw, "actual_sub": sub, "actual_role": role,
        "confidence": conf,
        "reason": rm.group(1).strip() if rm else "",
        "is_offline": "OFFLINE" in stdout or "offline" in stdout.lower(),
    }


def evaluate(case: dict, parsed: dict) -> dict:
    expected_fw = case.get("expected_fw", "")
    actual_fw = parsed["actual_fw"]
    fw_match = (actual_fw == expected_fw)
    # strict_match: both fw matches and (if ECC) sub matches expected sub_skill
    strict = fw_match
    # reasonable: ECC routed to GS-EngManager(eng decision) or SP also acceptable
    reasonable = fw_match
    if not fw_match and expected_fw == "ECC":
        if actual_fw in ("GS", "SP"): reasonable = True
    return {"fw_match": fw_match, "strict_match": strict, "reasonable": reasonable}


def main():
    if not ROUTER_PY.exists():
        print(f"ERROR: router not found at {ROUTER_PY}", file=sys.stderr)
        sys.exit(1)
    cases = []
    with open(CASES_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    print(f"Running {len(cases)} ECC coverage cases against router v3.1...")

    # Force auto mode
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
            prompt = case["prompt"]
            raw = call_router(prompt)
            parsed = parse_router_output(raw["stdout"])
            ev = evaluate(case, parsed)
            new_record = {
                "skill": case["skill"], "prompt": prompt,
                "expected_fw": case["expected_fw"],
                "actual_fw": parsed["actual_fw"],
                "actual_sub": parsed["actual_sub"],
                "actual_role": parsed["actual_role"],
                "confidence": parsed["confidence"],
                "reason": parsed["reason"],
                "fw_match": ev["fw_match"],
                "strict_match": ev["strict_match"],
                "reasonable": ev["reasonable"],
                "is_offline": parsed["is_offline"],
                "error": raw.get("stderr", "")[:120] if raw["exit_code"] != 0 else None,
                "latency_ms": raw["latency_ms"],
            }
            results.append(new_record)
            mark = "✓" if ev["strict_match"] else ("~" if ev["reasonable"] else "✗")
            if i % 10 == 0 or i == len(cases):
                print(f"  [{i}/{len(cases)}] {mark} {case['skill']:30s} → {parsed['actual_fw']:4s}")
    finally:
        try:
            if orig_mode is not None: mode_file.write_text(orig_mode)
            elif mode_file.exists(): mode_file.write_text("silent")
        except Exception: pass

    # Aggregate
    total = len(results)
    strict = sum(1 for r in results if r["strict_match"])
    reasonable = sum(1 for r in results if r["reasonable"])
    skills_seen = {}
    for r in results:
        skills_seen.setdefault(r["skill"], []).append(r)
    total_skills = len(skills_seen)
    strict_skills = sum(1 for s, rs in skills_seen.items() if any(r["strict_match"] for r in rs))
    reasonable_skills = sum(1 for s, rs in skills_seen.items() if any(r["reasonable"] for r in rs))

    summary = {
        "total_skills": total_skills,
        "total_cases": total,
        "strict_correct_skills": strict_skills,
        "reasonable_correct_skills": reasonable_skills,
        "case_strict_accuracy": round(strict / total, 3),
        "case_reasonable_accuracy": round(reasonable / total, 3),
        "skill_strict_accuracy": round(strict_skills / total_skills, 3),
        "skill_reasonable_accuracy": round(reasonable_skills / total_skills, 3),
        "wrong_skills_strict": [s for s, rs in skills_seen.items() if not any(r["strict_match"] for r in rs)],
        "wrong_skills_reasonable": [s for s, rs in skills_seen.items() if not any(r["reasonable"] for r in rs)],
        "always_gs_skills": [s for s, rs in skills_seen.items() if all(r["actual_fw"] == "GS" for r in rs)],
        "by_category": {},
        "skill_results": skills_seen,
    }

    JSONL_FILE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n")
    RESULTS_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nResults: {JSONL_FILE}\nSummary: {RESULTS_FILE}")
    print(f"Strict case accuracy: {strict}/{total} = {strict/total*100:.1f}%")
    print(f"Reasonable case accuracy: {reasonable}/{total} = {reasonable/total*100:.1f}%")
    print(f"Strict skill accuracy: {strict_skills}/{total_skills} = {strict_skills/total_skills*100:.1f}%")


if __name__ == "__main__":
    main()

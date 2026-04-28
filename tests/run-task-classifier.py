#!/usr/bin/env python3
"""
run-task-classifier.py — 跑 ① 任务分诊 4 case + P0 13 case,看 task_classifier 准确率

输入: tests/cases/synthetic-triage.jsonl + tests/cases/p0-cases.jsonl
输出: tests/results/task-classifier.jsonl + .md summary

通过 router.py 全链路跑(LLM + hard_regex + task_classifier),拿最终 decision 验 task_type。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "hook" / "router.py"
TRIAGE_CASES = ROOT / "tests" / "cases" / "synthetic-triage.jsonl"
P0_CASES = ROOT / "tests" / "cases" / "p0-cases.jsonl"
RESULTS = ROOT / "tests" / "results" / "task-classifier.jsonl"
SUMMARY = ROOT / "tests" / "results" / "task-classifier-summary.md"

# 把 P0 的 expected_v3.1.framework 推断成 expected.task_type
FW_TO_EXPECTED_TYPE = {"SP": "execution", "GS": "decision", "ECC": "domain", "CC": "simple"}


def call_router(prompt: str) -> dict:
    payload = {"prompt": prompt, "session_id": "classifier-test", "cwd": str(ROOT)}
    env = os.environ.copy()
    env["ROUTER_HOOK_MODE"] = "auto"
    isolated_home = ROOT / "tests" / "results" / ".isolated-home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    real_config = Path.home() / ".config"
    isolated_config = isolated_home / ".config"
    if not isolated_config.exists():
        isolated_config.symlink_to(real_config)
    env["HOME"] = str(isolated_home)

    log_path = isolated_home / ".claude" / "router-logs" / "router.log"
    if log_path.exists():
        log_path.unlink()

    try:
        proc = subprocess.run(
            [sys.executable, str(ROUTER)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}

    if not log_path.exists():
        return {"error": "no_log", "stderr": proc.stderr[:200]}
    last = None
    for line in log_path.read_text().splitlines():
        if line.strip():
            last = line
    if not last:
        return {"error": "empty_log"}
    return json.loads(last).get("decision", {"error": "no_decision"})


def expected_for(case: dict) -> dict:
    """Triage case 直接读 expected;P0 case 从 framework 推断 task_type。"""
    if "expected" in case:
        return case["expected"]
    fw = case.get("expected_v3.1", {}).get("framework")
    return {"task_type": FW_TO_EXPECTED_TYPE.get(fw)}


def main():
    cases = []
    for src in [TRIAGE_CASES, P0_CASES]:
        if src.exists():
            for line in src.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    rec["_source"] = src.name
                    cases.append(rec)

    print(f"loaded {len(cases)} cases ({TRIAGE_CASES.name}={sum(1 for c in cases if c['_source']==TRIAGE_CASES.name)}, "
          f"{P0_CASES.name}={sum(1 for c in cases if c['_source']==P0_CASES.name)})")

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    pass_count = 0

    with RESULTS.open("w") as f:
        for i, case in enumerate(cases, 1):
            cid = case["id"]
            prompt = case["prompt"]
            expected = expected_for(case)
            actual = call_router(prompt)

            exp_type = expected.get("task_type")
            act_type = actual.get("task_type")
            ok = exp_type == act_type if exp_type else None

            row = {
                "id": cid,
                "source": case["_source"],
                "prompt": prompt[:80],
                "expected_type": exp_type,
                "actual_type": act_type,
                "actual_fw": actual.get("framework_primary"),
                "actual_dispatch": actual.get("dispatch_target"),
                "classifier_reason": actual.get("classifier_reason"),
                "pass": ok,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)

            mark = "✅" if ok else ("❌" if ok is False else "—")
            print(f"  [{i:2}/{len(cases)}] {cid:18} {mark} expected={exp_type:9} actual={act_type or '-':9} fw={actual.get('framework_primary','-')}")
            if ok:
                pass_count += 1

    # summary
    triage_rows = [r for r in rows if r["source"] == TRIAGE_CASES.name]
    p0_rows = [r for r in rows if r["source"] == P0_CASES.name]
    triage_pass = sum(1 for r in triage_rows if r["pass"])
    p0_pass = sum(1 for r in p0_rows if r["pass"])

    lines = [
        "# Task Classifier 测试结果",
        "",
        f"**总通过率**: {pass_count}/{len(cases)} = {100*pass_count/len(cases):.1f}%",
        "",
        "## 分类目标",
        "",
        f"- ① 任务分诊 (synthetic-triage): **{triage_pass}/{len(triage_rows)}** {'✅' if triage_pass == len(triage_rows) else '⚠️'}",
        f"- P0 case 衍生 (p0-cases): **{p0_pass}/{len(p0_rows)}**",
        "",
        "## 失败明细",
        "",
    ]
    fails = [r for r in rows if r["pass"] is False]
    if not fails:
        lines.append("(无)")
    else:
        lines.append("| ID | prompt | expected | actual | classifier_reason |")
        lines.append("|---|---|---|---|---|")
        for r in fails:
            lines.append(f"| {r['id']} | {r['prompt']}… | {r['expected_type']} | {r['actual_type']} | {r['classifier_reason']} |")
    lines.append("")
    lines.append("## Day 2 验收门槛")
    lines.append("")
    lines.append("- ① 任务分诊 4/4 必须过(active-plan 明文要求)")
    lines.append(f"- 实际: **{triage_pass}/4** — {'✅ 通过' if triage_pass == 4 else '❌ 未达标'}")

    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== {pass_count}/{len(cases)} pass ===")
    print(f"  triage: {triage_pass}/{len(triage_rows)}")
    print(f"  p0:     {p0_pass}/{len(p0_rows)}")
    print(f"\nresults: {RESULTS}")
    print(f"summary: {SUMMARY}")


if __name__ == "__main__":
    main()

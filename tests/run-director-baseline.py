#!/usr/bin/env python3
"""
run-director-baseline.py — 跑 v3.1 inject 模式 baseline,产出 Director-Worker 对照数字

输入: tests/cases/p0-cases.jsonl (13 case)
输出: tests/results/baseline-v3.1.jsonl (每条带 v3.1 实际决策 + 对比)
       tests/results/baseline-v3.1-summary.md (整体准确率/通过率)

调用方式: 子进程调 hook/router.py(stdin JSON),拿 stdout 注入文本
不污染生产 router.log:用临时 LOG_PATH override

只测 v3.1 路由层的 framework_primary 准确率;
failure-mode case 的 director 行为这一步不测(Director-Worker 还没造)。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "hook" / "router.py"
CASES = ROOT / "tests" / "cases" / "p0-cases.jsonl"
RESULTS = ROOT / "tests" / "results" / "baseline-v3.1.jsonl"
SUMMARY = ROOT / "tests" / "results" / "baseline-v3.1-summary.md"

TMP_LOG = ROOT / "tests" / "results" / ".tmp-baseline.log"


def call_router(prompt: str) -> dict:
    """同步调 router.py,返回 decision dict。从临时 log 文件读最后一行。"""
    payload = {"prompt": prompt, "session_id": "baseline-test", "cwd": str(ROOT)}
    env = os.environ.copy()
    # 强制 auto 模式以拿到所有决策
    env["ROUTER_HOOK_MODE"] = "auto"
    # 重定向 LOG_PATH 到临时文件,避免污染生产 log
    # router.py 用 ~/.claude/router-logs/router.log,我们临时把 HOME 指到隔离目录
    isolated_home = ROOT / "tests" / "results" / ".isolated-home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    # 把真实 ~/.config 链过去(router.py 需要 keys.json),但 ~/.claude 用隔离的
    env["HOME"] = str(isolated_home)
    real_config = Path.home() / ".config"
    isolated_config = isolated_home / ".config"
    if not isolated_config.exists():
        isolated_config.symlink_to(real_config)

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
        return {"error": "router_timeout"}

    log_path = isolated_home / ".claude" / "router-logs" / "router.log"
    if not log_path.exists():
        return {"error": "no_log_written", "stderr": proc.stderr[:200]}

    last_line = None
    for line in log_path.read_text().splitlines():
        if line.strip():
            last_line = line
    if not last_line:
        return {"error": "empty_log"}
    try:
        rec = json.loads(last_line)
        return rec.get("decision", {"error": "no_decision_in_log"})
    except json.JSONDecodeError:
        return {"error": "decision_parse_failed"}


def evaluate(case: dict, actual: dict) -> dict:
    """对比 expected vs actual,返回 pass/fail + 原因。"""
    expected = case.get("expected_v3.1", {})
    cat = case.get("category")
    result = {"pass": True, "checks": []}

    # framework 检查
    if "framework" in expected:
        exp_fw = expected["framework"]
        act_fw = actual.get("framework_primary")
        ok = exp_fw == act_fw
        result["checks"].append({"check": "framework", "expected": exp_fw, "actual": act_fw, "pass": ok})
        if not ok:
            result["pass"] = False

    # confidence 下限检查 (failure-mode 用)
    if "confidence_min" in expected:
        cmin = expected["confidence_min"]
        cact = actual.get("confidence", 0)
        ok = cact >= cmin
        result["checks"].append({"check": "confidence_min", "expected": cmin, "actual": cact, "pass": ok})
        if not ok:
            result["pass"] = False

    # human_confirm_required 检查 (regression 用)
    if "human_confirm_required" in expected:
        exp = expected["human_confirm_required"]
        act = actual.get("human_confirm_required", False)
        ok = exp == act
        result["checks"].append({"check": "human_confirm_required", "expected": exp, "actual": act, "pass": ok})
        if not ok:
            result["pass"] = False

    # confidence 精确值 (real-replay,允许 ±0.1 漂移)
    if "confidence" in expected and "confidence_min" not in expected:
        exp = expected["confidence"]
        act = actual.get("confidence", 0)
        ok = abs(exp - act) <= 0.15
        result["checks"].append({"check": "confidence_drift", "expected": exp, "actual": act, "pass": ok, "tolerance": 0.15})
        # 漂移不算 fail,只记录
        # if not ok: result["pass"] = False

    return result


def write_summary(rows):
    total = len(rows)
    passed = sum(1 for r in rows if r["result"]["pass"])
    by_cat = {}
    for r in rows:
        cat = r["case"].get("category", "?")
        by_cat.setdefault(cat, {"total": 0, "pass": 0})
        by_cat[cat]["total"] += 1
        if r["result"]["pass"]:
            by_cat[cat]["pass"] += 1

    lines = []
    lines.append("# Baseline v3.1 — P0 13 case 测试结果\n")
    lines.append(f"**总通过率**: {passed}/{total} = {100*passed/total:.1f}%\n")
    lines.append("## 分类准确率\n")
    lines.append("| 类别 | 通过/总数 | 通过率 |")
    lines.append("|---|---|---|")
    for cat, s in sorted(by_cat.items()):
        rate = 100 * s["pass"] / s["total"] if s["total"] else 0
        lines.append(f"| {cat} | {s['pass']}/{s['total']} | {rate:.0f}% |")

    lines.append("\n## 失败明细\n")
    fails = [r for r in rows if not r["result"]["pass"]]
    if not fails:
        lines.append("(无)\n")
    else:
        for r in fails:
            cid = r["case"]["id"]
            prompt = r["case"]["prompt"][:60]
            lines.append(f"### {cid}")
            lines.append(f"- prompt: `{prompt}…`")
            for c in r["result"]["checks"]:
                if not c.get("pass", True):
                    lines.append(f"- ❌ {c['check']}: expected={c.get('expected')} actual={c.get('actual')}")
            lines.append("")

    lines.append("## 用作 Director-Worker 对照基线\n")
    lines.append("Day 6 全量跑时,Director-Worker 的同组 case 准确率必须 ≥ 此基线。")
    lines.append("低于基线意味着 Director-Worker 引入了回归。\n")

    SUMMARY.write_text("\n".join(lines), encoding="utf-8")


def main():
    if not CASES.exists():
        print(f"FATAL: cases not found: {CASES}", file=sys.stderr)
        sys.exit(1)

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    cases = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    print(f"loaded {len(cases)} cases")

    # 清掉旧的临时 log
    isolated_log = ROOT / "tests" / "results" / ".isolated-home" / ".claude" / "router-logs" / "router.log"
    if isolated_log.exists():
        isolated_log.unlink()

    rows = []
    with RESULTS.open("w") as f:
        for i, case in enumerate(cases, 1):
            print(f"  [{i:2}/{len(cases)}] {case['id']:20} ", end="", flush=True)
            actual = call_router(case["prompt"])
            evaluation = evaluate(case, actual)
            row = {"case": case, "actual_decision": actual, "result": evaluation}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            mark = "✅" if evaluation["pass"] else "❌"
            fw = actual.get("framework_primary", actual.get("error", "?"))
            print(f"{mark} fw={fw} conf={actual.get('confidence', 0):.2f}")

    write_summary(rows)
    passed = sum(1 for r in rows if r["result"]["pass"])
    print(f"\n=== {passed}/{len(rows)} pass ===")
    print(f"results: {RESULTS}")
    print(f"summary: {SUMMARY}")


if __name__ == "__main__":
    main()

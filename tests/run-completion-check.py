#!/usr/bin/env python3
"""
Runner for completion-check.py unit tests.

Usage:
    python3 run-completion-check.py [cases_file]

Output:
    tests/results/completion-check.json
    tests/results/completion-check-summary.md
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path.home() / "router-eval" / "hook" / "completion-check.py"
CASES_FILE = Path(__file__).parent / "cases" / "completion-check-cases.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_FILE = RESULTS_DIR / "completion-check.json"
SUMMARY_FILE = RESULTS_DIR / "completion-check-summary.md"

# Force hook mode to active (not "off") during tests
MODE_FILE = Path.home() / ".config" / "router-hook" / "mode"


def ensure_mode_active():
    """Ensure the mode file doesn't say 'off' during tests."""
    try:
        if MODE_FILE.exists():
            current = MODE_FILE.read_text().strip().lower()
            if current == "off":
                return "off"  # caller will skip
    except Exception:
        pass
    return "active"


def make_transcript_file(transcript_json: str) -> str:
    """Write transcript JSON array as JSONL (one obj per line) to a temp file."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        try:
            entries = json.loads(transcript_json)
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            f.write(transcript_json)
        return f.name


def make_plan_dir(plan_content: str) -> str:
    """Create a temp dir with .claude/active-plan.md for plan-based tests."""
    tmpdir = tempfile.mkdtemp()
    plan_dir = Path(tmpdir) / ".claude"
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "active-plan.md").write_text(plan_content, encoding="utf-8")
    return tmpdir


def run_case(case: dict) -> dict:
    """Run a single test case; return result dict."""
    transcript_path = None
    plan_tmpdir = None
    try:
        transcript_path = make_transcript_file(case["transcript"])

        # Build the payload
        payload = {
            "transcript_path": transcript_path,
            "stop_hook_active": case.get("stop_hook_active", False),
        }

        # Determine working directory
        plan_content = case.get("plan_content")
        if plan_content:
            plan_tmpdir = make_plan_dir(plan_content)
            cwd = plan_tmpdir
        else:
            # Use a clean temp dir with no active-plan.md
            cwd = tempfile.mkdtemp()
            plan_tmpdir = cwd  # mark for cleanup

        payload_str = json.dumps(payload, ensure_ascii=False)

        t0 = time.monotonic()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=payload_str,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        elapsed = time.monotonic() - t0

        stdout = proc.stdout.strip()
        blocked = False
        block_reason = ""

        if stdout:
            try:
                out = json.loads(stdout)
                if out.get("decision") == "block":
                    blocked = True
                    block_reason = out.get("reason", "")[:300]
            except Exception:
                pass

        expect = case["expect"]  # "block" or "pass"
        got = "block" if blocked else "pass"
        passed = got == expect

        return {
            "id": case["id"],
            "category": case.get("category", ""),
            "description": case.get("description", ""),
            "expect": expect,
            "got": got,
            "passed": passed,
            "block_reason": block_reason,
            "elapsed_ms": round(elapsed * 1000, 1),
            "stderr": proc.stderr.strip()[:200] if proc.stderr else "",
        }

    except subprocess.TimeoutExpired:
        return {
            "id": case["id"],
            "category": case.get("category", ""),
            "description": case.get("description", ""),
            "expect": case["expect"],
            "got": "timeout",
            "passed": False,
            "block_reason": "",
            "elapsed_ms": 10000,
            "stderr": "TIMEOUT",
        }
    except Exception as e:
        return {
            "id": case["id"],
            "category": case.get("category", ""),
            "description": case.get("description", ""),
            "expect": case["expect"],
            "got": "error",
            "passed": False,
            "block_reason": "",
            "elapsed_ms": 0,
            "stderr": str(e)[:200],
        }
    finally:
        # Cleanup
        if transcript_path and os.path.exists(transcript_path):
            try:
                os.unlink(transcript_path)
            except Exception:
                pass
        if plan_tmpdir and os.path.exists(plan_tmpdir):
            import shutil
            try:
                shutil.rmtree(plan_tmpdir, ignore_errors=True)
            except Exception:
                pass


def load_cases(cases_file: Path) -> list:
    cases = []
    with open(cases_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    cases.append(json.loads(line))
                except Exception as e:
                    print(f"[WARN] Failed to parse case line: {e}", file=sys.stderr)
    return cases


def build_summary(results: list, total_ms: float) -> str:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    # Category breakdown
    cat_stats = {}
    for r in results:
        cat = r["category"]
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "pass": 0, "fail": 0}
        cat_stats[cat]["total"] += 1
        if r["passed"]:
            cat_stats[cat]["pass"] += 1
        else:
            cat_stats[cat]["fail"] += 1

    # Failed cases
    failed_cases = [r for r in results if not r["passed"]]

    # FP / FN analysis
    false_positives = [r for r in results if r["expect"] == "pass" and r["got"] == "block"]
    false_negatives = [r for r in results if r["expect"] == "block" and r["got"] == "pass"]

    lines = [
        "# completion-check.py 测试报告",
        "",
        f"**运行时间**: {time.strftime('%Y-%m-%dT%H:%M:%S')}",
        f"**总耗时**: {total_ms:.0f} ms",
        "",
        "## 汇总",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总用例 | {total} |",
        f"| 通过 | {passed} |",
        f"| 失败 | {failed} |",
        f"| 准确率 | {passed/total*100:.1f}% |",
        f"| 误报 (FP: 应放行被拦截) | {len(false_positives)} |",
        f"| 漏报 (FN: 应拦截被放行) | {len(false_negatives)} |",
        "",
        "## 分类统计",
        "",
        "| 类别 | 总计 | 通过 | 失败 |",
        "|------|------|------|------|",
    ]

    for cat, s in sorted(cat_stats.items()):
        lines.append(f"| {cat} | {s['total']} | {s['pass']} | {s['fail']} |")

    if failed_cases:
        lines += [
            "",
            "## 失败用例详情",
            "",
        ]
        for r in failed_cases:
            status = "FP(误报)" if r["expect"] == "pass" and r["got"] == "block" else "FN(漏报)"
            lines += [
                f"### {r['id']} [{status}]",
                f"- **描述**: {r['description']}",
                f"- **期望**: {r['expect']} → **实际**: {r['got']}",
            ]
            if r.get("block_reason"):
                short_reason = r["block_reason"][:150].replace("\n", " ")
                lines.append(f"- **拦截原因**: {short_reason}")
            if r.get("stderr"):
                lines.append(f"- **stderr**: {r['stderr'][:100]}")
            lines.append("")

    # Notable cases discussion
    lines += [
        "## 值得讨论的 Case",
        "",
    ]

    # Select interesting cases: FPs (false positives — wrong blocks) are most critical
    # then FNs (missed hedges), then edge behaviors
    notable = []
    notable.extend(false_positives[:3])
    notable.extend(false_negatives[:3])
    # fill up to 5 from interesting edges if needed
    edge_interesting = [
        r for r in results
        if r["category"] == "edge" and r["id"] not in {n["id"] for n in notable}
    ]
    notable.extend(edge_interesting[: max(0, 5 - len(notable))])
    notable = notable[:5]

    for i, r in enumerate(notable, 1):
        status = "通过" if r["passed"] else "失败"
        fp_fn = ""
        if not r["passed"]:
            fp_fn = " [FP]" if r["expect"] == "pass" else " [FN]"
        lines += [
            f"{i}. **{r['id']}** ({r['category']}) — {r['description']}",
            f"   - 期望 `{r['expect']}`, 实际 `{r['got']}` → {status}{fp_fn}",
        ]
        if r.get("block_reason"):
            short = r["block_reason"][:120].replace("\n", " ")
            lines.append(f"   - 拦截原因: {short}")
        lines.append("")

    if failed > 5:
        lines += [
            f"> **警告**: 失败用例 {failed} > 5,需要重点关注",
            "",
        ]

    return "\n".join(lines)


def main():
    cases_file = Path(sys.argv[1]) if len(sys.argv) > 1 else CASES_FILE

    print(f"[INFO] Loading cases from {cases_file}")
    cases = load_cases(cases_file)
    print(f"[INFO] {len(cases)} cases loaded")

    # Check mode
    mode = ensure_mode_active()
    if mode == "off":
        print(
            "[WARN] Mode file is 'off' — hook will exit 0 for all inputs. "
            "Tests may incorrectly pass. Continuing anyway.",
            file=sys.stderr,
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    t_start = time.monotonic()

    for i, case in enumerate(cases, 1):
        r = run_case(case)
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        got_label = r["got"]
        print(
            f"[{i:03d}/{len(cases)}] {status:4s} | {case['id']} | "
            f"expect={r['expect']:5s} got={got_label:5s} | {r['description'][:60]}"
        )

    total_ms = (time.monotonic() - t_start) * 1000
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    # Write JSON results
    output = {
        "meta": {
            "hook": "completion-check.py",
            "cases_file": str(cases_file),
            "total": total,
            "passed": passed,
            "failed": failed,
            "accuracy": round(passed / total * 100, 1) if total else 0,
            "total_ms": round(total_ms, 1),
            "run_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "results": results,
    }

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[INFO] JSON results written to {RESULTS_FILE}")

    # Write summary
    summary = build_summary(results, total_ms)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(summary)
    print(f"[INFO] Summary written to {SUMMARY_FILE}")

    # Print quick summary
    print(f"\n{'='*60}")
    print(f"TOTAL: {total}  PASS: {passed}  FAIL: {failed}  ({passed/total*100:.1f}%)")
    if failed > 5:
        print(f"WARNING: {failed} failures > threshold of 5")
    print(f"{'='*60}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

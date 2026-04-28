#!/usr/bin/env python3
"""
Test runner for runtime-guard.py hook.
Reads cases from runtime-guard-cases.jsonl, executes each case,
and produces a structured JSON results file plus a markdown summary.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
HOOK = Path.home() / "router-eval" / "hook" / "runtime-guard.py"
CASES = Path(__file__).parent / "cases" / "runtime-guard-cases.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_FILE = RESULTS_DIR / "runtime-guard.json"
SUMMARY_FILE = RESULTS_DIR / "runtime-guard-summary.md"

LOG_DIR = Path.home() / ".claude" / "router-logs"
FAILURE_LOG = LOG_DIR / "failure-streak.log"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def backup_failure_log():
    """Save original failure-streak.log so we can restore it after tests."""
    if FAILURE_LOG.exists():
        bak = FAILURE_LOG.with_suffix(".log.bak_test")
        shutil.copy2(FAILURE_LOG, bak)
        return bak
    return None


def restore_failure_log(bak):
    """Restore original failure-streak.log."""
    if bak and bak.exists():
        shutil.copy2(bak, FAILURE_LOG)
        bak.unlink()
    elif not bak:
        # Original didn't exist; remove anything we wrote
        if FAILURE_LOG.exists():
            FAILURE_LOG.unlink()


def write_streak(tool_name: str, count: int):
    """Write `count` consecutive failure entries for `tool_name` to failure-streak.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for i in range(count):
        entries.append(json.dumps({
            "ts": "2026-04-26T10:00:00",
            "tool_name": tool_name,
            "error": f"Simulated failure #{i+1}",
        }, ensure_ascii=False))
    FAILURE_LOG.write_text("\n".join(entries) + "\n", encoding="utf-8")


def clear_streak():
    """Remove failure-streak.log entries (reset state between cases)."""
    if FAILURE_LOG.exists():
        FAILURE_LOG.write_text("", encoding="utf-8")


def setup_plan(tmpdir: Path, plan_content: str):
    """Create .claude/active-plan.md inside tmpdir."""
    claude_dir = tmpdir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "active-plan.md").write_text(plan_content, encoding="utf-8")


def run_hook(payload: dict) -> tuple[str, int]:
    """Invoke runtime-guard.py with payload via stdin. Returns (stdout, returncode)."""
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip(), result.returncode


def decide(stdout: str) -> str:
    """Parse hook output: empty = allow, JSON with decision=block = block."""
    if not stdout:
        return "allow"
    try:
        data = json.loads(stdout)
        return data.get("decision", "allow")
    except Exception:
        return "allow"


# ─── Main runner ──────────────────────────────────────────────────────────────

def main():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cases = []
    with open(CASES, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    total = len(cases)
    passed = 0
    failed_cases = []
    by_category: dict[str, dict] = {}
    false_positives = []
    false_negatives = []

    # Backup real failure log
    original_bak = backup_failure_log()
    clear_streak()

    tmpdir = Path(tempfile.mkdtemp(prefix="runtime-guard-test-"))
    start_time = time.time()

    try:
        for case in cases:
            cid = case["id"]
            category = case["category"]
            expect = case["expect"]
            description = case["description"]
            payload = case["payload"].copy()

            # ── 1. Set up streak state ─────────────────────────────────────────
            streak_setup = case.get("_streak_setup")
            if streak_setup:
                write_streak(streak_setup["tool"], streak_setup["count"])
            else:
                clear_streak()

            # ── 2. Set up scope-creep plan file ───────────────────────────────
            plan_content = case.get("_setup_plan")
            case_tmpdir = tmpdir / cid
            case_tmpdir.mkdir(exist_ok=True)

            if plan_content:
                setup_plan(case_tmpdir, plan_content)
                # Inject cwd into payload so hook reads the right active-plan.md
                payload["cwd"] = str(case_tmpdir)
                # Make file_path absolute within case_tmpdir (cases use relative paths)
                ti = payload.get("tool_input", {})
                fp = ti.get("file_path", "")
                if fp:
                    if Path(fp).is_absolute():
                        # Strip any old prefix and re-root under case_tmpdir
                        # (case file_paths should be relative for scope_creep cases)
                        payload["tool_input"]["file_path"] = str(case_tmpdir / Path(fp).name)
                    else:
                        payload["tool_input"]["file_path"] = str(case_tmpdir / fp)
            else:
                # Use a neutral cwd with no active-plan.md
                if "cwd" not in payload:
                    payload["cwd"] = str(tmpdir)

            # ── 3. Run hook ────────────────────────────────────────────────────
            try:
                stdout, rc = run_hook(payload)
            except subprocess.TimeoutExpired:
                stdout = ""
                rc = 0

            actual = decide(stdout)
            passed_case = actual == expect

            # ── 4. Record results ──────────────────────────────────────────────
            if by_category.get(category) is None:
                by_category[category] = {"total": 0, "passed": 0}
            by_category[category]["total"] += 1

            if passed_case:
                passed += 1
                by_category[category]["passed"] += 1
            else:
                # Parse reason from hook output for diagnosis
                reason = ""
                try:
                    reason = json.loads(stdout).get("reason", "") if stdout else ""
                except Exception:
                    reason = stdout[:200]

                record = {
                    "id": cid,
                    "category": category,
                    "description": description,
                    "expect": expect,
                    "actual": actual,
                    "hook_stdout": stdout[:300],
                    "reason": reason[:200],
                }
                failed_cases.append(record)
                if expect == "block" and actual == "allow":
                    false_negatives.append(record)
                elif expect == "allow" and actual == "block":
                    false_positives.append(record)

    finally:
        # ── 5. Cleanup ─────────────────────────────────────────────────────────
        restore_failure_log(original_bak)
        shutil.rmtree(tmpdir, ignore_errors=True)

    duration = round(time.time() - start_time, 2)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    # ── 6. Compute metrics ────────────────────────────────────────────────────
    block_cases = [c for c in cases if c["expect"] == "block"]
    allow_cases = [c for c in cases if c["expect"] == "allow"]
    tp = len(block_cases) - len(false_negatives)
    tn = len(allow_cases) - len(false_positives)
    tpr = round(tp / len(block_cases), 4) if block_cases else 0.0
    tnr = round(tn / len(allow_cases), 4) if allow_cases else 0.0

    results = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "true_positive_rate": tpr,
        "true_negative_rate": tnr,
        "false_positive": false_positives,
        "false_negative": false_negatives,
        "by_category": by_category,
        "duration_seconds": duration,
        "timestamp": timestamp,
    }

    RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 7. Print summary ─────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  runtime-guard.py 测试结果")
    print(f"{'='*60}")
    print(f"  总计:       {total}")
    print(f"  通过:       {passed}")
    print(f"  失败:       {total - passed}")
    print(f"  真正率(TPR): {tpr:.1%}  (应拦截 → 实际拦截)")
    print(f"  真负率(TNR): {tnr:.1%}  (应放行 → 实际放行)")
    print(f"  耗时:       {duration}s")
    print(f"{'='*60}")

    if failed_cases:
        print(f"\n失败用例({len(failed_cases)}条):")
        for fc in failed_cases:
            tag = "漏拦" if fc["expect"] == "block" else "误拦"
            print(f"  [{tag}] {fc['id']} | {fc['description']}")
            if fc["reason"]:
                print(f"         hook说: {fc['reason'][:100]}")
    else:
        print("\n全部通过！")

    print(f"\n结果已保存: {RESULTS_FILE}")

    # ── 8. Write markdown summary ────────────────────────────────────────────
    write_markdown_summary(results, failed_cases, by_category)
    print(f"摘要已保存: {SUMMARY_FILE}")

    # Exit non-zero if too many failures (>5)
    if (total - passed) > 5:
        print(f"\n警告: 失败 {total-passed} 条 > 5 条阈值，需要修复再分享！", file=sys.stderr)
        sys.exit(1)


def write_markdown_summary(results, failed_cases, by_category):
    total = results["total"]
    passed = results["passed"]
    failed = results["failed"]
    tpr = results["true_positive_rate"]
    tnr = results["true_negative_rate"]
    duration = results["duration_seconds"]
    timestamp = results["timestamp"]
    fp_list = results["false_positive"]
    fn_list = results["false_negative"]

    lines = []
    lines.append("# runtime-guard.py 单元测试报告")
    lines.append("")
    lines.append(f"> 测试时间: {timestamp}  |  耗时: {duration}s")
    lines.append("")

    # ── Section 1: 总览 ───────────────────────────────────────────────────────
    lines.append("## 1. 总览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 总测试用例 | {total} |")
    lines.append(f"| 通过 | {passed} |")
    lines.append(f"| 失败 | {failed} |")
    lines.append(f"| 真正率 TPR（应拦 → 拦到）| {tpr:.1%} |")
    lines.append(f"| 真负率 TNR（应放 → 放行）| {tnr:.1%} |")
    lines.append(f"| 合格阈值 | 失败 ≤ 5 条 |")
    lines.append(f"| 是否合格 | {'✅ 合格' if failed <= 5 else '❌ 不合格，需修复'} |")
    lines.append("")

    # ── Section 2: 分类明细 ───────────────────────────────────────────────────
    lines.append("## 2. 分类明细")
    lines.append("")
    lines.append("| 分类 | 总数 | 通过 | 失败 | 通过率 |")
    lines.append("|------|------|------|------|--------|")
    category_labels = {
        "blacklist_1": "黑名单#1 数据丢失",
        "blacklist_2": "黑名单#2 真实人通讯",
        "scope_creep": "Scope Creep 检测",
        "failure_streak": "熔断检测",
        "allow_safe_bash": "放行-安全Bash命令",
        "allow_scope_in_range": "放行-Scope范围内文件",
        "allow_other_tools": "放行-其他工具",
        "allow_streak_reset": "放行-非跨tool熔断",
    }
    for cat, stats in sorted(by_category.items()):
        label = category_labels.get(cat, cat)
        t = stats["total"]
        p = stats["passed"]
        f = t - p
        rate = f"{p/t:.0%}" if t else "N/A"
        lines.append(f"| {label} | {t} | {p} | {f} | {rate} |")
    lines.append("")

    # ── Section 3: 失败用例分析 ───────────────────────────────────────────────
    lines.append("## 3. 失败用例详细分析")
    lines.append("")

    if not failed_cases:
        lines.append("**无失败用例。全部 100 条通过。**")
        lines.append("")
    else:
        if fn_list:
            lines.append(f"### 3.1 漏拦（False Negative）— {len(fn_list)} 条")
            lines.append("")
            lines.append("应该拦截但放行了：")
            lines.append("")
            for i, fc in enumerate(fn_list, 1):
                lines.append(f"#### FN-{i}: {fc['id']} — {fc['description']}")
                lines.append("")
                lines.append(f"- **分类**: {fc['category']}")
                lines.append(f"- **期望**: block")
                lines.append(f"- **实际**: allow（漏拦）")
                lines.append(f"- **Hook 输出**: `{fc['hook_stdout'][:150] or '(空)'}`")

                # Diagnosis by category
                cat = fc["category"]
                if cat == "blacklist_1":
                    lines.append(f"- **根因分析**: 黑名单正则未匹配。检查 DESTRUCTIVE_BASH 的 regex 是否覆盖此变体。")
                    lines.append(f"  - rm 系列: regex `\\brm\\s+-[rRfF]+\\b.*?(/|~|\\$HOME|\\*)` 要求路径含 `/~$HOME*`，若命令路径不含这些字符会漏拦。")
                    lines.append(f"  - DELETE: regex `DELETE FROM \\w+ (?!.*WHERE)` 使用负前瞻，多空格或换行可能绕过。")
                elif cat == "blacklist_2":
                    lines.append(f"- **根因分析**: EMAIL_API_PATTERNS 正则未匹配此变体。SMTP 匹配 `smtp\\.\\w+\\.\\w+`，若域名分段不足2级会漏。")
                    lines.append(f"  - Slack/飞书: 需要 `-X POST` 顺序匹配，若参数顺序不同会漏拦。")
                elif cat == "scope_creep":
                    lines.append(f"- **根因分析**: scope_creep_check 的路径匹配逻辑用 `pat in fp or pat in fp_rel`（子串包含），")
                    lines.append(f"  若 allowed pattern 是目录名而文件路径包含该子串可能误过。")
                elif cat == "failure_streak":
                    lines.append(f"- **根因分析**: failure_streak_check 读取 failure-streak.log 最后10行并逐行逆序统计，")
                    lines.append(f"  若日志格式不匹配或 tool_name 有差异会漏拦。")
                lines.append("")

        if fp_list:
            lines.append(f"### 3.2 误拦（False Positive）— {len(fp_list)} 条")
            lines.append("")
            lines.append("不应拦截但被拦了：")
            lines.append("")
            for i, fc in enumerate(fp_list, 1):
                lines.append(f"#### FP-{i}: {fc['id']} — {fc['description']}")
                lines.append("")
                lines.append(f"- **分类**: {fc['category']}")
                lines.append(f"- **期望**: allow")
                lines.append(f"- **实际**: block（误拦）")
                lines.append(f"- **Hook 理由**: `{fc['reason'][:200] or '(无)'}`")

                cat = fc["category"]
                if "bash" in cat.lower() or "safe" in cat.lower():
                    lines.append(f"- **根因分析**: 正则过宽，匹配了不应拦截的命令。")
                    lines.append(f"  - `rm -f` 单文件被 rm -rf 正则误拦：检查 regex 是否要求 `[rRfF]+` 同时有 r 和 f。")
                    lines.append(f"  - `DELETE WHERE` 被无 WHERE 正则误拦：负前瞻 `(?!.*WHERE)` 在多行或大小写变体时可能失效。")
                    lines.append(f"  - mailgun/sendgrid 出现在注释/echo 字符串中被误拦：regex 未区分实际调用与文本提及。")
                elif "scope" in cat.lower() or "range" in cat.lower():
                    lines.append(f"- **根因分析**: scope_creep_check 路径匹配用子串，allowed pattern 过短时可能误匹配。")
                lines.append("")

    # ── Section 4: 改进建议 ───────────────────────────────────────────────────
    lines.append("## 4. 改进建议")
    lines.append("")
    lines.append("根据测试结果，优先级排序：")
    lines.append("")

    suggestions = []

    if fn_list:
        # Analyze patterns of failures
        fn_cats = [fc["category"] for fc in fn_list]
        if "blacklist_1" in fn_cats:
            suggestions.append(("HIGH", "blacklist_1 漏拦",
                "rm regex 要求路径含 `/~$HOME*`，对 `rm -rf localdir/` 等本地目录无效。建议改为：`\\brm\\s+-[rRfF]*r[rRfF]*\\b`（只要有 r 即拦），或额外加一条不限路径的宽泛规则。"))
            suggestions.append(("HIGH", "DELETE 无 WHERE 漏拦",
                "负前瞻 `(?!.*WHERE)` 对多行 SQL 无效。建议改用 `re.DOTALL` 或先 strip 命令再匹配。"))
        if "blacklist_2" in fn_cats:
            suggestions.append(("HIGH", "真实人通讯漏拦",
                "Slack/飞书 regex 要求 `-X POST` 在 URL 前，实际命令中参数位置可变。建议分开匹配 URL 和 HTTP 方法，不绑定顺序。"))
        if "scope_creep" in fn_cats:
            suggestions.append(("MEDIUM", "Scope creep 漏拦",
                "路径匹配用 `pat in fp`（子串），allowed 若写 `src/` 会匹配所有含该子串的路径（含范围外文件）。建议改为 fnmatch 或 pathlib 前缀匹配。"))
        if "failure_streak" in fn_cats:
            suggestions.append(("MEDIUM", "熔断漏拦",
                "streak 计数逆序遍历直到 tool_name 不同就 break；若日志中穿插了其他 tool 的失败，会中断连续计数。建议改为：扫全部最近N条，只统计指定 tool 的连续尾段。"))

    if fp_list:
        fp_cats = [fc["category"] for fc in fp_list]
        if any("safe" in c or "allow" in c for c in fp_cats):
            suggestions.append(("HIGH", "误拦安全命令",
                "正则边界过宽导致 false positive。具体：`rm -f singlefile` 被 rm -rf regex 误拦、`DELETE WHERE` 被无 WHERE regex 误拦。需加严格边界条件。"))
            suggestions.append(("MEDIUM", "字符串提及被误拦",
                "mailgun/sendgrid 域名出现在 echo/注释中被误拦。建议增加上下文检查：只拦含 `curl`/`wget`/`requests.post` 等实际调用动词的命令。"))

    if not suggestions:
        suggestions.append(("INFO", "当前实现健壮",
            "100条测试全部通过，无需立即修改。可在实战中继续收集 edge case。"))

    for prio, title, desc in suggestions:
        lines.append(f"### [{prio}] {title}")
        lines.append("")
        lines.append(f"{desc}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*由 run-runtime-guard.py 自动生成 @ {timestamp}*")

    SUMMARY_FILE.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()

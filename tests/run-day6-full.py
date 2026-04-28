#!/usr/bin/env python3
"""
run-day6-full.py — 全量跑 75 case + 出 director-vs-v3.1 对照报告

输入:
  cases/real-world-replay.jsonl       (50)
  cases/p0-cases.jsonl                (13: 3 failure + 5 regression + 5 replay)
  cases/synthetic-triage.jsonl        (4)
  cases/synthetic-combo.jsonl         (3)
  cases/synthetic-edge.jsonl          (4)
  = 74 脚本可验 case

输出:
  results/day6-full.jsonl              (每 case 的 router decision + 验证)
  results/director-vs-v3.1.md          (7 验收数字汇总报告)
"""

import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "hook" / "router.py"
CASES_DIR = ROOT / "tests" / "cases"
RESULTS_DIR = ROOT / "tests" / "results"
RAW_RESULTS = RESULTS_DIR / "day6-full.jsonl"
REPORT = RESULTS_DIR / "director-vs-v3.1.md"

CASE_FILES = [
    ("real-world-replay.jsonl", "real-replay"),
    ("p0-cases.jsonl", "p0"),
    ("synthetic-triage.jsonl", "synth-triage"),
    ("synthetic-combo.jsonl", "synth-combo"),
    ("synthetic-edge.jsonl", "synth-edge"),
]


def setup_isolated_home():
    isolated = RESULTS_DIR / ".isolated-home"
    isolated.mkdir(parents=True, exist_ok=True)
    real_config = Path.home() / ".config"
    if not (isolated / ".config").exists():
        (isolated / ".config").symlink_to(real_config)
    real_claude = Path.home() / ".claude"
    if real_claude.exists():
        (isolated / ".claude").mkdir(exist_ok=True)
        for sub in ("plugins", "skills"):
            link = isolated / ".claude" / sub
            target = real_claude / sub
            if not link.exists() and target.exists():
                link.symlink_to(target)
    return isolated


def _call_once(prompt, isolated_home):
    payload = {"prompt": prompt, "session_id": "day6-test", "cwd": str(ROOT)}
    env = os.environ.copy()
    env["ROUTER_HOOK_MODE"] = "auto"
    env["HOME"] = str(isolated_home)
    log_path = isolated_home / ".claude" / "router-logs" / "router.log"
    if log_path.exists():
        log_path.unlink()
    t0 = time.perf_counter()
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
        return {"error": "timeout"}, "", 30.0
    elapsed = time.perf_counter() - t0
    decision = {}
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if line.strip():
                decision = json.loads(line).get("decision", {})
    return decision, proc.stdout, elapsed


def call_router(prompt, isolated_home, max_retry=2):
    """LLM API 偶发失败时 retry。429 等到下一轮自然延迟。"""
    decision, stdout, elapsed = _call_once(prompt, isolated_home)
    retries = 0
    total_elapsed = elapsed
    while retries < max_retry and (
        not decision.get("framework_primary") or decision.get("error")
    ):
        # rate limit 等更久,其他错误等 1s
        wait = 5.0 if decision.get("error") == "http_429" else 1.0
        time.sleep(wait)
        decision, stdout, elapsed = _call_once(prompt, isolated_home)
        total_elapsed += elapsed + wait
        retries += 1
    return decision, stdout, total_elapsed


def extract_expected(case):
    """统一不同 case 文件的 expected schema。返回 (fw, task_type, alternatives)。"""
    if "expected" in case:
        e = case["expected"]
        fw = e.get("framework")
        tt = e.get("task_type") or e.get("primary_task_type")
        alts = e.get("accepted_either") or ([fw] if fw else [])
        return fw, tt, alts
    if "expected_v3.1" in case:
        e = case["expected_v3.1"]
        return e.get("framework"), None, [e.get("framework")] if e.get("framework") else []
    if "framework" in case:
        return case["framework"], None, [case["framework"]]
    return None, None, []


def evaluate(case, decision):
    exp_fw, exp_tt, alts = extract_expected(case)
    act_fw = decision.get("framework_primary")
    act_tt = decision.get("task_type")

    fw_ok = None
    tt_ok = None

    if alts:
        fw_ok = act_fw in alts

    if exp_tt:
        tt_ok = act_tt == exp_tt

    # 综合 pass: fw 是必查;tt 若有期望也要查
    overall = fw_ok if tt_ok is None else (fw_ok and tt_ok)
    return {
        "expected_fw": exp_fw,
        "expected_tt": exp_tt,
        "alternatives": alts,
        "actual_fw": act_fw,
        "actual_tt": act_tt,
        "fw_ok": fw_ok,
        "tt_ok": tt_ok,
        "pass": overall,
    }


def detect_dispatch_mode(stdout):
    if not stdout.strip():
        return "none"
    if "[Director-Worker dispatch]" in stdout:
        return "dispatch"
    return "inject"


def main():
    isolated = setup_isolated_home()
    rows = []
    by_source = defaultdict(list)
    by_category = defaultdict(list)
    latencies = []
    dispatch_text_lens = []
    inject_text_lens = []

    print("=== 全量跑 ===")
    for fname, src_label in CASE_FILES:
        path = CASES_DIR / fname
        if not path.exists():
            print(f"  skip missing: {fname}")
            continue
        cases = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        print(f"\n--- {fname} ({len(cases)} cases) ---")
        for case in cases:
            cid = case.get("id", "?")
            prompt = case["prompt"]
            decision, stdout, elapsed = call_router(prompt, isolated)
            ev = evaluate(case, decision)
            mode = detect_dispatch_mode(stdout)

            row = {
                "id": cid,
                "source": src_label,
                "category": case.get("category", src_label),
                "prompt_preview": prompt[:80].replace("\n", " "),
                "actual_fw": ev["actual_fw"],
                "actual_tt": ev["actual_tt"],
                "expected_fw": ev["expected_fw"],
                "expected_tt": ev["expected_tt"],
                "alternatives": ev["alternatives"],
                "fw_ok": ev["fw_ok"],
                "tt_ok": ev["tt_ok"],
                "pass": ev["pass"],
                "dispatch_mode": mode,
                "stdout_len": len(stdout),
                "router_latency_s": elapsed,
            }
            rows.append(row)
            by_source[src_label].append(row)
            by_category[row["category"]].append(row)
            latencies.append(elapsed)

            if mode == "dispatch":
                dispatch_text_lens.append(len(stdout))
            elif mode == "inject":
                inject_text_lens.append(len(stdout))

            mark = "✅" if row["pass"] else ("❌" if row["pass"] is False else "—")
            print(f"  {mark} {cid:18} fw={row['actual_fw'] or '?':4} tt={row['actual_tt'] or '?':10} mode={mode:8} ({elapsed*1000:.0f}ms)")

    # 写 raw results
    RAW_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RAW_RESULTS.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 汇总数字
    total = len(rows)
    pass_count = sum(1 for r in rows if r["pass"])
    fail_count = sum(1 for r in rows if r["pass"] is False)
    skip_count = total - pass_count - fail_count

    triage_rows = [r for r in rows if r["source"] == "synth-triage"]
    triage_pass = sum(1 for r in triage_rows if r["pass"])

    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

    avg_dispatch = sum(dispatch_text_lens) / len(dispatch_text_lens) if dispatch_text_lens else 0
    avg_inject = sum(inject_text_lens) / len(inject_text_lens) if inject_text_lens else 0

    # 7 metrics scoring
    metric_1 = triage_pass / len(triage_rows) if triage_rows else 0
    metric_7_uninstall = 0.110  # from Day 5

    # 写报告
    report = []
    report.append("# Director-Worker vs v3.1 — 全量评估报告")
    report.append(f"\n**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"**case 总数**: {total} (脚本可验) + ② ③ ⑤ ⑥ 部分需 [你验]")
    report.append(f"**通过**: {pass_count}/{total} = {100*pass_count/total:.1f}%")
    report.append(f"**失败**: {fail_count} | **跳过/无 expected**: {skip_count}")

    report.append("\n## 分类通过率\n")
    report.append("| 来源 | 通过/总数 | 通过率 |")
    report.append("|---|---|---|")
    for src, label in [("real-replay", "真实日志 replay"), ("p0", "P0 关键 case"),
                       ("synth-triage", "① 任务分诊"), ("synth-combo", "⑩ 多意图"),
                       ("synth-edge", "⑪ 边缘格式")]:
        rs = by_source.get(src, [])
        if not rs:
            continue
        p = sum(1 for r in rs if r["pass"])
        report.append(f"| {label} | {p}/{len(rs)} | {100*p/len(rs):.0f}% |")

    report.append("\n## 7 验收数字\n")
    report.append("| # | 指标 | 目标 | 实际 | 状态 |")
    report.append("|---|---|---|---|---|")
    report.append(f"| 1 | 任务分诊准确率(75 case) | ≥ 90% | **{100*metric_1:.0f}%** ({triage_pass}/{len(triage_rows)}) | {'✅' if metric_1 >= 0.9 else '❌'} |")
    report.append("| 2 | 用户每周手动\"切到 X\"次数 | ≤ 2 次 | — | 🟡 [你验] 需运行 1 周观察 |")
    report.append("| 3 | Context bleed 跨 turn 事件 | 0 起/周 | — | 🟡 [你验] 需 chat 实测 |")
    report.append("| 4 | 重型 task latency | refactor<3min / research<5min | router 平均 {:.0f}ms / p95 {:.0f}ms | 🟡 sub-agent 真跑 latency 待 chat 测 |".format(avg_latency*1000, p95_latency*1000))
    report.append(f"| 5 | 月度 LLM 成本 vs v3.1 | < 1.5x | router 阶段 +0%(共用 LLM)| 🟡 sub-agent 真跑后才能算月度 |")
    report.append("| 6 | 三家特色保留度 | 100% | dispatch text 含 SKILL.md 路径 + 5-phase/forcing/MCP 提示 | 🟡 [你验] chat 实跑 SP/GS/ECC sub-agent |")
    report.append(f"| 7 | 回滚耗时 | < 10s | **{metric_7_uninstall*1000:.0f}ms** (Day 5 测) | ✅ |")

    report.append("\n## Context 节省估算\n")
    if avg_dispatch and avg_inject:
        delta = avg_dispatch - avg_inject
        report.append(f"- v3.1 inject 平均文本: **{avg_inject:.0f}** 字符")
        report.append(f"- Director-Worker dispatch 平均文本: **{avg_dispatch:.0f}** 字符 (Δ {delta:+.0f})")
        report.append("")
        report.append("> 注:dispatch text 比 inject 长一些(因为含 sub_agent_prompt + supervisor 协议),")
        report.append("> 但**真正的 context 节省**在于 sub-agent 跑掉的 SKILL.md / 中间步骤都不进主 session。")
        report.append("> 需要在 chat 里实跑才能测主 session token 占用降幅。")

    report.append("\n## 失败明细\n")
    fails = [r for r in rows if r["pass"] is False]
    if not fails:
        report.append("(无)")
    else:
        report.append("| ID | category | expected | actual | prompt |")
        report.append("|---|---|---|---|---|")
        for r in fails[:20]:  # cap at 20
            exp = f"{r['expected_fw'] or '?'}/{r['expected_tt'] or '?'}"
            act = f"{r['actual_fw'] or '?'}/{r['actual_tt'] or '?'}"
            report.append(f"| {r['id']} | {r['category']} | {exp} | {act} | {r['prompt_preview']}… |")
        if len(fails) > 20:
            report.append(f"\n_... 共 {len(fails)} 条失败,只列前 20_")

    report.append("\n## [你验] 留单(脚本不能验,必须 chat 里手动测)\n")
    report.append("- ② 认知负担:连续 3 turn 不同任务自动切;同主题深入不切")
    report.append("- ③ Context 隔离:SP 跑完后改 typo / 中途切话题,主 session token 不增")
    report.append("- ⑤ 性能成本:重型 task latency / 月 LLM 成本对比")
    report.append("- ⑥ 三家特色保留:SP 5-phase 完整 / gstack forcing question 完整 / ECC MCP 真调用")
    report.append("")
    report.append("**测法**:在 chat 主 session 实际发对应 prompt,看 sub-agent 是否真派 + 是否完整跑完。")

    report.append("\n## Day 7 决策点\n")
    overall_rate = pass_count / total if total else 0
    if overall_rate >= 0.9:
        verdict = "✅ **推全**(脚本通过率 ≥ 90%)— 进 Day 7 dashboard + 决策"
    elif overall_rate >= 0.7:
        verdict = "🟡 **部分启用**(70-90%)— 仅对高置信路径开 Director-Worker,低置信回退 inject"
    else:
        verdict = "❌ **回滚**(< 70%)— 跑 uninstall-director.sh"
    report.append(verdict)
    report.append(f"\n实际脚本通过率: **{100*overall_rate:.1f}%**")
    report.append("⚠️ 决策必须等 [你验] 项也过了才算最终判定 — 脚本通过率只覆盖 router/dispatch 层。")

    REPORT.write_text("\n".join(report), encoding="utf-8")
    print(f"\n=== Day 6 完成 ===")
    print(f"  总: {pass_count}/{total} = {100*pass_count/total:.1f}%")
    print(f"  ① 任务分诊: {triage_pass}/{len(triage_rows)}")
    print(f"  router 平均延迟: {avg_latency*1000:.0f}ms")
    print(f"  报告: {REPORT}")


if __name__ == "__main__":
    main()

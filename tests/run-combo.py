#!/usr/bin/env python3
"""
Combo-cases runner: tests prompts that should trigger 2-3 skills simultaneously.
Designed to probe router's single-choice behavior on multi-intent prompts.

Usage:
    python3 run-combo.py [--verbose]

Output:
    tests/results/combo-cases.json
    tests/results/combo-summary.md
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
CASES_FILE = HERE / "cases" / "combo-cases.jsonl"
RESULTS_DIR = HERE / "results"
RESULTS_FILE = RESULTS_DIR / "combo-cases.json"
SUMMARY_FILE = RESULTS_DIR / "combo-summary.md"

ROUTER_PY = HOOK_DIR / "router.py"
SESSION_ID = "combo-test-session"
CWD = str(Path.home() / "router-eval-share")
TIMEOUT = 25


def load_cases():
    cases = []
    with open(CASES_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def call_router(prompt: str) -> dict:
    payload = json.dumps({"prompt": prompt, "session_id": SESSION_ID, "cwd": CWD},
                          ensure_ascii=False).encode("utf-8")
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(ROUTER_PY)],
            input=payload,
            capture_output=True,
            timeout=TIMEOUT,
        )
        latency = int((time.time() - t0) * 1000)
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace").strip(),
            "stderr": proc.stderr.decode("utf-8", errors="replace").strip(),
            "latency_ms": latency,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": "TIMEOUT",
                "latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"exit_code": -2, "stdout": "", "stderr": str(e),
                "latency_ms": int((time.time() - t0) * 1000)}


def parse_router_output(stdout: str) -> dict:
    """Extract structured info from router injection text."""
    if not stdout:
        return {
            "routed_to": "CC (silent/fast-path)",
            "framework": "CC",
            "has_action_required": False,
            "human_confirm": False,
            "offline_topic": False,
            "confidence": None,
            "fallback": None,
            "reason": "",
        }

    # Extract target from "🧭 Router → X (conf Y)"
    m = re.search(r"🧭 Router → ([\w\-\(\) ]+?)\s+\(conf ([\d.]+)\)", stdout)
    routed_to = m.group(1).strip() if m else "unknown"
    confidence = float(m.group(2)) if m else None

    # Infer framework
    if "Superpowers" in routed_to or "SP" in routed_to:
        framework = "SP"
    elif "gstack" in routed_to or "GS" in routed_to:
        framework = "GS"
        # extract role
    elif "ECC" in routed_to:
        framework = "ECC"
    elif "CC" in routed_to or "原生" in routed_to:
        framework = "CC"
    else:
        framework = "unknown"

    # Fallback line
    fallback_m = re.search(r"fallback:\s*(\S+)", stdout)
    fallback = fallback_m.group(1) if fallback_m else None

    # Reason line
    reason_m = re.search(r"reason:\s*(.+)", stdout)
    reason = reason_m.group(1).strip() if reason_m else ""

    return {
        "routed_to": routed_to,
        "framework": framework,
        "has_action_required": "[ACTION REQUIRED]" in stdout,
        "human_confirm": "不可逆" in stdout or "人工确认" in stdout or "human_confirm" in stdout.lower(),
        "offline_topic": "OFFLINE_TOPIC" in stdout or "offline" in stdout.lower(),
        "confidence": confidence,
        "fallback": fallback,
        "reason": reason,
    }


def evaluate(case: dict, parsed: dict) -> dict:
    """Return evaluation: winner_match, signal_loss_count, notes."""
    expected_winner = case.get("expected_winner", "")
    routed = parsed["routed_to"]
    framework = parsed["framework"]
    human_confirm = parsed["human_confirm"]
    expected_skills = case.get("expected_skills", [])
    case_type = case.get("type", "?")

    # Check if winner matches
    winner_match = False
    if "SP" in expected_winner:
        winner_match = framework == "SP"
    elif "GS-CEO" in expected_winner:
        winner_match = "CEO" in routed
    elif "GS-EngManager" in expected_winner:
        winner_match = "EngManager" in routed
    elif "GS-QA" in expected_winner:
        winner_match = "QA" in routed
    elif "GS-DocEngineer" in expected_winner:
        winner_match = "DocEngineer" in routed
    elif "ECC-debug" in expected_winner or "ECC-research" in expected_winner:
        winner_match = framework == "ECC"
    elif "ECC-security" in expected_winner:
        winner_match = framework == "ECC" and ("security" in routed.lower() or human_confirm)
    elif "ECC-database" in expected_winner:
        winner_match = framework == "ECC"
    elif "CC" in expected_winner:
        winner_match = framework == "CC"
    elif "BLOCK" in expected_winner or "human_confirm" in expected_winner.lower():
        winner_match = human_confirm or parsed["human_confirm"]
    elif "GS" in expected_winner:
        winner_match = framework == "GS"

    # Signal loss: skills that should have been captured but weren't
    signal_loss = len(expected_skills) - 1  # router can only pick 1

    # Assess quality
    if case_type == "E":
        # Type E: most important is human_confirm
        quality = "good" if human_confirm else ("partial" if winner_match else "miss")
    elif case_type in ("A", "B", "D", "F", "G", "H"):
        quality = "good" if winner_match else "miss"
    elif case_type == "C":
        # Type C: offline mix — want engineering to win, offline suppressed
        quality = "good" if (winner_match and not parsed["offline_topic"]) else (
            "partial" if winner_match else "miss"
        )
    else:
        quality = "good" if winner_match else "miss"

    notes = []
    if not winner_match:
        notes.append(f"Expected {expected_winner}, got {routed}")
    if signal_loss > 1:
        notes.append(f"{signal_loss} secondary intents dropped (single-choice loss)")
    if case_type == "A" and len(expected_skills) >= 2:
        notes.append("Cross-framework: router physically cannot activate both")
    if case_type == "D":
        notes.append(f"Multi-role request: {len(expected_skills)} roles requested, router picks 1")

    return {
        "winner_match": winner_match,
        "signal_loss_count": signal_loss,
        "quality": quality,
        "notes": notes,
    }


def run_case(case: dict, verbose: bool) -> dict:
    cid = case["id"]
    prompt = case["prompt"]

    raw = call_router(prompt)
    parsed = parse_router_output(raw["stdout"])
    eval_result = evaluate(case, parsed)

    result = {
        "id": cid,
        "type": case.get("type", "?"),
        "prompt": prompt,
        "expected_skills": case.get("expected_skills", []),
        "expected_winner": case.get("expected_winner", ""),
        "rationale": case.get("rationale", ""),
        "router_output": {
            "routed_to": parsed["routed_to"],
            "framework": parsed["framework"],
            "has_action_required": parsed["has_action_required"],
            "human_confirm": parsed["human_confirm"],
            "offline_topic": parsed["offline_topic"],
            "confidence": parsed["confidence"],
            "fallback": parsed["fallback"],
            "reason": parsed["reason"],
            "latency_ms": raw["latency_ms"],
        },
        "eval": eval_result,
    }

    status_icon = {"good": "✅", "partial": "⚠️", "miss": "❌"}.get(eval_result["quality"], "?")
    print(f"  {status_icon} [{cid}] ({case.get('type')}) → {parsed['routed_to']} "
          f"({'win' if eval_result['winner_match'] else 'MISS'}) | {raw['latency_ms']}ms")
    if verbose and eval_result["notes"]:
        for n in eval_result["notes"]:
            print(f"      note: {n}")

    return result


def generate_summary(results: list) -> str:
    total = len(results)
    good = sum(1 for r in results if r["eval"]["quality"] == "good")
    partial = sum(1 for r in results if r["eval"]["quality"] == "partial")
    miss = sum(1 for r in results if r["eval"]["quality"] == "miss")
    winner_match_count = sum(1 for r in results if r["eval"]["winner_match"])

    # Group by type
    by_type = {}
    for r in results:
        t = r.get("type", "?")
        by_type.setdefault(t, []).append(r)

    lines = []
    lines.append("# Combo Cases — Router 边界 Case 测试报告")
    lines.append("")
    lines.append(f"**测试时间**: {time.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**总 Case 数**: {total}")
    lines.append(f"**Winner 选对**: {winner_match_count}/{total} ({winner_match_count/total*100:.0f}%)")
    lines.append(f"**质量分布**: ✅ 好 {good} | ⚠️ 部分 {partial} | ❌ 漏/错 {miss}")
    lines.append("")

    # Type summary table
    lines.append("## 分类汇总")
    lines.append("")
    lines.append("| 类型 | 描述 | Case数 | Winner准 | 质量分布 |")
    lines.append("|------|------|--------|----------|---------|")
    type_desc = {
        "A": "跨 framework 同时命中",
        "B": "同 framework 内子能力冲突",
        "C": "离线 vs 工程混合",
        "D": "多角色评审请求",
        "E": "显式黑名单冲突",
        "F": "顺序合理双阶段",
        "G": "强弱信号竞争",
        "H": "全流程多阶段",
    }
    for t, cases in sorted(by_type.items()):
        wm = sum(1 for c in cases if c["eval"]["winner_match"])
        good_t = sum(1 for c in cases if c["eval"]["quality"] == "good")
        partial_t = sum(1 for c in cases if c["eval"]["quality"] == "partial")
        miss_t = sum(1 for c in cases if c["eval"]["quality"] == "miss")
        lines.append(f"| {t} | {type_desc.get(t, '?')} | {len(cases)} | {wm}/{len(cases)} | "
                     f"✅{good_t} ⚠️{partial_t} ❌{miss_t} |")
    lines.append("")

    # Detailed cases
    lines.append("## 详细结果")
    lines.append("")
    for r in results:
        q = r["eval"]["quality"]
        icon = {"good": "✅", "partial": "⚠️", "miss": "❌"}.get(q, "?")
        lines.append(f"### {icon} [{r['id']}] (类型{r['type']}) — {q.upper()}")
        lines.append("")
        lines.append(f"**Prompt**: `{r['prompt']}`")
        lines.append("")
        lines.append(f"**预期触发技能**: {' + '.join(r['expected_skills'])}")
        lines.append(f"**预期 winner**: `{r['expected_winner']}`")
        lines.append(f"**实际路由**: `{r['router_output']['routed_to']}` "
                     f"(conf {r['router_output']['confidence']}) [{r['router_output']['latency_ms']}ms]")
        if r['router_output']['fallback']:
            lines.append(f"**fallback**: `{r['router_output']['fallback']}`")
        if r['router_output']['reason']:
            lines.append(f"**router reason**: {r['router_output']['reason']}")
        lines.append(f"**Winner 准确**: {'✅ 是' if r['eval']['winner_match'] else '❌ 否'}")
        lines.append(f"**信号丢失数**: {r['eval']['signal_loss_count']} 个次要意图被丢弃")
        lines.append(f"**评估说明**: {r['rationale']}")
        if r["eval"]["notes"]:
            for n in r["eval"]["notes"]:
                lines.append(f"- {n}")
        lines.append("")

    # === Open Questions Section (the main deliverable) ===
    lines.append("---")
    lines.append("")
    lines.append("## 开放问题 (分享会亮点)")
    lines.append("")
    lines.append("_以下 7 个问题是本次测试最核心的发现,每个都是 router v3 当前**没有**解决的设计空白。_")
    lines.append("")

    # Compute stats for open questions
    type_A_misses = [r for r in results if r["type"] == "A" and not r["eval"]["winner_match"]]
    type_D_cases = [r for r in results if r["type"] == "D"]
    type_E_cases = [r for r in results if r["type"] == "E"]
    type_E_correct = [r for r in type_E_cases if r["eval"].get("winner_confirm_ok", r["eval"]["winner_match"])]
    type_C_cases = [r for r in results if r["type"] == "C"]
    type_H_cases = [r for r in results if r["type"] == "H"]

    # Recount for accurate stats
    e_human_confirm = sum(1 for r in type_E_cases if r["router_output"]["human_confirm"])
    d_multi_role_captured = 0  # always 0 — router is single-choice

    lines.append("### Q1: Router 物理上能同 turn 触发 2 个 skill 吗?")
    lines.append("")
    cross_fw = [r for r in results if r["type"] == "A"]
    lines.append(f"**现状**: 测试了 {len(cross_fw)} 个跨 framework 组合 case。"
                 f"Router 输出是单选 JSON(`framework_primary`),"
                 f"CC 的 `[ACTION REQUIRED]` 也只能触发 1 个 Skill。"
                 f"从代码层看,`action_block()` 函数返回单行文字,Claude 读到后调用 1 个 Skill。")
    lines.append(f"**数据**: {len(cross_fw)} 个 A 类 case 中每个都丢失了至少 1 个次要技能信号。")
    lines.append(f"**候选解法**:")
    lines.append(f"- 方案 A: `[ACTION REQUIRED]` 输出两行,激活两个 Skill(需验证 CC 是否按顺序执行)")
    lines.append(f"- 方案 B: 主 skill 内部感知 secondary intent,自行决定是否子调用")
    lines.append(f"- 方案 C: Router 输出 `secondary_skills` 数组,让 CC prompt 层串行激活")
    lines.append(f"**谁可以做**: 改 `action_block()` 函数 + 实测 CC 的 multi-Skill turn 行为")
    lines.append("")

    lines.append("### Q2: 同 framework 内子能力冲突怎么选? (B 类)")
    lines.append("")
    type_B = [r for r in results if r["type"] == "B"]
    b_miss = [r for r in type_B if not r["eval"]["winner_match"]]
    lines.append(f"**现状**: {len(type_B)} 个 B 类 case(同 ECC 或同 GS 内两个子能力冲突)。")
    lines.append(f"Router winner 准确率: {len(type_B)-len(b_miss)}/{len(type_B)}。")
    lines.append(f"**核心问题**: ECC-debug vs ECC-security 哪个优先?"
                 f"当前 L0 规定含明文密钥走 security,但不含明文密钥时 debug vs security 没有明确规则。")
    lines.append(f"**候选解法**:")
    lines.append(f"- 给 subskill 加优先级顺序: security > database > debug > research")
    lines.append(f"- 或在 router schema 增加 `ecc_secondary_subskill` 字段")
    lines.append(f"**谁可以做**: 修改 `ROUTER_SYSTEM` prompt 的 ECC 分支规则")
    lines.append("")

    lines.append("### Q3: 多角色评审 (D 类) 如何处理? gstack 能并发激活多角色吗?")
    lines.append("")
    lines.append(f"**现状**: {len(type_D_cases)} 个 D 类 case 全部是「显式多角色」请求。"
                 f"Router 选 1 个主导角色,其余角色信号丢失。")
    lines.append(f"**核心问题**: gstack 的 CEO/EngManager/QA 是独立 Skill,没有\"多角色同时激活\"的机制。")
    lines.append(f"**候选解法**:")
    lines.append(f"- gstack 内部增加「评审委员会」模式,一个 skill 内部顺序扮演多角色")
    lines.append(f"- Router 输出 `gs_secondary_roles` 数组")
    lines.append(f"- 用户显式说「三角色」时切换到 multi-agent 模式")
    lines.append(f"**谁可以做**: gstack skill 开发者 + router schema 扩展")
    lines.append("")

    lines.append("### Q4: 黑名单 (E 类) 和正常工程任务混在一条 prompt 里,router 怎么处理?")
    lines.append("")
    lines.append(f"**现状**: {len(type_E_cases)} 个 E 类 case。"
                 f"Router L0 规则: 含 rm-rf/DROP/push-force → human_confirm=true。"
                 f"实际 human_confirm 触发: {e_human_confirm}/{len(type_E_cases)} 个。")
    lines.append(f"**核心问题**: 当 prompt 包含「rm -rf xxx AND 重构代码」时,"
                 f"router 选的 framework 是什么?理想是:选 SP 执行重构 + 对 rm-rf 触发 human_confirm。"
                 f"实际行为:router 可能因 rm-rf 信号主导而路由到错误 framework,或 human_confirm 未触发。")
    lines.append(f"**候选解法**:")
    lines.append(f"- L0 规则改为:任何 prompt 含黑名单词,无论 framework 如何,强制 human_confirm=true")
    lines.append(f"- 增加「操作分解」:router 识别 prompt 内多个独立操作,分别评估")
    lines.append(f"**谁可以做**: 修改 L0 hard override 逻辑")
    lines.append("")

    lines.append("### Q5: 离线话题 + 工程问题混搭 (C 类) — offline bypass 会不会误杀工程信号?")
    lines.append("")
    lines.append(f"**现状**: {len(type_C_cases)} 个 C 类 case(工程 + offline_topic 混搭)。")
    lines.append(f"**核心问题**: 如果 router 识别到 offline_topic 就走 CC bypass,"
                 f"工程部分的信号就完全丢失。理想是:offline 部分走 CC 处理,工程部分走对应 framework。")
    lines.append(f"**候选解法**:")
    lines.append(f"- Prompt 拆分:先拆 offline 和 engineering 两段,分别路由")
    lines.append(f"- 优先级: engineering > offline_topic(当 prompt 同时含两者时)")
    lines.append(f"- 返回两个 action:一个 framework injection + 一个 offline 提示")
    lines.append(f"**谁可以做**: Router L0.5 OFFLINE_TOPIC 逻辑 + prompt 分割预处理")
    lines.append("")

    lines.append("### Q6: 全流程多阶段请求 (H 类) — router 只处理「入口」还是「全程」?")
    lines.append("")
    lines.append(f"**现状**: {len(type_H_cases)} 个 H 类 case(调研→设计→实现→测试→部署全流程)。"
                 f"Router 在 UserPromptSubmit 时只处理第一个 prompt,后续步骤用原来的 framework。")
    lines.append(f"**核心问题**: 全流程任务应该在每个阶段切换 framework,"
                 f"但 router 只在 turn 入口路由一次,后续阶段的 framework 变化无法自动感知。")
    lines.append(f"**候选解法**:")
    lines.append(f"- active-plan.md 中每个 step 带 framework 标注")
    lines.append(f"- 每个 step 完成时触发「step-level router」再决策")
    lines.append(f"- 或全流程统一走 SP(5-phase),让 SP 内部管理阶段切换")
    lines.append(f"**谁可以做**: active-plan.md 格式扩展 + SP skill 内部逻辑")
    lines.append("")

    lines.append("### Q7: Confidence 在多意图 case 下是否应该系统性降低?")
    lines.append("")
    conf_multi = [r for r in results
                  if len(r["expected_skills"]) >= 2
                  and r["router_output"]["confidence"] is not None
                  and r["router_output"]["confidence"] >= 0.75]
    lines.append(f"**现状**: {len(conf_multi)} 个多意图 case 中 router confidence ≥ 0.75,"
                 f"但这些 case 实际上是「强行单选」,应该低确定性。")
    lines.append(f"**核心问题**: Router confidence 校准规则面向的是「单意图」场景。"
                 f"多意图 prompt 的 confidence 不应该高,因为 router 知道自己在做有损选择。")
    lines.append(f"**候选解法**:")
    lines.append(f"- 增加校准规则: prompt 含多个动词 intent 信号 → conf 降至 ≤ 0.70")
    lines.append(f"- 或增加 `multi_intent_detected: true` 字段,供 Claude 侧感知")
    lines.append(f"**谁可以做**: ROUTER_SYSTEM prompt confidence 校准段 + schema 扩展")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 最值得现场讨论的 3 个 Case")
    lines.append("")

    # Find most interesting cases
    interesting = []
    # Case E01: blacklist + normal task
    e01 = next((r for r in results if r["id"] == "combo_E01"), None)
    if e01:
        interesting.append(e01)
    # Case A06: feat + research (order matters)
    a06 = next((r for r in results if r["id"] == "combo_A06"), None)
    if a06:
        interesting.append(a06)
    # Case D01: three roles
    d01 = next((r for r in results if r["id"] == "combo_D01"), None)
    if d01:
        interesting.append(d01)

    for i, r in enumerate(interesting, 1):
        q = r["eval"]["quality"]
        icon = {"good": "✅", "partial": "⚠️", "miss": "❌"}.get(q, "?")
        lines.append(f"### 现场 Case {i}: {icon} [{r['id']}]")
        lines.append(f"**Prompt**: `{r['prompt']}`")
        lines.append(f"**预期**: {' + '.join(r['expected_skills'])}")
        lines.append(f"**实际**: `{r['router_output']['routed_to']}` (conf {r['router_output']['confidence']})")
        lines.append(f"**为什么有趣**: {r['rationale']}")
        if r["router_output"]["reason"]:
            lines.append(f"**Router 自述理由**: {r['router_output']['reason']}")
        lines.append(f"**Discussion**: {'; '.join(r['eval']['notes']) if r['eval']['notes'] else '无额外 notes'}")
        lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not ROUTER_PY.exists():
        print(f"ERROR: Router not found at {ROUTER_PY}", file=sys.stderr)
        sys.exit(1)

    cases = load_cases()
    print(f"Running {len(cases)} combo cases against router...")
    print(f"Router: {ROUTER_PY}")
    print()

    # Force auto mode so router always outputs
    mode_file = Path.home() / ".config" / "router-hook" / "mode"
    original_mode = None
    try:
        if mode_file.exists():
            original_mode = mode_file.read_text().strip()
        mode_file.parent.mkdir(parents=True, exist_ok=True)
        mode_file.write_text("auto")
    except Exception as e:
        print(f"WARNING: Could not set router mode: {e}")

    results = []
    try:
        for case in cases:
            r = run_case(case, verbose=args.verbose)
            results.append(r)
    finally:
        try:
            if original_mode is not None:
                mode_file.write_text(original_mode)
            elif mode_file.exists():
                mode_file.write_text("silent")
        except Exception:
            pass

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Write JSON results
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to: {RESULTS_FILE}")

    # Write summary
    summary = generate_summary(results)
    SUMMARY_FILE.write_text(summary)
    print(f"Summary written to: {SUMMARY_FILE}")

    # Print quick stats
    good = sum(1 for r in results if r["eval"]["quality"] == "good")
    partial = sum(1 for r in results if r["eval"]["quality"] == "partial")
    miss = sum(1 for r in results if r["eval"]["quality"] == "miss")
    wm = sum(1 for r in results if r["eval"]["winner_match"])
    print(f"\n{'='*50}")
    print(f"Total: {len(results)}  Winner准: {wm}  ✅{good} ⚠️{partial} ❌{miss}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

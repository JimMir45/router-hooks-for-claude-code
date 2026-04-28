#!/usr/bin/env python3
"""
run-dispatch-test.py — Day 3 SP wrapper 验收

脚本可验:
  1. SP case 注入文本是 dispatch 格式(含 [Director-Worker dispatch] + sub_agent_prompt)
  2. 引用的 SKILL.md 路径真存在
  3. 非 SP / simple case 走 inject 模式(fallback 行为)
  4. dispatch 文本含 model=sonnet + subagent_type=general-purpose

不可脚本验(留 [你验]):
  ⑥.1 SP 5-phase 完整跑完 — 要在 chat 里实际派 sub-agent 看
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "hook" / "router.py"
RESULTS = ROOT / "tests" / "results" / "dispatch-test.jsonl"
SUMMARY = ROOT / "tests" / "results" / "dispatch-test-summary.md"

# 端到端 case:验证 router → dispatch_subagent → 注入文本全链路
CASES = [
    # SP execution → 应走 dispatch (Day 3)
    {"id": "e2e_sp_tdd", "expect_mode": "dispatch", "expect_skill": "test-driven-development",
     "prompt": "用 TDD 实现一个 LRU 缓存,先写测试再写实现"},
    # Day 4: GS decision → 应走 dispatch
    {"id": "e2e_gs_dispatch", "expect_mode": "dispatch",
     "prompt": "我们的 RAG metadata 用 Postgres 还是 MongoDB,要支持复杂 filter"},
    # Day 4: ECC research → 应走 dispatch
    {"id": "e2e_ecc_dispatch", "expect_mode": "dispatch",
     "prompt": "调研一下 PostgreSQL 16 的最新进展和我们慢查询能不能优化"},
]

# 单元测试:dispatch_subagent 内部信号选择(不走 router LLM,直接构造 decision)
UNIT_CASES = [
    # SP sub-skill 选择
    {"id": "unit_sp_tdd", "prompt": "用 TDD 实现 LRU", "decision": {"framework_primary": "SP", "task_type": "execution"}, "expect_skill": "test-driven-development"},
    {"id": "unit_sp_brainstorm", "prompt": "我想做个 AI 写作助手,先头脑风暴一下", "decision": {"framework_primary": "SP", "task_type": "execution"}, "expect_skill": "brainstorming"},
    {"id": "unit_sp_debug", "prompt": "API 一直报 500 错误,debug 找根因", "decision": {"framework_primary": "SP", "task_type": "execution"}, "expect_skill": "systematic-debugging"},
    {"id": "unit_sp_plan", "prompt": "帮我 plan 一下这个新功能怎么拆", "decision": {"framework_primary": "SP", "task_type": "execution"}, "expect_skill": "writing-plans"},
    # Day 4: GS 角色 → skill 映射
    {"id": "unit_gs_engmgr", "prompt": "评审这个架构方案", "decision": {"framework_primary": "GS", "task_type": "decision", "gs_role": "EngManager"}, "expect_skill": "plan-eng-review"},
    {"id": "unit_gs_ceo", "prompt": "这个产品该不该做", "decision": {"framework_primary": "GS", "task_type": "decision", "gs_role": "CEO"}, "expect_skill": "plan-ceo-review"},
    {"id": "unit_gs_designer", "prompt": "这个 UI 怎么设计", "decision": {"framework_primary": "GS", "task_type": "decision", "gs_role": "Designer"}, "expect_skill": "design-consultation"},
    {"id": "unit_gs_default", "prompt": "讨论一下 idea", "decision": {"framework_primary": "GS", "task_type": "decision", "gs_role": None}, "expect_skill": "office-hours"},
    # Day 4: ECC sub-skill → skill 映射
    {"id": "unit_ecc_research", "prompt": "调研 vector DB", "decision": {"framework_primary": "ECC", "task_type": "domain", "ecc_subskill": "research"}, "expect_skill": "deep-research"},
    {"id": "unit_ecc_debug", "prompt": "为啥 API 慢", "decision": {"framework_primary": "ECC", "task_type": "domain", "ecc_subskill": "debug"}, "expect_skill": "investigate"},
    {"id": "unit_ecc_database", "prompt": "加个索引", "decision": {"framework_primary": "ECC", "task_type": "domain", "ecc_subskill": "database"}, "expect_skill": "database-migrations"},
    {"id": "unit_ecc_memory_skip", "prompt": "记下来", "decision": {"framework_primary": "ECC", "task_type": "domain", "ecc_subskill": "memory"}, "expect_mode": "inject"},
    # fallback / simple
    {"id": "unit_fallback_simple", "prompt": "fix typo", "decision": {"framework_primary": "CC", "task_type": "simple"}, "expect_mode": "none"},
]


def call_router(prompt: str) -> tuple[dict, str]:
    """返回 (decision, stdout_text)"""
    payload = {"prompt": prompt, "session_id": "dispatch-test", "cwd": str(ROOT)}
    env = os.environ.copy()
    env["ROUTER_HOOK_MODE"] = "auto"
    isolated_home = ROOT / "tests" / "results" / ".isolated-home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    real_config = Path.home() / ".config"
    isolated_config = isolated_home / ".config"
    if not isolated_config.exists():
        isolated_config.symlink_to(real_config)
    # dispatch_subagent 需访问真实 skill 缓存。只软链需要的子目录,log 路径 router.py 自己建
    real_claude = Path.home() / ".claude"
    if real_claude.exists():
        (isolated_home / ".claude").mkdir(exist_ok=True)
        for sub in ("plugins", "skills"):
            link = isolated_home / ".claude" / sub
            target = real_claude / sub
            if not link.exists() and target.exists():
                link.symlink_to(target)
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
        return {"error": "timeout"}, ""

    decision = {}
    if log_path.exists():
        for line in log_path.read_text().splitlines():
            if line.strip():
                decision = json.loads(line).get("decision", {})

    return decision, proc.stdout


def detect_mode(stdout: str) -> str:
    if not stdout.strip():
        return "none"
    if "[Director-Worker dispatch]" in stdout and "--- sub_agent_prompt ---" in stdout:
        return "dispatch"
    if "🧭 Router →" in stdout:
        return "inject"
    return "unknown"


def evaluate(case: dict, decision: dict, stdout: str) -> dict:
    actual_mode = detect_mode(stdout)
    expected_mode = case["expect_mode"]
    checks = []
    overall_pass = True

    # 1. mode 对吗
    mode_ok = actual_mode == expected_mode
    checks.append({"check": "mode", "expected": expected_mode, "actual": actual_mode, "pass": mode_ok})
    if not mode_ok:
        overall_pass = False

    # 2. dispatch 模式特殊验:skill 名 + 路径存在 + Agent 工具参数
    if expected_mode == "dispatch":
        # skill 名
        if "expect_skill" in case:
            skill_match = re.search(r"skill=([\w-]+)", stdout)
            actual_skill = skill_match.group(1) if skill_match else None
            sk_ok = actual_skill == case["expect_skill"]
            checks.append({"check": "skill", "expected": case["expect_skill"], "actual": actual_skill, "pass": sk_ok})
            if not sk_ok:
                overall_pass = False

        # SKILL.md 路径存在(prompt 里 `1. Read <path>` 或老格式 `Read the workflow at: <path>`)
        path_match = re.search(r"Read (?:the workflow at: )?(\S+SKILL\.md)", stdout)
        if path_match:
            skill_path = Path(path_match.group(1))
            path_ok = skill_path.exists()
            checks.append({"check": "skill_path_exists", "path": str(skill_path), "pass": path_ok})
            if not path_ok:
                overall_pass = False
        else:
            checks.append({"check": "skill_path_present", "pass": False})
            overall_pass = False

        # Agent 工具参数
        for required in ["subagent_type: general-purpose", "model: sonnet"]:
            ok = required in stdout
            checks.append({"check": f"contains:{required}", "pass": ok})
            if not ok:
                overall_pass = False

    return {"pass": overall_pass, "checks": checks, "actual_mode": actual_mode}


def run_unit_tests():
    """直接调 dispatch_subagent.build_dispatch_instruction,不走 router LLM。"""
    sys.path.insert(0, str(ROOT / "hook"))
    from dispatch_subagent import build_dispatch_instruction

    rows = []
    print("\n--- 单元测试 (dispatch_subagent 内部信号选择) ---")
    for case in UNIT_CASES:
        result = build_dispatch_instruction(case["prompt"], case["decision"])
        row = {"case": case, "result": result}
        if "expect_skill" in case:
            actual_skill = None
            if result["mode"] == "dispatch":
                import re as _re
                m = _re.search(r"skill=([\w-]+)", result["text"])
                actual_skill = m.group(1) if m else None
            ok = actual_skill == case["expect_skill"]
            row["pass"] = ok
            print(f"  {case['id']:24} {'✅' if ok else '❌'} expect_skill={case['expect_skill']:25} actual={actual_skill}")
        elif "expect_mode" in case:
            ok = result["mode"] == case["expect_mode"]
            row["pass"] = ok
            print(f"  {case['id']:24} {'✅' if ok else '❌'} expect_mode={case['expect_mode']:8} actual={result['mode']}")
        rows.append(row)
    return rows


def main():
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    pass_count = 0

    print("--- 端到端测试 (router → dispatch_subagent) ---")
    with RESULTS.open("w") as f:
        for i, case in enumerate(CASES, 1):
            print(f"  [{i}/{len(CASES)}] {case['id']:18} ", end="", flush=True)
            decision, stdout = call_router(case["prompt"])
            ev = evaluate(case, decision, stdout)
            row = {
                "case": case,
                "decision_fw": decision.get("framework_primary"),
                "decision_task_type": decision.get("task_type"),
                "actual_mode": ev["actual_mode"],
                "stdout_preview": stdout[:200].replace("\n", "\\n"),
                "result": ev,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            mark = "✅" if ev["pass"] else "❌"
            print(f"{mark} expect={case['expect_mode']:8} actual={ev['actual_mode']:8} fw={decision.get('framework_primary','?')}")
            if ev["pass"]:
                pass_count += 1

        unit_rows = run_unit_tests()
        unit_pass = sum(1 for r in unit_rows if r.get("pass"))
        for r in unit_rows:
            f.write(json.dumps({"unit": True, **{k: v for k, v in r.items() if k != "result"}, "result_mode": r["result"].get("mode")}, ensure_ascii=False) + "\n")

    # summary
    lines = [
        "# Day 3 — SP wrapper / Director-Worker dispatch 测试结果",
        "",
        f"**脚本可验部分**: {pass_count}/{len(CASES)} = {100*pass_count/len(CASES):.1f}%",
        "",
        "## 验证内容",
        "",
        "| Case | Mode 对 | Skill 名 | SKILL.md 存在 | Agent 参数 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        cid = r["case"]["id"]
        checks = {c["check"]: c.get("pass") for c in r["result"]["checks"]}
        mode_ok = "✅" if checks.get("mode") else "❌"
        sk_ok = "—" if "skill" not in checks else ("✅" if checks["skill"] else "❌")
        path_ok = "—" if not any(c.startswith("skill_path") for c in checks) else (
            "✅" if checks.get("skill_path_exists") else "❌")
        agent_ok = "—" if not any("subagent_type" in c for c in checks) else (
            "✅" if all(v for k, v in checks.items() if "subagent_type" in k or "model:" in k) else "❌")
        lines.append(f"| {cid} | {mode_ok} | {sk_ok} | {path_ok} | {agent_ok} |")

    lines.extend([
        "",
        "## 不可脚本验(留 [你验])",
        "",
        "**⑥.1 SP 5-phase 完整跑通**",
        "",
        "Hook 输出 dispatch 指令,真正调 Agent 工具是主 session Claude 的事。",
        "脚本只能验文本格式,验不了 sub-agent 在 isolated context 里",
        "  - 实际 invoke 到 SKILL.md",
        "  - 5-phase 全跑完",
        "  - 返回的摘要质量",
        "",
        "**手动验法**:",
        "1. 在 Claude Code 主 session 输入: `用 TDD 写一个 LRU 缓存`",
        "2. 看 hook 注入是否含 `[Director-Worker dispatch]`",
        "3. Claude 应自动调 Agent 工具,subagent_type=general-purpose",
        "4. 等 Agent 返回,检查是否真的执行了 5-phase TDD",
        "5. 验摘要质量 + 主 session context 占用是否显著低于 v3.1",
        "",
        "## Day 3 验收门槛",
        "",
        f"- 脚本部分: **{pass_count}/{len(CASES)}** {'✅ 通过' if pass_count == len(CASES) else '⚠️ 需修'}",
        "- [你验] 部分: 待 chat 里手动测",
    ])

    lines.extend([
        "",
        f"## 单元测试 (dispatch_subagent 内部)",
        "",
        f"**{unit_pass}/{len(unit_rows)} 通过**",
    ])

    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=== 端到端 {pass_count}/{len(CASES)} | 单元 {unit_pass}/{len(unit_rows)} ===")
    print(f"results: {RESULTS}")
    print(f"summary: {SUMMARY}")


if __name__ == "__main__":
    main()

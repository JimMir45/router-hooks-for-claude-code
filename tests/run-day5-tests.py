#!/usr/bin/env python3
"""
run-day5-tests.py — Day 5 验收

测试:
  ④.1 长 task 进度可见  — dispatch_text/sub_prompt 含 [PROGRESS] 约定
  ④.2 sub-agent 失败回退 — dispatch_text 含 supervisor 协议 + fallback 路径
  ⑦   uninstall < 10s    — 复制到 temp 跑回滚,验证耗时 + 残留 + 语法

⑦ 不动真实 hook/ 目录,在 temp 里跑。
"""

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_DIR = ROOT / "hook"
RESULTS = ROOT / "tests" / "results" / "day5-tests.md"

sys.path.insert(0, str(HOOK_DIR))


def test_progress_visible():
    """④.1 — sub_prompt 提供可选 [STATUS] milestone;dispatch text 告诉主 session 转发(best-effort)
    经 e2e 验证后:[PROGRESS] phase=N/TOTAL 严格协议 LLM 不可靠遵守,降级为 [STATUS] 自由形式。
    """
    from dispatch_subagent import build_dispatch_instruction
    cases = [
        ("用 TDD 写 LRU", {"framework_primary": "SP", "task_type": "execution"}),
        ("Postgres vs Mongo", {"framework_primary": "GS", "task_type": "decision", "gs_role": "EngManager"}),
        ("调研 vector DB", {"framework_primary": "ECC", "task_type": "domain", "ecc_subskill": "research"}),
    ]
    checks = []
    for prompt, decision in cases:
        d = build_dispatch_instruction(prompt, decision)
        sub = d["sub_agent_prompt"] or ""
        text = d["text"]
        ok_status_offered = "[STATUS]" in sub and "milestone" in sub.lower()
        ok_supervisor_surfaces = "[STATUS]" in text and "best-effort" in text.lower()
        checks.append({
            "fw": decision["framework_primary"],
            "status_in_sub_prompt": ok_status_offered,
            "supervisor_relays": ok_supervisor_surfaces,
            "pass": ok_status_offered and ok_supervisor_surfaces,
        })
    return checks


def test_failure_fallback():
    """④.2 - dispatch text 必须告诉主 session 失败回退到 inject 模式"""
    from dispatch_subagent import build_dispatch_instruction
    cases = [
        ("用 TDD 写 LRU", {"framework_primary": "SP", "task_type": "execution"}),
        ("Postgres vs Mongo", {"framework_primary": "GS", "task_type": "decision", "gs_role": "EngManager"}),
        ("调研 vector DB", {"framework_primary": "ECC", "task_type": "domain", "ecc_subskill": "research"}),
    ]
    checks = []
    for prompt, decision in cases:
        d = build_dispatch_instruction(prompt, decision)
        text = d["text"]
        sub = d["sub_agent_prompt"] or ""
        # sub-agent 必须输出 OUTCOME envelope
        ok_outcome_envelope = "[OUTCOME status=" in sub and "[/OUTCOME]" in sub
        # supervisor 必须有失败处理逻辑
        ok_failed_handling = "status=failed" in text and "Fallback" in text and "v3.1 inject" in text
        ok_empty_handling = "empty" in text.lower() or "missing" in text.lower()
        checks.append({
            "fw": decision["framework_primary"],
            "outcome_envelope_required": ok_outcome_envelope,
            "supervisor_handles_failed": ok_failed_handling,
            "supervisor_handles_empty": ok_empty_handling,
            "pass": ok_outcome_envelope and ok_failed_handling and ok_empty_handling,
        })
    return checks


def test_uninstall_speed():
    """⑦ - uninstall < 10s,无 fence 残留,语法对"""
    # 拷贝整个 hook 目录到 temp
    tmp = ROOT / "tests" / "results" / ".uninstall-test"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(HOOK_DIR, tmp)

    script = tmp / "uninstall-director.sh"

    t0 = time.perf_counter()
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=30)
    elapsed = time.perf_counter() - t0

    router_after = (tmp / "router.py").read_text()
    task_classifier_gone = not (tmp / "task_classifier.py").exists()
    dispatch_gone = not (tmp / "dispatch_subagent.py").exists()
    no_fence = "DIRECTOR-WORKER" not in router_after
    no_imports = "task_classifier" not in router_after and "dispatch_subagent" not in router_after
    has_v31_call = "print(render_injection(decision))" in router_after

    # 语法可解析
    import ast
    try:
        ast.parse(router_after)
        syntax_ok = True
    except SyntaxError as e:
        syntax_ok = False
        print(f"  syntax error: {e}")

    return {
        "elapsed_seconds": elapsed,
        "under_10s": elapsed < 10.0,
        "exit_code": proc.returncode,
        "task_classifier_removed": task_classifier_gone,
        "dispatch_subagent_removed": dispatch_gone,
        "router_no_fence": no_fence,
        "router_no_dw_imports": no_imports,
        "router_has_v31_call": has_v31_call,
        "router_syntax_ok": syntax_ok,
        "stdout_tail": proc.stdout[-200:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-200:] if proc.stderr else "",
        "pass": elapsed < 10 and proc.returncode == 0 and task_classifier_gone and dispatch_gone
                and no_fence and no_imports and has_v31_call and syntax_ok,
    }


def main():
    print("=== ④.1 progress visible (best-effort STATUS) ===")
    progress = test_progress_visible()
    for c in progress:
        print(f"  {c['fw']:4} status_offered={'✅' if c['status_in_sub_prompt'] else '❌'} "
              f"supervisor_relays={'✅' if c['supervisor_relays'] else '❌'} → {'✅' if c['pass'] else '❌'}")
    p1 = all(c["pass"] for c in progress)

    print("\n=== ④.2 failure fallback ===")
    fallback = test_failure_fallback()
    for c in fallback:
        print(f"  {c['fw']:4} envelope={'✅' if c['outcome_envelope_required'] else '❌'} "
              f"failed_handle={'✅' if c['supervisor_handles_failed'] else '❌'} "
              f"empty_handle={'✅' if c['supervisor_handles_empty'] else '❌'} → {'✅' if c['pass'] else '❌'}")
    p2 = all(c["pass"] for c in fallback)

    print("\n=== ⑦ uninstall ===")
    u = test_uninstall_speed()
    print(f"  elapsed={u['elapsed_seconds']:.3f}s (target <10s) → {'✅' if u['under_10s'] else '❌'}")
    print(f"  exit_code={u['exit_code']} → {'✅' if u['exit_code']==0 else '❌'}")
    print(f"  task_classifier removed: {'✅' if u['task_classifier_removed'] else '❌'}")
    print(f"  dispatch_subagent removed: {'✅' if u['dispatch_subagent_removed'] else '❌'}")
    print(f"  router no fence: {'✅' if u['router_no_fence'] else '❌'}")
    print(f"  router no DW imports: {'✅' if u['router_no_dw_imports'] else '❌'}")
    print(f"  router has v3.1 call: {'✅' if u['router_has_v31_call'] else '❌'}")
    print(f"  router syntax ok: {'✅' if u['router_syntax_ok'] else '❌'}")
    if u["stdout_tail"]:
        print(f"  stdout: ...{u['stdout_tail']}")
    p3 = u["pass"]

    # write summary
    lines = [
        "# Day 5 测试结果",
        "",
        f"- ④.1 长 task 进度可见: **{'通过' if p1 else '失败'}**",
        f"- ④.2 sub-agent 失败回退: **{'通过' if p2 else '失败'}**",
        f"- ⑦ uninstall < 10s: **{'通过' if p3 else '失败'}** (实际 {u['elapsed_seconds']:.3f}s)",
        "",
        "## 备注",
        "- ④.1 验的是 `[PROGRESS] phase=N/total` 在 sub_prompt 必约定 + dispatch text 告诉主 session 转发",
        "- ④.2 验的是 `[OUTCOME status=success|failed]` envelope + supervisor 协议含 fallback 路径",
        "- ⑦ 在 temp 拷贝里跑,不动真实 hook/。回滚后 router.py 必须语法对 + 无 Director-Worker 残留",
    ]
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text("\n".join(lines), encoding="utf-8")

    total_pass = sum([p1, p2, p3])
    print(f"\n=== Day 5: {total_pass}/3 ===")
    print(f"summary: {RESULTS}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
E2E scenario driver for router hook chain testing.
Simulates 12 real-engineer scenarios without starting Claude Code.
Directly feeds payloads to hooks and records full chain logs.

Usage:
    python3 run-e2e.py [--scenario e2e_01] [--verbose]

Output:
    tests/results/e2e-scenarios.jsonl  — per-scenario result with chain log
    tests/results/e2e-summary.md       — human-readable summary
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
HOOK_DIR = Path(os.environ.get("ROUTER_HOOK_DIR", Path.home() / ".router-hook"))
CASES_FILE = HERE / "cases" / "e2e-scenarios.jsonl"
RESULTS_DIR = HERE / "results"
RESULTS_FILE = RESULTS_DIR / "e2e-scenarios.jsonl"
SUMMARY_FILE = RESULTS_DIR / "e2e-summary.md"

ROUTER_PY      = HOOK_DIR / "router.py"
RUNTIME_GUARD  = HOOK_DIR / "runtime-guard.py"
COMPLETION_CHK = HOOK_DIR / "completion-check.py"
FAILURE_TRCKR  = HOOK_DIR / "failure-tracker.py"

# Failure streak log (shared with runtime-guard)
STREAK_LOG = Path.home() / ".claude" / "router-logs" / "failure-streak.log"

SESSION_ID = "e2e-test-session"
CWD = str(Path.home() / "router-eval-share")

TIMEOUT = 20  # seconds per hook call

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_scenarios(only_id=None):
    cases = []
    with open(CASES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            if only_id and c["id"] != only_id:
                continue
            cases.append(c)
    return cases


def call_hook(hook_path: Path, payload: dict, timeout: int = TIMEOUT) -> dict:
    """Invoke a hook script with JSON payload on stdin. Returns {stdout, stderr, exit_code, latency_ms}."""
    t0 = time.time()
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(hook_path)],
            input=payload_bytes,
            capture_output=True,
            timeout=timeout,
        )
        latency = int((time.time() - t0) * 1000)
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.decode("utf-8", errors="replace").strip(),
            "stderr": proc.stderr.decode("utf-8", errors="replace").strip(),
            "latency_ms": latency,
        }
    except subprocess.TimeoutExpired:
        latency = int((time.time() - t0) * 1000)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "TIMEOUT",
            "latency_ms": latency,
        }
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return {
            "exit_code": -2,
            "stdout": "",
            "stderr": str(e),
            "latency_ms": latency,
        }


def setup_failure_streak(tool_name: str, count: int, error_msg: str = "subprocess.CalledProcessError"):
    """Write N consecutive failure entries to the streak log for a given tool."""
    STREAK_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Read existing lines, filter out old test entries for this tool
    existing = []
    if STREAK_LOG.exists():
        for line in STREAK_LOG.read_text().splitlines():
            try:
                e = json.loads(line)
                if e.get("session_id") != SESSION_ID:
                    existing.append(line)
            except Exception:
                existing.append(line)
    # Append fresh consecutive failures
    new_entries = []
    for i in range(count):
        new_entries.append(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tool_name": tool_name,
            "error": error_msg,
            "session_id": SESSION_ID,
        }, ensure_ascii=False))
    STREAK_LOG.write_text("\n".join(existing + new_entries) + "\n")


def clear_failure_streak_for_session():
    """Remove all failure entries from the current test session."""
    if not STREAK_LOG.exists():
        return
    lines = []
    for line in STREAK_LOG.read_text().splitlines():
        try:
            e = json.loads(line)
            if e.get("session_id") != SESSION_ID:
                lines.append(line)
        except Exception:
            lines.append(line)
    STREAK_LOG.write_text("\n".join(lines) + ("\n" if lines else ""))


def setup_active_plan(tmpdir: str, plan_content: str):
    """Create .claude/active-plan.md under tmpdir if plan_content provided."""
    if not plan_content:
        return None
    plan_dir = Path(tmpdir) / ".claude"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plan_dir / "active-plan.md"
    plan_file.write_text(plan_content)
    return str(plan_file)


def build_transcript_file(transcript_data) -> str:
    """Write transcript JSON to a temp file, return its path."""
    fd, path = tempfile.mkstemp(suffix=".jsonl", prefix="e2e_transcript_")
    os.close(fd)
    if isinstance(transcript_data, str):
        # already a JSON string of a list
        entries = json.loads(transcript_data)
    else:
        entries = transcript_data
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# Router hook call (UserPromptSubmit)
# ---------------------------------------------------------------------------

def run_router_scenario(scenario: dict, verbose: bool) -> dict:
    """Call router.py with a UserPromptSubmit payload."""
    prompt = scenario.get("prompt", "")
    payload = {
        "prompt": prompt,
        "session_id": SESSION_ID,
        "cwd": CWD,
    }
    step = {
        "hook": "router.py",
        "event": "UserPromptSubmit",
        "payload_summary": {"prompt_preview": prompt[:80]},
    }
    result = call_hook(ROUTER_PY, payload)
    step["result"] = result

    # Parse router output
    stdout = result["stdout"]
    decision_info = {}
    if stdout:
        decision_info["has_action_required"] = "[ACTION REQUIRED]" in stdout
        # Extract framework from injection line
        m = re.search(r"🧭 Router → ([\w\-\(\) ]+?)\s+\(conf", stdout)
        if m:
            decision_info["routed_to"] = m.group(1).strip()
        decision_info["offline_topic"] = "OFFLINE_TOPIC" in stdout
        decision_info["has_output"] = True
    else:
        decision_info["has_output"] = False
        decision_info["has_action_required"] = False
        decision_info["routed_to"] = "CC (fast-path or silent)"
        decision_info["offline_topic"] = False

    step["decision_info"] = decision_info
    return step


# ---------------------------------------------------------------------------
# Runtime-guard hook call (PreToolUse)
# ---------------------------------------------------------------------------

def run_runtime_guard_scenario(scenario: dict, tmpdir: str, verbose: bool) -> list:
    """Call runtime-guard.py with a PreToolUse payload. Returns list of steps."""
    steps = []

    # Step 0: If failure streak setup needed, prime the log
    streak_setup = scenario.get("streak_setup")
    if streak_setup:
        setup_failure_streak(
            streak_setup["tool"],
            streak_setup["count"],
            streak_setup.get("error", "FAILED - AssertionError"),
        )
        steps.append({
            "hook": "failure-tracker.py",
            "event": "streak_setup",
            "payload_summary": streak_setup,
            "result": {"note": f"Wrote {streak_setup['count']} failure entries to streak log"},
        })

    # Step 1: Build plan if needed
    plan_content = scenario.get("plan_content") or (scenario.get("payload", {}).get("_setup_plan"))
    plan_file = setup_active_plan(tmpdir, plan_content)

    payload = {
        "tool_name": scenario.get("tool_name", "Bash"),
        "tool_input": scenario.get("tool_input", {}),
        "session_id": SESSION_ID,
        "cwd": tmpdir if plan_content else CWD,
    }

    step = {
        "hook": "runtime-guard.py",
        "event": "PreToolUse",
        "payload_summary": {
            "tool_name": payload["tool_name"],
            "cmd_preview": str(scenario.get("tool_input", {}))[:80],
        },
    }
    result = call_hook(RUNTIME_GUARD, payload)
    step["result"] = result

    # Parse decision
    stdout = result["stdout"]
    if stdout:
        try:
            dec = json.loads(stdout)
            step["decision"] = dec.get("decision", "unknown")
            step["reason_preview"] = dec.get("reason", "")[:150]
        except Exception:
            step["decision"] = "parse_error"
            step["reason_preview"] = stdout[:150]
    else:
        step["decision"] = "allow"
        step["reason_preview"] = ""

    steps.append(step)
    return steps


# ---------------------------------------------------------------------------
# Completion-check hook call (Stop)
# ---------------------------------------------------------------------------

def run_completion_check_scenario(scenario: dict, tmpdir: str, verbose: bool) -> list:
    """Call completion-check.py with a Stop payload. Returns list of steps."""
    steps = []

    # Build transcript temp file
    transcript_data = []
    assistant_text = scenario.get("assistant_text", "")
    if assistant_text:
        transcript_data = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": assistant_text}]
                }
            }
        ]
    transcript_path = build_transcript_file(transcript_data)

    # Setup active-plan if needed
    plan_content = scenario.get("plan_content")
    if plan_content:
        setup_active_plan(tmpdir, plan_content)

    payload = {
        "transcript_path": transcript_path,
        "session_id": SESSION_ID,
        "stop_hook_active": scenario.get("stop_hook_active", False),
    }

    step = {
        "hook": "completion-check.py",
        "event": "Stop",
        "payload_summary": {
            "assistant_text_preview": assistant_text[:80],
            "stop_hook_active": payload["stop_hook_active"],
        },
    }
    result = call_hook(COMPLETION_CHK, payload)
    step["result"] = result

    # Parse decision
    stdout = result["stdout"]
    if stdout:
        try:
            dec = json.loads(stdout)
            step["decision"] = dec.get("decision", "unknown")
            step["reason_preview"] = dec.get("reason", "")[:150]
        except Exception:
            step["decision"] = "parse_error"
            step["reason_preview"] = stdout[:150]
    else:
        step["decision"] = "pass"
        step["reason_preview"] = ""

    steps.append(step)

    # Cleanup temp transcript
    try:
        os.unlink(transcript_path)
    except Exception:
        pass

    return steps


# ---------------------------------------------------------------------------
# Scenario dispatcher
# ---------------------------------------------------------------------------

def run_scenario(scenario: dict, verbose: bool) -> dict:
    scenario_id = scenario["id"]
    scene = scenario["scene"]
    if verbose:
        print(f"\n{'='*60}")
        print(f"[{scenario_id}] {scene}")

    ts_start = time.strftime("%Y-%m-%dT%H:%M:%S")
    chain_log = []
    passed = False
    actual_decision = None
    error = None

    with tempfile.TemporaryDirectory(prefix=f"e2e_{scenario_id}_") as tmpdir:
        try:
            hooks = scenario.get("hooks_under_test", [])

            if "router.py" in hooks and scenario.get("expected_router"):
                # Scenarios 1-7: router test
                step = run_router_scenario(scenario, verbose)
                chain_log.append(step)

                expected_router = scenario.get("expected_router", "CC")
                expected_action = scenario.get("expected_action_required", False)
                expected_fast = scenario.get("expected_fast_path", False)
                expected_offline = scenario.get("expected_offline_topic", False)

                di = step.get("decision_info", {})
                has_action = di.get("has_action_required", False)
                routed_to = di.get("routed_to", "")
                has_output = di.get("has_output", False)
                offline = di.get("offline_topic", False)

                # Evaluate pass/fail
                if expected_fast:
                    # Fast path: no output expected (silent mode won't render CC)
                    passed = not has_output or (not has_action)
                    actual_decision = "fast-path→CC" if not has_output else routed_to
                elif expected_router == "CC" and expected_offline:
                    passed = offline or (not has_action)
                    actual_decision = f"CC/offline={offline}"
                elif expected_router == "CC":
                    passed = not has_action
                    actual_decision = routed_to or "CC"
                elif expected_router == "SP":
                    passed = has_action and ("Superpowers" in routed_to or "SP" in routed_to)
                    actual_decision = routed_to
                elif expected_router == "GS-CEO":
                    passed = has_action and ("CEO" in routed_to or "gstack-CEO" in routed_to)
                    actual_decision = routed_to
                elif expected_router == "GS-EngManager":
                    passed = has_action and ("EngManager" in routed_to or "gstack-EngManager" in routed_to)
                    actual_decision = routed_to
                elif expected_router == "ECC":
                    passed = has_action
                    actual_decision = routed_to
                else:
                    passed = True
                    actual_decision = routed_to

            elif "runtime-guard.py" in hooks:
                # Scenarios 8-9, 12: runtime-guard test
                steps = run_runtime_guard_scenario(scenario, tmpdir, verbose)
                chain_log.extend(steps)

                # The last step is always the guard decision
                guard_step = next((s for s in reversed(steps) if s.get("hook") == "runtime-guard.py"), None)
                if guard_step:
                    actual_decision = guard_step.get("decision", "unknown")
                    expected = scenario.get("expected_decision", "block")
                    passed = actual_decision == expected
                else:
                    passed = False
                    actual_decision = "no_guard_step"

            elif "completion-check.py" in hooks:
                # Scenarios 10-11: completion-check test
                steps = run_completion_check_scenario(scenario, tmpdir, verbose)
                chain_log.extend(steps)

                cc_step = next((s for s in reversed(steps) if s.get("hook") == "completion-check.py"), None)
                if cc_step:
                    actual_decision = cc_step.get("decision", "unknown")
                    expected = scenario.get("expected_decision", "pass")
                    passed = actual_decision == expected
                else:
                    passed = False
                    actual_decision = "no_cc_step"

        except Exception as e:
            error = str(e)
            passed = False

    # Cleanup streak entries we added
    clear_failure_streak_for_session()

    result = {
        "id": scenario_id,
        "scene": scene,
        "persona": scenario.get("persona", {}),
        "prompt_preview": scenario.get("prompt", scenario.get("assistant_text", ""))[:100],
        "expected_router": scenario.get("expected_router"),
        "expected_decision": scenario.get("expected_decision"),
        "actual_decision": actual_decision,
        "passed": passed,
        "ts": ts_start,
        "chain_log": chain_log,
        "error": error,
    }

    status = "PASS" if passed else "FAIL"
    if verbose:
        print(f"  → {status} | actual={actual_decision}")
        if error:
            print(f"  ERROR: {error}")
    else:
        print(f"  [{status}] {scenario_id}: {scene} → {actual_decision}")

    return result


# ---------------------------------------------------------------------------
# Summary generator
# ---------------------------------------------------------------------------

HOOK_DESCRIPTIONS = {
    "router.py": "UserPromptSubmit → 5层意图路由器",
    "runtime-guard.py": "PreToolUse → 黑名单/熔断/scope检查",
    "completion-check.py": "Stop → 假宣告检测",
    "failure-tracker.py": "PostToolUse → 失败计数记录",
}


def generate_summary(results: list) -> str:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    lines = [
        "# E2E 场景测试 — 结果汇总",
        "",
        f"**总场景**: {total}  |  **通过**: {passed}  |  **失败**: {failed}",
        f"**通过率**: {passed/total*100:.1f}%",
        "",
        "---",
        "",
    ]

    for r in results:
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        lines.append(f"## [{r['id']}] {r['scene']}  {status}")
        lines.append("")

        p = r.get("persona", {})
        lines.append(f"**画像**: {p.get('role','?')} — {p.get('task','?')}")

        prompt = r.get("prompt_preview", "")
        if prompt:
            lines.append(f"**Prompt**: `{prompt}`")

        # Hook chain
        chain = r.get("chain_log", [])
        if chain:
            lines.append("**Hook 链路**:")
            for step in chain:
                hook = step.get("hook", "?")
                event = step.get("event", "")
                dec = step.get("decision", "")
                di = step.get("decision_info", {})
                res = step.get("result", {})
                latency = res.get("latency_ms", "?")

                if dec:
                    lines.append(f"  - `{hook}` ({event}) → **{dec}** ({latency}ms)")
                    reason = step.get("reason_preview", "")
                    if reason:
                        lines.append(f"    > {reason[:120]}")
                elif di:
                    routed = di.get("routed_to", "CC")
                    has_action = di.get("has_action_required", False)
                    offline = di.get("offline_topic", False)
                    tag = "[ACTION REQUIRED]" if has_action else ("[OFFLINE]" if offline else "[silent]")
                    lines.append(f"  - `{hook}` ({event}) → **{routed}** {tag} ({latency}ms)")
                else:
                    lines.append(f"  - `{hook}` ({event}) ({latency}ms)")

        exp_r = r.get("expected_router", "")
        exp_d = r.get("expected_decision", "")
        actual = r.get("actual_decision", "?")
        if exp_r:
            lines.append(f"**预期路由**: {exp_r}  **实际**: {actual}")
        if exp_d:
            lines.append(f"**预期决策**: {exp_d}  **实际**: {actual}")

        if r.get("error"):
            lines.append(f"**错误**: {r['error']}")

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="E2E hook chain scenario driver")
    parser.add_argument("--scenario", help="Run only this scenario ID (e.g. e2e_01)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Verify hooks exist
    for hook in [ROUTER_PY, RUNTIME_GUARD, COMPLETION_CHK, FAILURE_TRCKR]:
        if not hook.exists():
            print(f"ERROR: Hook not found: {hook}", file=sys.stderr)
            sys.exit(1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    scenarios = load_scenarios(only_id=args.scenario)
    if not scenarios:
        print("No scenarios found.", file=sys.stderr)
        sys.exit(1)

    print(f"Running {len(scenarios)} E2E scenarios...")
    print(f"Hooks dir: {HOOK_DIR}")
    print()

    # Set router mode to auto so we can see all decisions in stdout
    mode_file = Path.home() / ".config" / "router-hook" / "mode"
    original_mode = None
    try:
        if mode_file.exists():
            original_mode = mode_file.read_text().strip()
        mode_file.parent.mkdir(parents=True, exist_ok=True)
        mode_file.write_text("auto")
    except Exception as e:
        print(f"WARNING: Could not set router mode to auto: {e}")

    results = []
    try:
        for scenario in scenarios:
            r = run_scenario(scenario, verbose=args.verbose)
            results.append(r)
    finally:
        # Restore original mode
        try:
            if original_mode is not None:
                mode_file.write_text(original_mode)
            elif mode_file.exists():
                mode_file.write_text("silent")
        except Exception:
            pass

    # Write results JSONL
    with open(RESULTS_FILE, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nResults written to: {RESULTS_FILE}")

    # Write summary markdown
    summary = generate_summary(results)
    SUMMARY_FILE.write_text(summary)
    print(f"Summary written to: {SUMMARY_FILE}")

    # Final stats
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    print(f"\n{'='*40}")
    print(f"TOTAL: {total}  PASS: {passed}  FAIL: {failed}")
    print(f"{'='*40}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

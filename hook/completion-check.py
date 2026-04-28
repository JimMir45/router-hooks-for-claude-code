#!/usr/bin/env python3
"""
Stop hook: completion sanity check.
Implements autonomy-rules.md §self-verify vs user-verify.

Goal: catch false completion claims — when Claude says 'done' but:
  (a) used hedging words: "should be OK" / "probably" / "looks fine"
  (b) active-plan.md still has unchecked [Agent self-verify] items

Block decision injects a reason string back into Claude, asking it to
actually run the verification before claiming completion.

Exit semantics:
  - exit 0 + empty stdout = pass
  - JSON {"decision":"block","reason":"..."} = block, force re-think
"""
import json
import os
import re
import sys
import time
from pathlib import Path

_CONFIG_BASE = Path(os.environ.get("ROUTER_HOOK_CONFIG", Path.home() / ".config" / "router-hook"))
MODE_FILE = _CONFIG_BASE / "mode"

LOG = Path.home() / ".claude" / "router-logs" / "completion-check.log"
LOG.parent.mkdir(parents=True, exist_ok=True)


def load_mode():
    try:
        return MODE_FILE.read_text().strip().lower()
    except Exception:
        return "silent"


# Hedging words that often hide unverified claims.
# Only fires on explicit "done + hedge" combos or compact "should be OK" phrases.
# Bilingual: catches both English and Chinese hedge patterns.
HEDGE_PATTERNS = [
    # (a) completion claim + hedge word nearby
    (re.compile(
        r"(completed?|done|finished|fixed|implemented|"
        r"完成|搞定|做完|改完|跑通)[^\n]{0,25}?"
        r"(should|probably|maybe|might\s*be|perhaps|likely|"
        r"应该|估计|看起来|可能|大概|理论上)", re.I),
     "completion claim with hedging word"),
    (re.compile(
        r"(should|probably|maybe|might\s*be|perhaps|likely|"
        r"应该|估计|看起来|可能|大概|理论上)[^\n]{0,25}?"
        r"(completed?|done|finished|fixed|implemented|"
        r"完成了|搞定了|做完了|改完了|跑通了)", re.I),
     "completion claim with hedging word (hedge first)"),
    # (b) compact optimistic phrases
    (re.compile(
        r"(should|probably|maybe|likely|应该|估计|看起来|大概)"
        r"[^\n]{0,3}?(be\s*fine|be\s*ok|work|no\s*issues?|all\s*good|"
        r"没问题|没事|可以了|搞定了|没什么问题)", re.I),
     "unverified optimistic assertion"),
    # (c) explicitly says 'didn't test' but claims done
    (re.compile(
        r"(didn.t|haven.t|without|没.{0,3})"
        r"(test|verify|run|try|测试|验证|跑|试)"
        r".{0,30}?(done|complete|finished|works?|fine|ok|"
        r"完成|可以|没问题)", re.I),
     "claiming done without self-verification"),
]


def hedge_check(last_assistant_text: str):
    if not last_assistant_text:
        return None
    for rx, label in HEDGE_PATTERNS:
        if rx.search(last_assistant_text):
            return {
                "decision": "block",
                "reason": (
                    f"Self-verify not done: detected [{label}]\n"
                    f"Per autonomy-rules.md §self-verify vs user-verify, "
                    f"completion claims must be based on script exit codes.\n"
                    f"Please actually run verify/test commands and show exit code "
                    f"instead of saying 'should be OK'."
                ),
            }
    return None


def active_plan_check(transcript_path: str):
    """If cwd has active-plan.md with unchecked [Agent self-verify] items, block."""
    cwd = os.getcwd()
    plan = Path(cwd) / ".claude" / "active-plan.md"
    if not plan.exists():
        return None
    try:
        text = plan.read_text()
    except Exception:
        return None
    # Matches both English [Agent self-verify] and Chinese [Agent 自验]
    unchecked = re.findall(
        r"^\s*-\s*\[\s*\].*\[Agent\s*(self-verify|自验)\].*$", text, re.M
    )
    if unchecked:
        return {
            "decision": "block",
            "reason": (
                f"active-plan.md still has {len(unchecked)} unchecked [Agent self-verify] items:\n"
                + "\n".join(f"  * {u.strip()[:120]}" for u in unchecked[:5])
                + "\nPlease run each verification script, check them off, then claim completion."
            ),
        }
    return None


def get_last_assistant_text(transcript_path: str) -> str:
    """Read last assistant message from transcript jsonl."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
        for line in reversed(lines[-40:]):
            try:
                obj = json.loads(line)
                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                return c.get("text", "")
                    elif isinstance(content, str):
                        return content
            except Exception:
                continue
    except Exception:
        return ""
    return ""


def log(decision):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "decision": decision.get("decision", "pass") if decision else "pass",
                "reason": decision.get("reason", "")[:200] if decision else "",
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    if load_mode() == "off":
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if payload.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = payload.get("transcript_path", "")
    last_text = get_last_assistant_text(transcript_path)

    for check in (
        lambda: hedge_check(last_text),
        lambda: active_plan_check(transcript_path),
    ):
        result = check()
        if result and result.get("decision") == "block":
            log(result)
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(0)

    log(None)
    sys.exit(0)


if __name__ == "__main__":
    main()

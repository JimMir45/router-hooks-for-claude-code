#!/usr/bin/env python3
"""
PreToolUse hook: runtime decision guard.
Implements autonomy-rules.md §② runtime decision:
  - 5-category blacklist (technical intercept)
  - 3-consecutive-failure circuit breaker
  - scope creep detection (outside active-plan.md range)

Does NOT duplicate checks already covered by other hooks
(block-no-verify, git-push-reminder, config-protection).

Exit semantics:
  - exit 0 + empty stdout = allow
  - exit 0 + JSON {"decision":"block","reason":"..."} = block + show reason
"""
import json
import os
import re
import sys
import time
from pathlib import Path

_CONFIG_BASE = Path(os.environ.get("ROUTER_HOOK_CONFIG",
                                    str(Path.home() / ".config" / "router-hook")))
MODE_FILE = _CONFIG_BASE / "mode"

LOG_DIR = Path.home() / ".claude" / "router-logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
FAILURE_LOG = LOG_DIR / "failure-streak.log"
RUNTIME_LOG = LOG_DIR / "runtime-guard.log"


def load_mode():
    try:
        return MODE_FILE.read_text().strip().lower()
    except Exception:
        return "silent"


# --- Rule 1: Scope creep detection ---
def scope_creep_check(tool_name: str, tool_input: dict, cwd: str):
    """If active-plan.md exists and declares allowed files, enforce scope."""
    if tool_name not in ("Edit", "Write", "MultiEdit"):
        return None
    fp = tool_input.get("file_path", "")
    if not fp:
        return None
    plan = Path(cwd) / ".claude" / "active-plan.md"
    if not plan.exists():
        return None
    try:
        text = plan.read_text()
    except Exception:
        return None
    m = re.search(r"(?:Allowed files|allowed_files):\s*\n((?:[ \t]*[-*]\s*.+\n?)+)", text, re.I)
    if not m:
        return None
    allowed_block = m.group(1)
    allowed = [line.strip("-* \t\n") for line in allowed_block.strip().split("\n")]
    allowed = [a for a in allowed if a]
    if not allowed:
        return None
    fp_rel = fp.replace(cwd, "").lstrip("/")
    for pat in allowed:
        if pat in fp or pat in fp_rel:
            return None
    return {
        "decision": "block",
        "reason": (
            f"Scope creep: {fp} is not in active-plan.md's Allowed files scope\n"
            f"Allowed: {allowed}\n"
            f"To expand scope, update active-plan.md first or explicitly override."
        ),
    }


# --- Rule 2: Failure streak ---
def failure_streak_check(tool_name: str):
    """Read failure-streak.log written by failure-tracker.py."""
    if not FAILURE_LOG.exists():
        return None
    try:
        lines = FAILURE_LOG.read_text().strip().split("\n")[-10:]
    except Exception:
        return None
    recent = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            if entry.get("tool_name") != tool_name:
                break
            recent.append(entry)
            if len(recent) >= 3:
                break
        except Exception:
            continue
    if len(recent) >= 3:
        return {
            "decision": "block",
            "reason": (
                f"Circuit breaker: {tool_name} failed 3 consecutive times\n"
                f"Last failure: {recent[0].get('error', '?')[:120]}\n"
                f"Stop and diagnose root cause. "
                f"Say 'clear failure-streak' to reset the counter."
            ),
        }
    return None


# --- Rule 3: Hard blacklist ---
DESTRUCTIVE_BASH = [
    # Only intercept recursive rm (flags include r or R), not rm -f single-file
    (re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\b.*?(/|~|\$HOME|\*)"),
     "rm -rf bulk delete"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", re.I),
     "SQL DROP (irreversible)"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.I),
     "SQL TRUNCATE (irreversible)"),
    (re.compile(r"\bDELETE\s+FROM\s+\w+\s*(?!.*WHERE)", re.I),
     "SQL DELETE without WHERE (full table)"),
    (re.compile(r"\bgit\s+reset\s+--hard\s+(HEAD~|origin)"),
     "git reset --hard (discards uncommitted work)"),
    (re.compile(r"\bgit\s+push\s+.*(--force-with-lease|--force|-f\b)"),
     "git push --force"),
]

EMAIL_API_PATTERNS = [
    re.compile(r"api\.mailgun\.net", re.I),
    re.compile(r"api\.sendgrid\.com", re.I),
    re.compile(r"api\.resend\.com", re.I),
    re.compile(r"smtp\.\w+\.\w+", re.I),
    re.compile(r"\bcurl.*?-X\s+POST.*?(api\.slack\.com|hooks\.slack\.com|api\.feishu\.cn)", re.I),
]


def blacklist_check(tool_name: str, tool_input: dict):
    if tool_name != "Bash":
        return None
    cmd = tool_input.get("command", "")
    if not cmd:
        return None
    for rx, label in DESTRUCTIVE_BASH:
        if rx.search(cmd):
            return {
                "decision": "block",
                "reason": (
                    f"Blacklist #1 data loss: {label}\n"
                    f"Pattern: {rx.pattern}\n"
                    f"See autonomy-rules.md blacklist. "
                    f"If intentional, use a more specific command (limit path / add WHERE)."
                ),
            }
    for rx in EMAIL_API_PATTERNS:
        if rx.search(cmd):
            return {
                "decision": "block",
                "reason": (
                    "Blacklist #2 real-user communication: detected outbound email/IM API call\n"
                    "See autonomy-rules.md blacklist. "
                    "If this is a test endpoint, explicitly say so."
                ),
            }
    return None


def log_entry(payload, decision):
    try:
        with open(RUNTIME_LOG, "a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tool_name": payload.get("tool_name"),
                "decision": decision.get("decision", "allow") if decision else "allow",
                "reason": decision.get("reason", "")[:200] if decision else "",
                "session_id": payload.get("session_id", "")[:16],
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

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    cwd = payload.get("cwd", os.getcwd())

    for check in (
        lambda: blacklist_check(tool_name, tool_input),
        lambda: scope_creep_check(tool_name, tool_input, cwd),
        lambda: failure_streak_check(tool_name),
    ):
        result = check()
        if result and result.get("decision") == "block":
            log_entry(payload, result)
            print(json.dumps(result, ensure_ascii=False))
            sys.exit(0)

    log_entry(payload, None)
    sys.exit(0)


if __name__ == "__main__":
    main()

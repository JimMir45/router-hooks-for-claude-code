#!/usr/bin/env python3
"""
PostToolUseFailure hook: track consecutive failures per tool.
Writes to ~/.claude/router-logs/failure-streak.log so runtime-guard.py
can detect 3-strike circuit-break per autonomy-rules.md §fallback.

Each line: {ts, tool_name, error, session_id}
"""
import json
import sys
import time
from pathlib import Path

LOG = Path.home() / ".claude" / "router-logs" / "failure-streak.log"
LOG.parent.mkdir(parents=True, exist_ok=True)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    error_msg = str(payload.get("tool_response", {}).get("error", ""))[:300]
    # Skip phantom failures (no error message, hook signal only)
    if not error_msg.strip():
        sys.exit(0)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tool_name": payload.get("tool_name", ""),
        "error": error_msg,
        "session_id": payload.get("session_id", "")[:16],
    }
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # Trim to last 200 lines so file doesn't grow unbounded
        lines = LOG.read_text().splitlines()
        if len(lines) > 200:
            LOG.write_text("\n".join(lines[-200:]) + "\n")
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()

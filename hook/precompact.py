#!/usr/bin/env python3
"""
PreCompact hook: capture key state before Claude Code auto-compacts the session.

Reads from stdin:
  {
    "session_id": "...",
    "transcript_path": "/path/to/conv.jsonl",
    "trigger": "auto" | "manual",
    "custom_instructions": "..."  (manual only)
  }

Writes a Markdown snapshot to:
  ~/.claude/precompact-snapshots/<ts>-<session_id_short>.md

Captures:
  - cwd (resolved via session transcript metadata)
  - active-plan.md (if exists)
  - bg-pids.txt (if exists)
  - last 10 user/assistant exchanges from transcript (for "what was being worked on")
  - timestamp + trigger reason

Exit 0 always (never blocks compact).
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

SNAPSHOTS_DIR = Path.home() / ".claude" / "precompact-snapshots"
INDEX_FILE = SNAPSHOTS_DIR / "index.jsonl"
MAX_RECENT_TURNS = 10
MAX_ACTIVE_PLAN_BYTES = 50_000
MAX_TRANSCRIPT_BYTES_TO_SCAN = 2_000_000  # 2MB tail


def _safe_read(p: Path, limit: int = MAX_ACTIVE_PLAN_BYTES) -> str:
    try:
        b = p.read_bytes()
    except Exception:
        return ""
    if len(b) > limit:
        b = b[-limit:]
        prefix = b"...(truncated to last %d bytes)...\n\n" % limit
        b = prefix + b
    try:
        return b.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_recent_turns(transcript_path: str) -> list:
    if not transcript_path or not os.path.exists(transcript_path):
        return []
    try:
        size = os.path.getsize(transcript_path)
        offset = max(0, size - MAX_TRANSCRIPT_BYTES_TO_SCAN)
        with open(transcript_path, "rb") as fh:
            fh.seek(offset)
            data = fh.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    turns = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        msg = o.get("message") or {}
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        chunks = []
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t == "text":
                    chunks.append(c.get("text", ""))
                elif t == "tool_use":
                    chunks.append(f"[TOOL:{c.get('name', '?')}]")
        else:
            chunks.append(str(content))
        text = " ".join(x for x in chunks if x).strip()
        if not text:
            continue
        if text.startswith("<local-command") or text.startswith("<command-"):
            continue
        ts = (o.get("timestamp") or "")[:19]
        turns.append({"ts": ts, "role": role, "text": text})
    return turns[-MAX_RECENT_TURNS:]


def _find_cwd(transcript_path: str) -> str:
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path) as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("cwd"):
                    return o["cwd"]
    except Exception:
        pass
    return ""


def _extract_markers(plan_text: str) -> dict:
    deferred = []
    you_verify = []
    for line in plan_text.splitlines():
        ls = line.strip()
        if "[deferred]" in ls.lower():
            deferred.append(ls)
        if "[you-verify]" in ls.lower() or "[你验]" in ls:
            you_verify.append(ls)
    return {"deferred": deferred, "you_verify": you_verify}


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}
    session_id = payload.get("session_id") or ""
    transcript_path = payload.get("transcript_path") or ""
    trigger = payload.get("trigger") or "unknown"
    custom_instructions = payload.get("custom_instructions") or ""

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts_iso = datetime.now().strftime("%Y%m%dT%H%M%S")
    sid_short = (session_id or "anon")[:16]
    snap_path = SNAPSHOTS_DIR / f"{ts_iso}-{sid_short}.md"

    cwd = _find_cwd(transcript_path)
    project_root = Path(cwd) if cwd else None
    active_plan_text = ""
    bg_pids_text = ""
    markers = {"deferred": [], "you_verify": []}
    if project_root and project_root.is_dir():
        ap = project_root / ".claude" / "active-plan.md"
        if ap.is_file():
            active_plan_text = _safe_read(ap)
            markers = _extract_markers(active_plan_text)
        bp = project_root / ".claude" / "bg-pids.txt"
        if bp.is_file():
            bg_pids_text = _safe_read(bp, limit=10_000)

    recent_turns = _extract_recent_turns(transcript_path)

    lines = []
    lines.append(f"# PreCompact Snapshot")
    lines.append("")
    lines.append(f"- **timestamp**: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- **session_id**: `{session_id}`")
    lines.append(f"- **trigger**: {trigger}")
    if custom_instructions:
        lines.append(f"- **custom_instructions**: {custom_instructions}")
    lines.append(f"- **cwd**: `{cwd or '(unknown)'}`")
    lines.append(f"- **transcript**: `{transcript_path or '(unknown)'}`")
    lines.append("")

    if markers["deferred"]:
        lines.append("## ⏸ Deferred decisions(待处理)")
        for m in markers["deferred"]:
            lines.append(f"- {m}")
        lines.append("")
    if markers["you_verify"]:
        lines.append("## ✋ Pending [you-verify] items")
        for m in markers["you_verify"]:
            lines.append(f"- {m}")
        lines.append("")

    if active_plan_text:
        lines.append("## 📋 active-plan.md (snapshot)")
        lines.append("")
        lines.append("```markdown")
        lines.append(active_plan_text)
        lines.append("```")
        lines.append("")
    else:
        lines.append("## 📋 active-plan.md")
        lines.append("_(none found in this cwd)_")
        lines.append("")

    if bg_pids_text:
        lines.append("## 🔧 Background processes (bg-pids.txt)")
        lines.append("")
        lines.append("```")
        lines.append(bg_pids_text)
        lines.append("```")
        lines.append("")

    if recent_turns:
        lines.append(f"## 💬 Last {len(recent_turns)} user/assistant turns")
        lines.append("")
        for t in recent_turns:
            text = t["text"][:500].replace("\n", " ")
            lines.append(f"- **[{t['ts']}] {t['role']}**: {text}")
        lines.append("")

    lines.append("---")
    lines.append("_To resume: read this file, then check `.claude/active-plan.md` in the cwd above for current step._")

    snap_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        with INDEX_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "session_id": session_id,
                "trigger": trigger,
                "cwd": cwd,
                "snapshot": str(snap_path),
                "has_active_plan": bool(active_plan_text),
                "deferred_count": len(markers["deferred"]),
                "you_verify_count": len(markers["you_verify"]),
                "recent_turns": len(recent_turns),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)

#!/usr/bin/env python3
"""Replace bd's BEADS INTEGRATION block + Session Completion section in each
project CLAUDE.md with a lighter version that doesn't conflict with the user's
existing systems (TodoWrite, MEMORY.md, prism's active-plan.md).
"""
import re
import shutil
from datetime import datetime
from pathlib import Path

NEW_BEADS_BLOCK = """<!-- BEGIN BEADS INTEGRATION v:2-friendly -->
## Beads Issue Tracker (memory layer)

This project uses **bd (beads)** for persistent project memory + issue tracking.

### How it fits with other tools

| Tool | Purpose | When to use |
|---|---|---|
| `bd remember "..."` | Long-term project facts/decisions/bans | Auto-captured by router.py from memorable user signals |
| `bd ready / bd show / bd update` | Issue tracking (optional) | When tracking multi-step work as issues |
| `TodoWrite / TaskCreate` | In-session ephemeral tasks | Current session todos (parallel to bd, not replacing) |
| `.claude/active-plan.md` | Current task progress (prism) | When following a multi-step plan |
| `~/.claude/projects/.../memory/MEMORY.md` | Global cross-project soft memory | Auto-managed (don't disable) |

**bd 跟其他层是补充关系,不是替代。** Each tool has its own scope.

### Quick reference

```bash
bd memories             # list current memories (auto-injected at session start)
bd remember "..."       # add a memory (router.py auto-does this for you)
bd ready                # show issues with no blockers (optional)
```

<!-- END BEADS INTEGRATION -->
"""

PROJECTS_DIR = Path("/Users/jiangyi/LS/Project")


def fix_one(claudemd: Path) -> str:
    text = claudemd.read_text()

    # Replace BEADS INTEGRATION block
    pat = re.compile(
        r"<!-- BEGIN BEADS INTEGRATION.*?<!-- END BEADS INTEGRATION -->\n?",
        re.DOTALL,
    )
    m = pat.search(text)
    if not m:
        return "no_beads_block"
    text = pat.sub(NEW_BEADS_BLOCK, text)

    # Remove "Session Completion" section (mandatory git-push workflow conflicts with our flow).
    # Match "## Session Completion" until the next top-level "## " heading or EOF.
    session_pat = re.compile(
        r"## Session Completion\n.*?(?=\n## |\Z)",
        re.DOTALL,
    )
    text = session_pat.sub("", text)

    claudemd.write_text(text)
    return "ok"


def main():
    fixed = 0
    skipped = 0
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        cm = d / "CLAUDE.md"
        if not cm.exists():
            continue
        # Backup
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = cm.with_suffix(f".md.bak-{ts}-pre-fix")
        shutil.copy2(cm, bak)
        result = fix_one(cm)
        if result == "ok":
            fixed += 1
            print(f"  [ok]   {d.name}")
        else:
            skipped += 1
            bak.unlink()  # no change → remove unneeded backup
            print(f"  [skip] {d.name}: {result}")
    print(f"\nFixed {fixed} files, skipped {skipped}")


if __name__ == "__main__":
    main()

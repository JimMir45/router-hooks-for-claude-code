#!/usr/bin/env python3
"""Migrate router-hook bundle from global settings.json to per-project settings.json.

Path B: shared code lives in ~/LS/Project/router-eval-share/hook/*.py;
each project's `.claude/settings.json` registers them. Global hooks removed.

Operations:
  1. For each known project:
     - Read <project>/.claude/settings.json (create if missing)
     - Add 5 hook entries (idempotent — skip if already present)
     - Write back
  2. Optionally clean global ~/.claude/settings.json (--clean-global)
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECTS = [
    "AI-Rec",
    "ai-content-platform",
    "car-ktv",
    "dazi",
    "instinct",
    "music-rec-engine",
    "music-rec-offline-eval-platform",
    "music-score",
    "music-score-factory",
    "router-eval-share",
    "sensevoice",
    "xiaoyi",
]

HOOK_DIR = "/Users/jiangyi/LS/Project/router-eval-share/hook"

# (event, command, matcher)
HOOK_ENTRIES = [
    ("UserPromptSubmit", f"python3 {HOOK_DIR}/router.py", "*"),
    ("PreToolUse",       f"python3 {HOOK_DIR}/runtime-guard.py", "*"),
    ("PostToolUseFailure", f"python3 {HOOK_DIR}/failure-tracker.py", "*"),
    ("Stop",             f"python3 {HOOK_DIR}/completion-check.py", "*"),
    ("PreCompact",       f"python3 {HOOK_DIR}/precompact.py", "*"),
]

PROJECT_ROOT = Path("/Users/jiangyi/LS/Project")


def add_hook(settings: dict, event: str, command: str, matcher: str) -> bool:
    """Add hook to settings (idempotent). Returns True if added, False if already present."""
    hooks_root = settings.setdefault("hooks", {})
    arr = hooks_root.setdefault(event, [])
    # Check if this exact command already present in any entry's hooks list
    for entry in arr:
        for h in entry.get("hooks", []):
            if h.get("command", "").strip() == command.strip():
                return False
    arr.append({
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command}],
    })
    return True


def remove_hook(settings: dict, command_prefix: str) -> int:
    """Remove any hook entry whose command starts with prefix. Returns count removed."""
    removed = 0
    hooks_root = settings.get("hooks", {})
    for event, entries in list(hooks_root.items()):
        new_entries = []
        for entry in entries:
            new_hooks = [h for h in entry.get("hooks", []) if not h.get("command", "").strip().startswith(command_prefix.strip())]
            if new_hooks:
                if len(new_hooks) != len(entry.get("hooks", [])):
                    removed += len(entry.get("hooks", [])) - len(new_hooks)
                    entry["hooks"] = new_hooks
                new_entries.append(entry)
            else:
                removed += len(entry.get("hooks", []))
        hooks_root[event] = new_entries
    return removed


def install_per_project():
    for proj in PROJECTS:
        pdir = PROJECT_ROOT / proj
        if not pdir.is_dir():
            print(f"[skip] {proj}: dir not found")
            continue
        claude_dir = pdir / ".claude"
        claude_dir.mkdir(exist_ok=True)
        settings_path = claude_dir / "settings.json"
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
            except Exception as e:
                print(f"[skip] {proj}: settings.json invalid ({e})")
                continue
        else:
            settings = {}
        # Backup
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = settings_path.with_suffix(f".json.bak-{ts}-pre-B")
        if settings_path.exists():
            shutil.copy2(settings_path, bak)
        added = 0
        for event, command, matcher in HOOK_ENTRIES:
            if add_hook(settings, event, command, matcher):
                added += 1
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=4))
        print(f"[ok]   {proj}: +{added} hooks added, settings.json written")


def clean_global():
    p = Path.home() / ".claude" / "settings.json"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = p.with_suffix(f".json.bak-{ts}-pre-B")
    shutil.copy2(p, bak)
    print(f"[backup] {bak}")
    settings = json.loads(p.read_text())
    total = 0
    for cmd in [e[1] for e in HOOK_ENTRIES]:
        # cmd is like "python3 /Users/jiangyi/LS/Project/router-eval-share/hook/router.py"
        n = remove_hook(settings, cmd)
        total += n
        print(f"  removed {n} entries matching: {cmd}")
    p.write_text(json.dumps(settings, ensure_ascii=False, indent=4))
    print(f"[ok] global settings.json: {total} hooks removed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="Install hooks to each project's .claude/settings.json")
    ap.add_argument("--clean-global", action="store_true", help="Remove our 5 hooks from ~/.claude/settings.json")
    args = ap.parse_args()
    if not (args.install or args.clean_global):
        ap.print_help()
        sys.exit(1)
    if args.install:
        install_per_project()
    if args.clean_global:
        clean_global()


if __name__ == "__main__":
    main()

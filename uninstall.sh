#!/usr/bin/env bash
# uninstall.sh — Remove Router Hook from Claude Code
#
# Usage:
#   ./uninstall.sh              # interactive uninstall
#   ./uninstall.sh --dry-run    # preview what would be removed
#   ./uninstall.sh --force      # no confirmation prompt

set -euo pipefail

HOOK_DIR="${ROUTER_HOOK_DIR:-$HOME/.router-hook}"
CONFIG_DIR="${ROUTER_HOOK_CONFIG:-$HOME/.config/router-hook}"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
LOCAL_BIN="$HOME/.local/bin"

DRY_RUN=false
FORCE=false

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
dryrun()  { echo -e "${YELLOW}[DRY]${NC}  Would: $*"; }

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --force)   FORCE=true ;;
    -h|--help)
      grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Router Hook — Uninstaller"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if $DRY_RUN; then
  warn "DRY RUN mode — nothing will be deleted"
  echo ""
fi

if ! $FORCE && ! $DRY_RUN; then
  echo "This will:"
  echo "  - Remove hook registrations from ~/.claude/settings.json"
  echo "  - Remove ~/.router-hook/ directory"
  echo "  - Remove ~/.local/bin/router-mode symlink"
  echo "  - Optionally remove ~/.config/router-hook/ (config + keys)"
  echo "  - Remove the router hard rule from ~/.claude/CLAUDE.md"
  echo ""
  read -rp "Continue? [y/N] " confirm
  if [[ "${confirm,,}" != "y" ]]; then
    echo "Aborted."
    exit 0
  fi
  echo ""
fi

# ── 1. Remove hooks from settings.json ───────────────────────────────────────
info "Removing hook registrations from settings.json..."

PYTHON_REMOVE=$(cat <<'PYEOF'
import json
import sys

settings_path = sys.argv[1]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except Exception:
    print("settings.json not found or invalid — skipping")
    sys.exit(0)

hooks = settings.get("hooks", [])
ROUTER_COMMANDS = [
    "router.py",
    "runtime-guard.py",
    "failure-tracker.py",
    "completion-check.py",
]

def is_router_hook(hook_entry):
    for sub in hook_entry.get("hooks", []):
        cmd = sub.get("command", "")
        if any(rc in cmd for rc in ROUTER_COMMANDS):
            return True
    return False

original_count = len(hooks)
new_hooks = [h for h in hooks if not is_router_hook(h)]
removed = original_count - len(new_hooks)

settings["hooks"] = new_hooks
with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"Removed {removed} router hook registration(s) from settings.json")
PYEOF
)

if $DRY_RUN; then
  dryrun "Remove 4 router hook entries from $CLAUDE_SETTINGS"
elif [ -f "$CLAUDE_SETTINGS" ]; then
  python3 - "$CLAUDE_SETTINGS" <<< "$PYTHON_REMOVE"
  success "Hook registrations removed"
else
  warn "settings.json not found — skipping"
fi

# ── 2. Remove router hard rule from CLAUDE.md ─────────────────────────────────
info "Removing router hard rule from CLAUDE.md..."

PYTHON_CLAUDEMD=$(cat <<'PYEOF'
import sys
import re

path = sys.argv[1]
try:
    with open(path) as f:
        content = f.read()
except FileNotFoundError:
    print("CLAUDE.md not found — skipping")
    sys.exit(0)

# Remove the block from marker to next top-level ## (or end of file)
pattern = r'\n## Router Hook Hard Rule\n.*?(?=\n## |\Z)'
new_content = re.sub(pattern, '', content, flags=re.DOTALL)
if new_content != content:
    with open(path, 'w') as f:
        f.write(new_content)
    print("Router hard rule removed from CLAUDE.md")
else:
    print("Router hard rule not found in CLAUDE.md — skipping")
PYEOF
)

if $DRY_RUN; then
  dryrun "Remove '## Router Hook Hard Rule' block from $CLAUDE_MD"
elif [ -f "$CLAUDE_MD" ]; then
  python3 - "$CLAUDE_MD" <<< "$PYTHON_CLAUDEMD"
else
  warn "CLAUDE.md not found — skipping"
fi

# ── 3. Remove hook directory ──────────────────────────────────────────────────
info "Removing hook directory: $HOOK_DIR ..."

if $DRY_RUN; then
  dryrun "rm -rf $HOOK_DIR"
elif [ -d "$HOOK_DIR" ]; then
  rm -rf "$HOOK_DIR"
  success "Removed: $HOOK_DIR"
else
  warn "Hook directory not found: $HOOK_DIR"
fi

# ── 4. Remove router-mode symlink ─────────────────────────────────────────────
info "Removing router-mode symlink..."

if $DRY_RUN; then
  dryrun "rm -f $LOCAL_BIN/router-mode"
elif [ -L "$LOCAL_BIN/router-mode" ]; then
  rm -f "$LOCAL_BIN/router-mode"
  success "Removed: $LOCAL_BIN/router-mode"
else
  warn "Symlink not found: $LOCAL_BIN/router-mode"
fi

# ── 5. Optionally remove config directory ─────────────────────────────────────
if $DRY_RUN; then
  dryrun "Ask user whether to remove $CONFIG_DIR (contains keys.json)"
elif [ -d "$CONFIG_DIR" ]; then
  echo ""
  if $FORCE; then
    rm -rf "$CONFIG_DIR"
    success "Removed config directory: $CONFIG_DIR"
  else
    read -rp "Remove config directory (contains your API keys)? $CONFIG_DIR [y/N] " confirm_cfg
    if [[ "${confirm_cfg,,}" == "y" ]]; then
      rm -rf "$CONFIG_DIR"
      success "Removed: $CONFIG_DIR"
    else
      info "Kept: $CONFIG_DIR (your keys are safe)"
    fi
  fi
fi

# ── 6. Optionally restore settings.json backup ────────────────────────────────
if ! $DRY_RUN; then
  latest_bak=$(ls -t "$CLAUDE_SETTINGS".bak.* 2>/dev/null | head -1 || echo "")
  if [ -n "$latest_bak" ]; then
    echo ""
    echo "Found backup: $latest_bak"
    if ! $FORCE; then
      read -rp "Restore this backup? [y/N] " restore
      if [[ "${restore,,}" == "y" ]]; then
        cp "$latest_bak" "$CLAUDE_SETTINGS"
        success "Restored: $CLAUDE_SETTINGS from $latest_bak"
      else
        info "Kept current settings.json (hooks removed, other settings intact)"
      fi
    fi
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Uninstall complete.${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Start a new Claude Code session to confirm hooks are gone."
echo ""

#!/usr/bin/env bash
# install.sh — Router Hook for Claude Code
# Installs 4 Claude Code hooks that add a 5-layer intent router + runtime guards.
#
# Usage:
#   ./install.sh              # normal install
#   ./install.sh --dry-run    # preview what would happen, no changes
#   ./install.sh --uninstall  # delegate to uninstall.sh
#
# What it does:
#   1. Check dependencies (python3, claude)
#   2. Copy hook files to ~/.router-hook/ (or custom ROUTER_HOOK_DIR)
#   3. Create ~/.config/router-hook/keys.json.example
#   4. Prompt user to fill in API key
#   5. Backup ~/.claude/settings.json
#   6. Register 4 hooks in settings.json (idempotent)
#   7. Append router hard rule to ~/.claude/CLAUDE.md (idempotent)
#   8. Create ~/.claude/reports/ directory
#   9. Run smoke test
#  10. Print success message

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
HOOK_DIR="${ROUTER_HOOK_DIR:-$HOME/.router-hook}"
CONFIG_DIR="${ROUTER_HOOK_CONFIG:-$HOME/.config/router-hook}"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
REPORTS_DIR="$HOME/.claude/reports"
LOG_DIR="$HOME/.claude/router-logs"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=false
SKIP_KEY_PROMPT=false

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERR]${NC}  $*" >&2; }
dryrun()  { echo -e "${YELLOW}[DRY]${NC}  Would: $*"; }

# ── Args ──────────────────────────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=true ;;
    --no-key)    SKIP_KEY_PROMPT=true ;;
    --uninstall) exec "$(dirname "$0")/uninstall.sh" ;;
    -h|--help)
      grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

if $DRY_RUN; then
  warn "DRY RUN mode — no files will be modified"
  echo ""
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Router Hook for Claude Code — Installer"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Step 1: Check dependencies ────────────────────────────────────────────────
info "Step 1/9: Checking dependencies..."

check_dep() {
  local cmd=$1
  local install_hint=$2
  if command -v "$cmd" &>/dev/null; then
    success "$cmd found: $(command -v "$cmd")"
  else
    error "$cmd not found. $install_hint"
    exit 1
  fi
}

check_dep python3  "Install Python 3.8+ from https://python.org"
check_dep claude   "Install Claude Code from https://claude.ai/code"

python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
info "Python version: $python_version"

# ── Step 2: Copy hook files ───────────────────────────────────────────────────
info "Step 2/9: Installing hook files to $HOOK_DIR ..."

HOOK_FILES=(
  "hook/router.py"
  "hook/runtime-guard.py"
  "hook/failure-tracker.py"
  "hook/completion-check.py"
  "hook/render-report.py"
  "hook/cleanup-reports.py"
  "hook/router-mode"
)

if $DRY_RUN; then
  dryrun "mkdir -p $HOOK_DIR"
  for f in "${HOOK_FILES[@]}"; do
    dryrun "cp $SCRIPT_DIR/$f $HOOK_DIR/$(basename "$f")"
  done
else
  mkdir -p "$HOOK_DIR"
  for f in "${HOOK_FILES[@]}"; do
    src="$SCRIPT_DIR/$f"
    dst="$HOOK_DIR/$(basename "$f")"
    if [ -f "$src" ]; then
      cp "$src" "$dst"
      chmod +x "$dst"
      success "Installed: $dst"
    else
      error "Source not found: $src"
      exit 1
    fi
  done
fi

# Install router-mode to PATH
LOCAL_BIN="$HOME/.local/bin"
if $DRY_RUN; then
  dryrun "mkdir -p $LOCAL_BIN && ln -sf $HOOK_DIR/router-mode $LOCAL_BIN/router-mode"
else
  mkdir -p "$LOCAL_BIN"
  ln -sf "$HOOK_DIR/router-mode" "$LOCAL_BIN/router-mode"
  success "Symlinked router-mode to $LOCAL_BIN/router-mode"
  if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
    warn "$LOCAL_BIN is not in your PATH."
    warn "Add this to your ~/.zshrc or ~/.bashrc:"
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
fi

# ── Step 3: Create config directory and keys template ─────────────────────────
info "Step 3/9: Setting up config directory at $CONFIG_DIR ..."

if $DRY_RUN; then
  dryrun "mkdir -p $CONFIG_DIR"
  dryrun "cp $SCRIPT_DIR/config/keys.json.example $CONFIG_DIR/keys.json.example"
  dryrun "echo silent > $CONFIG_DIR/mode"
else
  mkdir -p "$CONFIG_DIR"
  cp "$SCRIPT_DIR/config/keys.json.example" "$CONFIG_DIR/keys.json.example"
  # Set default mode to silent (only alert on action-required)
  if [ ! -f "$CONFIG_DIR/mode" ]; then
    echo "silent" > "$CONFIG_DIR/mode"
    success "Set router mode to: silent (default)"
  else
    info "Router mode already set to: $(cat "$CONFIG_DIR/mode")"
  fi
fi

# ── Step 4: Prompt for API key ────────────────────────────────────────────────
info "Step 4/9: API key configuration..."

if ! $DRY_RUN && ! $SKIP_KEY_PROMPT; then
  KEYS_FILE="$CONFIG_DIR/keys.json"
  if [ -f "$KEYS_FILE" ]; then
    info "keys.json already exists at $KEYS_FILE"
  else
    echo ""
    echo "  The router uses any OpenAI-compatible API endpoint."
    echo "  Examples: OpenAI, Together.ai, local Ollama, etc."
    echo ""
    echo "  See config/keys.json.example for full configuration options."
    echo ""
    echo "  Option A — Quick setup (OpenAI):"
    echo "    Enter your API key now, or press Enter to configure manually later."
    echo ""
    read -rp "  OpenAI API key (or press Enter to skip): " user_key
    if [ -n "$user_key" ]; then
      cat > "$KEYS_FILE" <<KEYS_EOF
{
  "primary": {
    "name": "openai",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "$user_key"
  },
  "fallback": {
    "name": "openai-fallback",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "$user_key"
  }
}
KEYS_EOF
      chmod 600 "$KEYS_FILE"
      success "Created $KEYS_FILE with your key"
    else
      warn "Skipped. Copy and fill in the example:"
      warn "  cp $CONFIG_DIR/keys.json.example $KEYS_FILE"
      warn "  then edit: $KEYS_FILE"
    fi
  fi
else
  $DRY_RUN && dryrun "Prompt user for API key -> create $CONFIG_DIR/keys.json"
  $SKIP_KEY_PROMPT && info "Skipping key prompt (--no-key)"
fi

# ── Step 5: Backup settings.json ──────────────────────────────────────────────
info "Step 5/9: Backing up Claude settings..."

TIMESTAMP=$(date +%Y%m%d%H%M%S)

if $DRY_RUN; then
  dryrun "cp $CLAUDE_SETTINGS $CLAUDE_SETTINGS.bak.$TIMESTAMP"
else
  if [ -f "$CLAUDE_SETTINGS" ]; then
    cp "$CLAUDE_SETTINGS" "$CLAUDE_SETTINGS.bak.$TIMESTAMP"
    success "Backed up to: $CLAUDE_SETTINGS.bak.$TIMESTAMP"
  else
    warn "settings.json not found at $CLAUDE_SETTINGS; will create it"
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    echo '{}' > "$CLAUDE_SETTINGS"
  fi
fi

# ── Step 6: Register hooks in settings.json ───────────────────────────────────
info "Step 6/9: Registering hooks in settings.json..."

PYTHON_SCRIPT=$(cat <<'PYEOF'
import json
import sys
import os
from pathlib import Path

settings_path = sys.argv[1]
hook_dir = sys.argv[2]

try:
    with open(settings_path) as f:
        settings = json.load(f)
except Exception:
    settings = {}

if not isinstance(settings, dict):
    settings = {}

hooks = settings.setdefault("hooks", [])

NEW_HOOKS = [
    {
        "description": "Router: 5-layer intent router (entry)",
        "event": "UserPromptSubmit",
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {hook_dir}/router.py"
        }]
    },
    {
        "description": "Router: runtime guard + circuit-breaker (pre-tool)",
        "event": "PreToolUse",
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {hook_dir}/runtime-guard.py"
        }]
    },
    {
        "description": "Router: failure tracker (post-tool failure)",
        "event": "PostToolUseFailure",
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {hook_dir}/failure-tracker.py"
        }]
    },
    {
        "description": "Router: completion sanity check (stop)",
        "event": "Stop",
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"python3 {hook_dir}/completion-check.py"
        }]
    },
]

def hook_already_registered(existing_hooks, new_hook):
    """Check if a hook with the same command is already registered."""
    new_cmd = new_hook["hooks"][0]["command"]
    for h in existing_hooks:
        for sub in h.get("hooks", []):
            if sub.get("command") == new_cmd:
                return True
    return False

added = 0
for new_hook in NEW_HOOKS:
    if not hook_already_registered(hooks, new_hook):
        hooks.append(new_hook)
        added += 1
        print(f"  + Registered: {new_hook['description']}")
    else:
        print(f"  ~ Already registered: {new_hook['description']}")

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
    f.write("\n")

print(f"Done: {added} new hooks added, settings saved.")
PYEOF
)

if $DRY_RUN; then
  dryrun "python3 <register-hooks-script> $CLAUDE_SETTINGS $HOOK_DIR"
  echo "  Would register:"
  echo "  + UserPromptSubmit -> router.py"
  echo "  + PreToolUse       -> runtime-guard.py"
  echo "  + PostToolUseFailure -> failure-tracker.py"
  echo "  + Stop             -> completion-check.py"
else
  python3 - "$CLAUDE_SETTINGS" "$HOOK_DIR" <<< "$PYTHON_SCRIPT"
  success "Hooks registered in settings.json"
fi

# ── Step 7: Append router rule to CLAUDE.md ───────────────────────────────────
info "Step 7/9: Adding router rule to CLAUDE.md..."

ROUTER_RULE_MARKER="## Router Hook Hard Rule"

add_claude_md_rule() {
  local claude_md="$1"
  mkdir -p "$(dirname "$claude_md")"
  if [ -f "$claude_md" ] && grep -qF "$ROUTER_RULE_MARKER" "$claude_md" 2>/dev/null; then
    info "Router rule already in CLAUDE.md, skipping"
    return
  fi
  cat >> "$claude_md" <<'RULE_EOF'

## Router Hook Hard Rule

A `UserPromptSubmit` hook injects a `🧭 Router → ...` block into your system context at the start of every turn (when the prompt isn't fast-pathed).

**When the injection contains an `[ACTION REQUIRED]` line, you MUST follow it BEFORE generating any response:**

1. **First**: invoke the Skill tool with the exact name(s) listed in the `[ACTION REQUIRED]` line.
2. **Then**: produce your response using the loaded skill's workflow.

**Rules:**
- The router is the single source of truth for framework selection. Do NOT skip the `[ACTION REQUIRED]` step even if you think you can answer without it.
- If multiple Skill options are listed, pick the one that best matches the task and invoke exactly one.
- If `[ACTION REQUIRED]` is absent (CC routing or OFFLINE_TOPIC), respond natively — do not invoke any framework skill.
- If the router decision shows `human_confirm_required: true`, surface that to the user and pause for confirmation before any tool invocation that could execute the action.
- The user can override with "switch to X" / "use native CC" — when overridden, ignore the router's framework and follow the user's explicit choice.

**Why this exists**: without explicit invocation, you tend to *imitate* a framework's style instead of *activating* it.

RULE_EOF
  success "Added router rule to: $claude_md"
}

if $DRY_RUN; then
  dryrun "Append router hard rule to $CLAUDE_MD (if not already present)"
else
  add_claude_md_rule "$CLAUDE_MD"
fi

# ── Step 8: Create reports directory ─────────────────────────────────────────
info "Step 8/9: Creating directory structure..."

if $DRY_RUN; then
  dryrun "mkdir -p $REPORTS_DIR $LOG_DIR"
else
  mkdir -p "$REPORTS_DIR" "$LOG_DIR"
  success "Created: $REPORTS_DIR"
  success "Created: $LOG_DIR"
fi

# ── Step 9: Smoke test ────────────────────────────────────────────────────────
info "Step 9/9: Running smoke test..."

run_smoke_test() {
  local failures=0

  # Test 1: router.py syntax
  if python3 -m py_compile "$HOOK_DIR/router.py" 2>/dev/null; then
    success "router.py: syntax OK"
  else
    error "router.py: syntax error"
    ((failures++)) || true
  fi

  # Test 2: runtime-guard.py syntax
  if python3 -m py_compile "$HOOK_DIR/runtime-guard.py" 2>/dev/null; then
    success "runtime-guard.py: syntax OK"
  else
    error "runtime-guard.py: syntax error"
    ((failures++)) || true
  fi

  # Test 3: completion-check.py syntax
  if python3 -m py_compile "$HOOK_DIR/completion-check.py" 2>/dev/null; then
    success "completion-check.py: syntax OK"
  else
    error "completion-check.py: syntax error"
    ((failures++)) || true
  fi

  # Test 4: router.py runs with empty stdin (should exit 0 cleanly in silent mode)
  local test_payload='{"prompt":"","session_id":"test"}'
  if echo "$test_payload" | python3 "$HOOK_DIR/router.py" >/dev/null 2>&1; then
    success "router.py: runs cleanly with empty prompt"
  else
    warn "router.py: non-zero exit with empty prompt (may be OK if keys not configured)"
  fi

  # Test 5: runtime-guard.py runs cleanly
  local guard_payload='{"tool_name":"Read","tool_input":{"file_path":"/tmp/test"},"cwd":"/tmp"}'
  if echo "$guard_payload" | python3 "$HOOK_DIR/runtime-guard.py" >/dev/null 2>&1; then
    success "runtime-guard.py: runs cleanly with safe tool"
  else
    warn "runtime-guard.py: unexpected output for safe tool"
  fi

  # Test 6: router-mode script works
  if "$HOOK_DIR/router-mode" status >/dev/null 2>&1; then
    success "router-mode: works (current: $(cat "$CONFIG_DIR/mode" 2>/dev/null || echo unknown))"
  else
    warn "router-mode: could not read mode"
  fi

  return $failures
}

if $DRY_RUN; then
  dryrun "Run syntax checks and basic smoke tests on all 4 hook scripts"
else
  if run_smoke_test; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "  ${GREEN}Installation complete!${NC}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  Next steps:"
    echo ""
    if [ ! -f "$CONFIG_DIR/keys.json" ]; then
      echo "  1. Configure your API key (REQUIRED):"
      echo "     cp $CONFIG_DIR/keys.json.example $CONFIG_DIR/keys.json"
      echo "     # then edit keys.json with your actual API key"
      echo ""
    fi
    echo "  2. Start a new Claude Code session:"
    echo "     claude"
    echo ""
    echo "  3. Type any prompt — the router will classify it automatically."
    echo "     In silent mode, you'll only see output when a framework action is needed."
    echo ""
    echo "  4. Switch router mode anytime:"
    echo "     router-mode auto    # verbose: see every routing decision"
    echo "     router-mode silent  # quiet: only signal when needed (default)"
    echo "     router-mode off     # disable completely"
    echo ""
    echo "  Logs: $LOG_DIR/router.log"
    echo ""
  else
    echo ""
    warn "Installation completed with warnings. Check errors above."
    echo ""
  fi
fi

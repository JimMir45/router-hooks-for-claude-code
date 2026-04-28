# Installation Guide

Detailed 5-step installation for Router Hook for Claude Code.

---

## Step 1: Install Prerequisites

### Claude Code (required)

Install the Claude Code CLI if you haven't already:

```bash
# Check if already installed
claude --version

# Install via npm (if not installed)
npm install -g @anthropic-ai/claude-code
```

Visit [claude.ai/code](https://claude.ai/code) for the latest installation instructions.

### Python 3.8+ (usually pre-installed)

```bash
python3 --version
# Should show: Python 3.8.x or higher
```

If not installed: [python.org/downloads](https://www.python.org/downloads/)

Router Hook uses only Python standard library — no `pip install` required.

---

## Step 2: Clone the Repository

```bash
git clone https://github.com/YOUR_ORG/router-hook
cd router-hook
```

Verify the hook files are present:

```bash
ls hook/
# Should show: router.py  runtime-guard.py  failure-tracker.py  completion-check.py  render-report.py  cleanup-reports.py  router-mode
```

---

## Step 3: Configure Your API Key

The router uses a small LLM (gpt-4o-mini or equivalent) to classify prompts. You need an API key for any OpenAI-compatible endpoint.

**Option A — Interactive (recommended):**

The installer will prompt you for an API key. Just run `./install.sh` and enter your key when asked.

**Option B — Manual:**

```bash
cp ~/.config/router-hook/keys.json.example ~/.config/router-hook/keys.json
```

Edit `~/.config/router-hook/keys.json`:

```json
{
  "primary": {
    "name": "openai",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "sk-YOUR_ACTUAL_KEY_HERE"
  },
  "fallback": {
    "name": "openai-fallback",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "sk-YOUR_ACTUAL_KEY_HERE"
  }
}
```

**Where to get an API key:**
- OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- Together.ai: [api.together.xyz](https://api.together.xyz) (often cheaper)
- Local: Run [Ollama](https://ollama.ai) locally (free, private, works offline)

**Other compatible providers** — see `config/keys.json.example` for full examples.

---

## Step 4: Run the Installer

```bash
chmod +x install.sh
./install.sh
```

**What the installer does:**

1. Checks for `python3` and `claude` dependencies
2. Copies hook files to `~/.router-hook/`
3. Creates `~/.config/router-hook/keys.json.example`
4. Prompts for your API key (optional — can skip)
5. Backs up `~/.claude/settings.json` with timestamp
6. Registers 4 hooks in `settings.json` (idempotent — safe to run twice)
7. Appends router hard rule to `~/.claude/CLAUDE.md`
8. Creates `~/.claude/reports/` and `~/.claude/router-logs/` directories
9. Runs syntax and smoke tests on all hook scripts
10. Prints next steps

**Preview without making changes:**

```bash
./install.sh --dry-run
```

---

## Step 5: Verify Installation

Start a new Claude Code session and send a prompt:

```bash
claude
```

Type something to Claude, for example:
```
Refactor the auth module to use JWT tokens instead of session cookies
```

**Expected behavior in `auto` mode:**

You should see something like:
```
🧭 Router → Superpowers (5-phase)  (conf 0.82)
   fallback: GS-EngManager
   reason: Multi-file refactoring task with architectural implications

[ACTION REQUIRED] Before responding, invoke Skill("superpowers:using-superpowers") ...
```

**In `silent` mode (default):** You won't see anything unless the router detects a significant routing decision or a blacklisted operation.

**Switch to verbose mode to verify:**

```bash
router-mode auto
```

Then send a few prompts in Claude and you should see routing decisions appear.

**Check the logs:**

```bash
tail -5 ~/.claude/router-logs/router.log | python3 -m json.tool
```

You should see JSON entries with `framework_primary`, `confidence`, and `reason` fields.

---

## Troubleshooting Installation

**"claude: command not found"**
Install Claude Code: `npm install -g @anthropic-ai/claude-code`

**"router-mode: command not found"**
Add `~/.local/bin` to your PATH:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**"no_providers_configured" in logs**
Your `keys.json` is missing or the key is still `REPLACE_WITH_YOUR_KEY`.
Check: `cat ~/.config/router-hook/keys.json`

**Hooks not showing up in Claude**
Restart Claude Code — hooks only activate in new sessions.
Verify: `grep -A3 "router.py" ~/.claude/settings.json`

---

## Uninstall

```bash
./uninstall.sh
```

See `README.md` for details.

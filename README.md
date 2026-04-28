# Router Hook for Claude Code

**English** · [中文](README.zh.md)

A **Claude Code hook system** that automatically classifies every prompt through a 5-layer intent router, routes it to the right AI framework, and enforces runtime guardrails — so Claude spends less time asking you what to do and more time doing it right.

---

## Architecture

Three phases cover the full Agent work lifecycle:

```
Phase 1: Entry              Phase 2: Runtime             Phase 3: Endpoint
UserPromptSubmit            PreToolUse / Failure          Stop
─────────────────           ──────────────────────        ─────────────────
router.py                   runtime-guard.py              completion-check.py
                            failure-tracker.py

You type a prompt           Claude is about to            Claude says "done"
      ↓                     run a tool
5-layer decision tree             ↓                             ↓
picks framework:            Blacklist checks:             Hedging word check:
  SP  → Superpowers         • rm -rf / DROP SQL           • "should be OK"
  GS  → gstack role         • git push --force            • "probably works"
  ECC → deep-research       • email/IM APIs               • unverified claims
  CC  → native Claude       Circuit-breaker:              active-plan check:
                            • 3 consecutive failures      • unchecked self-verify
                            Scope-creep:                    items still exist
                            • files outside active-plan
```

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| [Claude Code](https://claude.ai/code) | Any | The Claude Code CLI (`claude` command) |
| Python 3 | 3.8+ | Standard library only — no pip installs needed |
| API key | — | Any OpenAI-compatible endpoint (OpenAI, Together.ai, local Ollama, etc.) |

---

## Install

```bash
git clone https://github.com/JimMir45/router-hooks-for-claude-code
cd router-hooks-for-claude-code
./install.sh
```

Or one-liner (after you trust the source):

```bash
git clone https://github.com/JimMir45/router-hooks-for-claude-code && cd router-hooks-for-claude-code && ./install.sh
```

The installer is idempotent — safe to run twice. It will not overwrite existing config.

---

## Quickstart (5 lines)

```bash
# 1. After install, configure your API key:
cp ~/.config/router-hook/keys.json.example ~/.config/router-hook/keys.json
# Edit keys.json with your actual key

# 2. Start Claude Code:
claude

# 3. Type any prompt — the router runs automatically in the background.
#    In default 'silent' mode, you'll only see output when a framework is selected.

# 4. See what the router decided last time:
tail -1 ~/.claude/router-logs/router.log | python3 -m json.tool

# 5. Switch modes:
router-mode auto    # verbose: see every routing decision
router-mode silent  # quiet: only alert when needed (default)
router-mode off     # fully disabled
```

---

## Troubleshooting

**Hook not triggering**
- Restart Claude Code (`claude`) — hooks only activate in new sessions.
- Check registration: `cat ~/.claude/settings.json | python3 -m json.tool | grep router`
- Re-run installer: `./install.sh` (idempotent, safe)

**API call failing / router falling back to native CC**
- Check your key: `cat ~/.config/router-hook/keys.json`
- Test endpoint manually:
  ```bash
  curl -s https://api.openai.com/v1/chat/completions \
    -H "Authorization: Bearer YOUR_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
  ```
- Check router log for error details: `tail -5 ~/.claude/router-logs/router.log`

**Hook is blocking something it shouldn't**
- Check runtime-guard log: `tail -10 ~/.claude/router-logs/runtime-guard.log`
- Temporarily disable: `router-mode off`
- If the blacklist regex is too aggressive, open an issue with the specific command.

**I want to turn it off temporarily**
```bash
router-mode off    # disables router + runtime-guard + completion-check
router-mode silent # re-enable (default)
```

**How do I uninstall completely?**
```bash
./uninstall.sh
```
This removes all hooks from `settings.json`, removes `~/.router-hook/`, and removes the router rule from `CLAUDE.md`.

---

## Configuration

### API Provider (`~/.config/router-hook/keys.json`)

Supports any OpenAI-compatible endpoint:

```json
{
  "primary": {
    "name": "openai",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "sk-..."
  },
  "fallback": {
    "name": "openai-fallback",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "sk-..."
  }
}
```

See `config/keys.json.example` for more examples (Together.ai, local Ollama, key-file indirection).

### Router Mode

| Mode | Behavior | When to use |
|------|----------|-------------|
| `silent` | Only shows output when action required (default) | Normal daily use |
| `auto` | Shows routing decision on every prompt | Debugging / evaluating the router |
| `off` | Completely disabled | When you need clean Claude without interference |

---

## Docs

- `docs/router-spec-v3.md` — 5-layer decision tree spec with data analysis
- `docs/autonomy-rules.md` — 2-state workflow + blacklist + verification rules
- `docs/decision-taxonomy.md` — Theoretical backbone (6+4+5 decision axes)
- `INSTALL.md` — Detailed installation guide
- `docs/ARCHITECTURE.md` — Architecture deep-dive

---

## License

MIT — see [LICENSE](LICENSE).

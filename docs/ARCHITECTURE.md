# Architecture

Router Hook for Claude Code operates across three phases of the Agent work lifecycle.

---

## Three-Phase Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 1: Entry                                                              │
│ Hook: UserPromptSubmit → hook/router.py                                     │
│                                                                             │
│  User prompt                                                                │
│      │                                                                      │
│      ▼                                                                      │
│  Fast-path check (< 10 chars → CC, no LLM call)                            │
│      │                                                                      │
│      ▼                                                                      │
│  5-Layer Decision Tree ──── calls gpt-4o-mini ──────────────────────────── │
│      │                      (primary → fallback)                            │
│      ▼                                                                      │
│  Layer 0: Hard Override        (credentials / irreversible ops)             │
│  Layer 0.5: OFFLINE_TOPIC      (non-engineering: finance/legal/lifestyle)   │
│  Layer 1: Complexity           (architecture / complex / simple)            │
│  Layer 2: Vagueness            (product_vague / engineering_vague)          │
│  Layer 3: Intent               (debug / research / feat / chore / ...)      │
│  Layer 4: Default → CC                                                      │
│      │                                                                      │
│      ▼                                                                      │
│  Output framework: SP | GS | ECC | CC                                      │
│  Inject into Claude context:                                                │
│    "🧭 Router → Superpowers  (conf 0.82)"                                  │
│    "[ACTION REQUIRED] invoke Skill(...)"   ← Claude MUST follow this       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 2: Runtime                                                            │
│ Hook: PreToolUse → hook/runtime-guard.py                                    │
│ Hook: PostToolUseFailure → hook/failure-tracker.py                          │
│                                                                             │
│  Claude is about to run a tool (Bash, Edit, Write, ...)                    │
│      │                                                                      │
│      ▼                                                                      │
│  Check 1: Blacklist                                                         │
│    • rm -rf / recursive delete                                              │
│    • SQL DROP / TRUNCATE / DELETE without WHERE                             │
│    • git push --force                                                       │
│    • Outbound email / Slack / IM API calls                                  │
│      │                                                                      │
│      ▼                                                                      │
│  Check 2: Scope creep                                                       │
│    • If .claude/active-plan.md has "Allowed files:" section                 │
│    • Block edits to files outside that list                                 │
│      │                                                                      │
│      ▼                                                                      │
│  Check 3: Circuit breaker                                                   │
│    • failure-tracker.py writes to failure-streak.log on each tool failure  │
│    • runtime-guard.py reads it; 3 consecutive failures → block             │
│      │                                                                      │
│      ▼                                                                      │
│  ALLOW (exit 0, empty stdout) or BLOCK (JSON {"decision":"block",...})      │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ Phase 3: Endpoint                                                           │
│ Hook: Stop → hook/completion-check.py                                       │
│                                                                             │
│  Claude is about to stop and return to user                                 │
│      │                                                                      │
│      ▼                                                                      │
│  Check 1: Hedging word detection                                            │
│    • Scans last assistant message                                           │
│    • "should be OK" + "done" combo → BLOCK                                  │
│    • "probably works" + "finished" → BLOCK                                  │
│    • "didn't test" + "complete" → BLOCK                                     │
│      │                                                                      │
│      ▼                                                                      │
│  Check 2: active-plan.md unchecked items                                   │
│    • If .claude/active-plan.md has unchecked "- [ ] ...[Agent self-verify]" │
│    • Block: "You still have N unverified items"                             │
│      │                                                                      │
│      ▼                                                                      │
│  PASS (exit 0) or BLOCK (force Claude to actually run verification)         │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The 4 Frameworks

| Code | Name | When routed here | Skills triggered |
|------|------|-----------------|-----------------|
| SP | Superpowers | Multi-file features, TDD, large refactors | `superpowers:*` |
| GS | gstack | Architecture decisions, reviews, complex design | `plan-eng-review`, `office-hours`, etc. |
| ECC | ECC subskill | Research, debugging, security, DB work | `deep-research`, `investigate`, etc. |
| CC | Native Claude | Chores, comprehension, docs, vibe coding | (no skill invoked) |

---

## Data Flow

```
User prompt
    │
    ├── router.py (Phase 1)
    │     ├── load_mode() → silent / auto / off
    │     ├── fast_path() → shortcut for trivial inputs
    │     ├── load_providers() → read ~/.config/router-hook/keys.json
    │     ├── call_router() → HTTP to OpenAI-compatible API
    │     ├── render_injection() → build 🧭 Router → ... string
    │     └── print() → injected into Claude's system context
    │
    ├── runtime-guard.py (Phase 2, per tool call)
    │     ├── load_mode() → check if off
    │     ├── blacklist_check() → regex match on Bash commands
    │     ├── scope_creep_check() → read active-plan.md allowed files
    │     ├── failure_streak_check() → read failure-streak.log
    │     └── print(JSON) if blocked
    │
    ├── failure-tracker.py (Phase 2, on each tool failure)
    │     └── append to ~/.claude/router-logs/failure-streak.log
    │
    └── completion-check.py (Phase 3, before Claude stops)
          ├── load_mode() → check if off
          ├── get_last_assistant_text() → read transcript jsonl
          ├── hedge_check() → regex match on hedging phrases
          ├── active_plan_check() → find unchecked self-verify items
          └── print(JSON) if blocked
```

---

## Decision Framework Layers

The 3-layer theoretical framework (see `docs/decision-taxonomy.md`):

```
Layer 1: Cognitive backbone       decision-taxonomy.md
         6 orthogonal axes        (X domain, Y consequence, Z involvement,
         + 4 auxiliary axes        W phase, I information, M mode)
         + 5 meta-principles      (accountability, postpone, andon,
                                   protected paths, defense-in-depth)
             ↓
Layer 2: Workflow definition      autonomy-rules.md
         2-state machine          (discussion mode / execution mode)
         5 blacklist categories
         fallback mechanisms
         self-verify vs user-verify
             ↓
Layer 3: Runtime spec             docs/router-spec-v3.md
         5-layer decision tree    (L0 hard override → L4 default)
         confidence calibration
         schema definition
             ↓ enforced by
         hook/*.py                (the actual running code)
```

---

## Config Files

| Path | Purpose |
|------|---------|
| `~/.config/router-hook/keys.json` | API endpoint + key configuration |
| `~/.config/router-hook/mode` | Current mode: `silent` / `auto` / `off` |
| `~/.claude/settings.json` | Hook registrations (modified by install.sh) |
| `~/.claude/CLAUDE.md` | Router hard rule for Claude to follow |
| `~/.router-hook/` | Hook script installation directory |
| `~/.claude/router-logs/router.log` | Per-prompt routing decisions |
| `~/.claude/router-logs/runtime-guard.log` | PreToolUse decisions |
| `~/.claude/router-logs/failure-streak.log` | Tool failure tracking |
| `~/.claude/router-logs/completion-check.log` | Stop hook decisions |
| `~/.claude/reports/` | HTML reports from render-report.py |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ROUTER_HOOK_DIR` | `~/.router-hook` | Where hook scripts are installed |
| `ROUTER_HOOK_CONFIG` | `~/.config/router-hook` | Config directory (keys.json, mode file) |

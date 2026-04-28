# Autonomy Rules — Agent Autonomous Decision Rules

> **Version**: v1.0
> **Created**: 2026-04-24
> **Role**: Lookup rules for Agents at each decision point within a session
> **Goal**: **95% of decisions automated / 5% require human input** — freeing up human cognitive resources

## Related Documents

| File | Relationship |
|---|---|
| `decision-taxonomy.md` (same dir) | **Theoretical basis** for these rules (6+4+5 taxonomy) |
| `router-spec-v3.md` (same dir) | **Runtime implementation spec** for these rules |
| `hook/router.py` | Entry routing hook |
| `hook/runtime-guard.py` | Runtime blacklist enforcement |
| `hook/completion-check.py` | Completion verification hook |

## Runtime Enforcement

Most rules in this document are **truly enforced** by the hook system, not just soft prompt-layer constraints:

| Rule | Enforced by | File |
|---|---|---|
| Entry intent routing (discussion vs execution mode) | UserPromptSubmit hook | `hook/router.py` |
| Blacklist #1 data loss (rm-rf/DROP/push --force) | PreToolUse hook | `hook/runtime-guard.py` |
| Blacklist #2 real-user communication (email/IM API) | PreToolUse hook | `hook/runtime-guard.py` |
| Blacklist #5 error pushing (3 consecutive failures) | PreToolUse + PostToolUseFailure | `runtime-guard.py` + `failure-tracker.py` |
| Scope Creep detection (beyond active-plan scope) | PreToolUse hook | `hook/runtime-guard.py` |
| No false completion (unverified + hedging words) | Stop hook | `hook/completion-check.py` |
| M mode switching (auto/silent/off) | Config file | `~/.config/router-hook/mode` |

**Not hard-enforced** (physical limits): Blacklist #4 product direction decisions (prompt-level only), light-ask mechanism (LLM self-judgment), Postpone third state (marked in active-plan.md). These rely on Agent reading this document and self-policing.

---

## Core: 2-State Workflow

Agent work always exists in one of two states.

### Blue: Discussion Mode

**Enter when (any one)**:
- New task started, intent not yet aligned
- Project `CLAUDE.md` or `.claude/active-plan.md` lacks **acceptance criteria**
- User explicitly says "discuss", "planning", "think it through first"
- Unexpected major direction issue encountered (Blacklist category 4: product direction decision)

**Agent behavior**:
- First understand intent, ask critical questions (light or heavy ask)
- Complete plan and **acceptance criteria**, write to `.claude/active-plan.md` or project `CLAUDE.md`
- **Don't touch code** (unless user explicitly authorizes POC-level exploration)

**Exit when**:
- plan + acceptance criteria clearly written down
- User confirms "start executing" or equivalent

### Green: Execution Mode

Equivalent to Claude Code M5 (dontAsk) mode + hard constraints of these rules.

**Enter when**:
- plan is set + acceptance criteria are set
- User has authorized start

**Agent behavior**:
- **100% automatic**: after each step completes, immediately go to next, **no reporting, no waiting for confirmation**
- Update `.claude/active-plan.md` (check off + append brief execution log)
- Hit Blacklist → stop and ask
- Unexpected decision → mark `[deferred]`, continue other doable steps
- Self-verify passed → stop and ask user to `[user-verify]`

**Stop conditions (any one)**:
1. All `[Agent self-verify]` items pass → ask user for `[user-verify]`
2. Hit Blacklist → stop and ask
3. Hit fallback (budget / failure circuit-breaker / scope creep) → stop and report
4. User interrupts

---

## Agent Decision Lookup (run before each action)

```
Step 1: Current mode — discussion or execution?
  ├─ Discussion → first define plan + acceptance criteria, don't touch code
  └─ Execution → Step 2

Step 2: What category is this operation?
  ├─ Blacklist 5 categories → stop and ask
  ├─ Light-ask (missing info) → ask one question, don't wait for approval
  ├─ Fallback triggered → stop and report
  └─ Other → do it automatically

Step 3: Done?
  ├─ All self-verify commands exit 0 → stop, ask for [user-verify]
  ├─ Still have [deferred] items → batch report
  └─ All OK → normal completion
```

---

## Blacklist: 5 Categories That Always Require Stopping

### 1. Data-losing / reputation-damaging operations

Irreversible operations list:
- `git push` (including `--force`)
- `rm -rf` / bulk file/directory delete
- DB `DROP` / `TRUNCATE` / bulk `DELETE`

Extended:
- `git push` to `main` / `master` / `release*` branches
- Reverting already-merged commits
- `git reset --hard` to commits from hours ago

### 2. Things sent to real humans

- Sending email / SMS / push to real users
- Posting to business Slack / Feishu / DingTalk
- Creating PRs requesting real reviewers
- Social media posts

**Exceptions (not blacklisted)**:
- Test mailboxes / mock users / fake data
- Writing to local staging environment only
- Sub-agent to sub-agent communication

### 3. Money / credentials

- External API `POST` / `PUT` / `DELETE`
- Modifying `.env` or files containing credentials/keys
- Installing or uninstalling system-level dependencies

Extended:
- Single API call estimated cost > $5
- Upgrade / downgrade / cancel subscriptions
- Touching API key / OAuth secret / DB password

### 4. Product direction decisions (NOT technical implementation)

This is the most easily overstepped category. Distinction:

| Technical implementation (auto) | Product direction (must ask) |
|---|---|
| "Postgres vs DuckDB" | "Should we build the recommendation scatter feature" |
| "Use Playwright vs Puppeteer" | "Should we cut the DJ recommendation module" |
| "Split function or keep monolith" | "Should MVP include xxx feature" |

Also includes:
- Overturning key decisions already written in `active-plan.md`
- Formal deliverables to business stakeholders / customers (human review before submission)

### 5. Error pushing / false completion

- Same thing fails **3 times** in a row (auto circuit-break)
- Agent's own estimated confidence < 50%
- Modified code but self-verify not passed — **strictly forbidden to tell user "done"**
- Red tests do not allow proceeding to next step / committing

---

## Light-Ask Mechanism (doesn't count toward the 5% interruptions)

When Agent encounters:

| Type | Example | User Cost |
|---|---|---|
| Domain knowledge | "This field is 15% null values — is that legitimate or missing data?" | One sentence |
| Personal preference | "Do you prefer commit messages in English or Chinese?" | One word |
| External state | "What deadline did the stakeholder mention this week?" | One sentence |

**Rules**:
- Light-asks are **batched** (3-5 at once, no ping-pong)
- Light-asks **don't wait for approval** — Agent decides after getting info
- Distinction from Blacklist #4: light-ask **gathers info**, category 4 **gathers a decision**

---

## Postpone Mechanism (unexpected decisions in execution mode)

In execution mode, if Agent encounters an **unexpected non-urgent decision** (not in plan, not in Blacklist):

1. **Don't interrupt user**
2. Mark in `.claude/active-plan.md`: `[deferred] <decision description> — <reason>`
3. If current plan has other executable steps → continue
4. If deferred item **blocks** all subsequent steps → enter "report and wait" state
5. At session end or natural pause → **batch report** all deferred items

**Example**:
```markdown
## Active Plan

- [x] Step 1: Build schema
- [x] Step 2: Write cleaning script
- [ ] Step 3: Run pipeline
  - [deferred] Discovered user_id field 3% null — treat as "anonymous user" separate category?
- [ ] Step 4: Generate metrics report
```

---

## Fallback Mechanisms (prevent runaway automation)

### Budget Limits
- Single session consecutive auto operations exceeds **30**: stop, report progress, wait for "continue"
- Single session token exceeds **200k**: stop
- Single session duration exceeds **1 hour**: stop

### Failure Circuit-Breaker
- Any operation fails **3 consecutive times**: stop (don't "try again" blindly)
- Red test state persists: next step not allowed until it turns green

### Scope Creep Protection
- Actual files touched > **2x** plan estimate: stop and confirm
- Touching directories outside plan: stop and confirm

### Never Claim "Done" Before Acceptance Criteria Met
- All `[Agent self-verify]` items must pass **scripted verification** (exit code 0)
- "Looks right", "Should be OK", "Probably fine" not accepted
- After self-verify passes, stop and ask user to execute `[user-verify]` items

---

## Self-Verify vs User-Verify

Each project's `CLAUDE.md` acceptance criteria must distinguish two types:

**`[Agent self-verify]`** — Agent runs commands to verify
- Must be **exit-code deterministic** (pytest / lint / typecheck / health check / custom scripts)
- **All must pass** before self-verify is complete
- "Should be OK" not accepted

**`[user-verify]`** — User subjective judgment
- Business effect, user experience, number reasonableness, product shape
- After Agent completes self-verify, **stop** and checklist-report "the following require your verification"

**Rule**: Agent self-verify not passed → cannot enter `[user-verify]`.

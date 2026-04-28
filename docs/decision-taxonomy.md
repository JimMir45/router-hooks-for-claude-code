# AI Agent Decision Taxonomy v1.1

> **Version**: v1.1 (2026-04-27 simplified: runtime algorithm removed, now implemented by router system)
> **Created**: 2026-04-24 / Simplified: 2026-04-27
> **Based on**: Research across 4 domains (decision theory / agent engineering / software management / risk & security)
> **Role**: Cognitive framework / theoretical reference — not runtime execution rules.
>
> ## Relationship to Runtime Implementation
>
> This document defines 6+4+5 decision classification axes. Their **runtime enforcement** is handled by:
>
> | Scope | Runtime Implementation | File |
> |---|---|---|
> | Entry intent routing (user prompt → framework selection) | router hook | `hook/router.py` + `docs/router-spec-v3.md` |
> | Runtime decisions (tool interception during execution) | runtime-guard hook | `hook/runtime-guard.py` |
> | Completion verification (done claim check) | completion-check hook | `hook/completion-check.py` |
> | Blacklist / fallback enforcement | autonomy-rules.md + above hooks | `docs/autonomy-rules.md` |
>
> This document no longer contains the routing pseudocode algorithm — that's now implemented by router-spec-v3 and the hooks. This document retains the 6 orthogonal axes + 4 auxiliary axes + 5 meta-principles, as a **cognitive tool for discussing new decision types**.

## Why This Document Exists

When managing 5+ concurrent projects, AI agents were causing burnout instead of reducing it. Root cause diagnosis:

1. **Agents asked about decisions at the wrong level** — trivial questions asked repeatedly (Staff anti-pattern), major decisions made autonomously without consultation (intent overreach)
2. **No objective decision taxonomy** — Agents acted on vague session-level hints
3. **Constraints from old projects wrongly applied to new ones**

This document provides a **cross-project, objective, lookup-table** decision taxonomy, serving as:
- Theoretical backbone for Agent system design
- Basis for `CLAUDE.md` / `settings.json` / hooks configuration
- Baseline for future axis extensions

## Three-Layer Overview

```
Level 1: Minimal orthogonal basis — 6 axes (X, Y, Z, W, I, M)
    Each decision must be located on all 6 axes simultaneously
Level 2: Auxiliary 4 axes (B, T, L, U)
    Non-orthogonal but practical; constraints/modifiers for Level 1
Level 3: 5 Meta-principles (guardrails)
    Hard guardrails across all decision axis combinations
```

---

# Level 1: Minimal Orthogonal Basis — 6 Axes

## Axis 1 — X: Decision Domain (What)

| Tier | Domain | Typical Actions |
|---|---|---|
| X1 | Filesystem | read/write/delete/rename files |
| X2 | Process/Service | start/stop/monitor processes |
| X3 | Network | HTTP calls, websocket, outbound comms |
| X4 | Version Control | git commit/push/branch/tag |
| X5 | Database | SELECT/INSERT/UPDATE/DELETE/DDL |
| X6 | Package Management | npm/pip/cargo install or upgrade |
| X7 | Deployment | deploy/publish/rollback |
| X8 | Code Generation | new files/modules/functions |
| X9 | Code Modification | refactor/fix/optimize |
| X10 | Communication | send message/email/notification |
| X11 | Decision/Planning | ADR/option selection/priority ranking |
| X12 | Learning/Self-evolution | modify rule/skill/config |

## Axis 2 — Y: Consequence Profile (How risky)

4 sub-axes (consequences are multi-dimensional, cannot collapse to a single risk level).

### Y1 — Reversibility (R0-R4)

| Tier | Definition | Example | Quantitative Criterion |
|---|---|---|---|
| R0 Fully reversible | Ctrl+Z level | unsaved edits, git stash | <1 min, no loss |
| R1 Delayed reversible | Backup available | git revert pushed commit | minutes + rollback |
| R2 Costly reversible | Significant time/resources | DB restore from backup | hours + manual work |
| R3 Factually irreversible | Theoretically recoverable but business impact done | sent user email, merged to main | side effects can't be undone |
| R4 Absolutely irreversible | No backup/undo | DROP TABLE without backup, force push overwriting history | impossible to recover |

**Escalation criteria** (any one triggers upgrade): no backup, externally visible (information left the system), DDL not in transaction, time window closed, credential leaked.

### Y2 — Blast Radius (T0-T10)

| Tier | Scope | Scale | Decision |
|---|---|---|---|
| T0 | Agent's own memory | session variables only | auto |
| T1 | Single file | 1 file | auto |
| T2 | Single module | ≤10 files same package | auto (if tests pass) |
| T3 | Whole project | cross-module / config | flag if schema change |
| T4 | Local dev environment | shell/env/global tool | flag |
| T5 | Remote repo (unprotected branch) | feature branch push | auto (PR as safety net) |
| T6 | Remote protected branch | main/release push | **human only** |
| T7 | Staging environment | staging deploy | flag + notify |
| T8 | Production | prod deploy / DB migrate | **human + dual approval** |
| T9 | External third-party | email/payment/publish | **human only** |
| T10 | Multi-tenant user data | all tenant data | **human + ticket** |

### Y3 — External Side Effects

| Tier | Definition |
|---|---|
| E0 | No side effects (local read-only) |
| E1 | Local write (filesystem) |
| E2 | Network read (HTTP GET, search, query) |
| E3 | Network write (API POST/PUT) |
| E4 | Communication send (email/Slack/IM) |
| E5 | Financial flow (payment/transfer) |

### Y4 — Compliance Sensitivity

| Tier | Definition | Regulatory Triggers |
|---|---|---|
| D0 | Public | — |
| D1 | Internal | — |
| D2 | Confidential (NDA/access control required) | — |
| D3 | Regulated (PII/PHI/PCI) | GDPR/HIPAA/PCI-DSS/EU AI Act |

**D3 operations always require Human approval; no M mode can bypass this.**

## Axis 3 — Z: Human Involvement (Who decides)

Based on Vroom-Yetton-Jago 5 tiers, adapted for Agents:

| Tier | Code | Description | Trigger |
|---|---|---|---|
| Z1 | AI | Agent decides + executes autonomously | Low risk + high familiarity + sufficient info |
| Z2 | AII | Agent gathers info from human, then decides | Insufficient info but Agent has decision authority |
| Z3 | CI | Agent proposes options, human confirms | Have a plan, but need user preference |
| Z4 | CII | Agent + Human discuss | Complex context requiring co-creation |
| Z5 | GII | Consensus decision | Cross-project / cross-person major decisions |

## Axis 4 — W: Decision Phase (When)

### W1 — Decision Timing

| Tier | Phase | Typical Decisions |
|---|---|---|
| W1.1 | plan | option selection, architecture, task breakdown |
| W1.2 | runtime | tradeoffs during execution, error handling |
| W1.3 | handoff | session switch, compact, worktree handoff |
| W1.4 | retro | retrospective, postmortem, lessons learned |

### W2 — Decision State

| Tier | State | Meaning |
|---|---|---|
| proposed | Proposed | Agent has given a recommendation, not finalized |
| accepted | Accepted | Executed or approved |
| **deferred** | Deferred | Should be done but info insufficient, waiting for conditions |
| superseded | Superseded | Overridden by a later decision |

## Axis 5 — I: Information State (What do I know)

### I1 — Information Completeness
- I1.0 Complete (structured, verifiable)
- I1.1 Partial (needs inferential fill)
- I1.2 Needs external query (search/ask)
- I1.3 Ambiguous (multiple interpretations)

### I2 — Value of Information (VOI, Howard theory)
- **High VOI**: Further research would significantly change the decision → **Research first, then act**
- **Low VOI**: Decision won't change even with more research → **Act now** (counter-LLM over-thinking)

### I3 — Causal Knowability (Cynefin)
- Known (Simple): cause-effect clear, best practice applies
- Knowable (Complicated): expert can analyze
- **Complex**: must probe-sense-respond (emergent)
- Chaotic: must act-sense-respond

## Axis 6 — M: Session Mode (How autonomous now)

| Tier | Mode | Autonomy | Use Case |
|---|---|---|---|
| M1 | default | Standard ask-first | Default when uncertain |
| M2 | plan | Discuss only, no execution | Planning phase |
| M3 | acceptEdits | Accept edits, ask for others | Refactoring phase |
| M4 | auto | Full autonomy + fallback | Trusted tasks |
| M5 | dontAsk | Fully autonomous | Long task runs |
| M6 | bypass | Bypass all checks | **Dangerous, sandbox only** |

**Key**: **M axis can be hot-switched, but Y axis and meta-principle guardrails never change.** No M mode permits auto when Y1≥R3 or Y2≥T6 or Y4=D3.

---

# Level 2: Auxiliary 4 Axes

## Axis 7 — B: Budget Constraints

| Sub-dim | Tiers | Trigger |
|---|---|---|
| B1 tokens | per-task / per-session / per-day | exceed → downgrade |
| B2 frequency | max consecutive auto-reply | exceed → human in loop |
| B3 failures | N consecutive blocks → fallback | 3 consecutive / 20 cumulative |
| B4 money | $ per-month | OWASP LLM10 Unbounded Consumption |

## Axis 8 — T: Trust Boundary

| Tier | Scope |
|---|---|
| T_Trust0 | Agent sandbox (fully trusted) |
| T_Trust1 | Local dev environment (generally trusted) |
| T_Trust2 | Company intranet (trusted but auditable) |
| T_Trust3 | Controlled external (whitelist URL/package/API) |
| T_Trust4 | Open external (untrusted) |

## Axis 9 — L: Learning / Evolution

| Sub-dim | Description |
|---|---|
| L1 Hard rules | Written to config files, persistent |
| L2 Soft rules | In-session constraints, ephemeral (lost after compact) |
| L3 Evolved rules | Self-sedimentation from corrections → memory → rule |

## Axis 10 — U: Audit Traceability

| Tier | Audit Level | Decision |
|---|---|---|
| U0 | No trace | **Prohibit any auto** |
| U1 | Operations log only, no inputs | Limited auto |
| U2 | Full input + operations + results | Can auto |
| U3 | Full trace + auto-generated rollback scripts | Aggressive auto |

---

# Level 3: 5 Meta-Principles (Guardrails)

Cannot be bypassed by any axis combination.

## Principle 1: Accountability Anchoring

**Rule**: Every decision must have a single Accountable entity (person or Agent), recorded in a traceable log.
**Anti-pattern**: In multi-agent systems, no one being responsible = collective irresponsibility.
**Implementation**: Log must contain `accountable_entity` field.

## Principle 2: Postpone is Legitimate

**Rule**: Decision state is ternary (execute / ask / defer), not binary.
**Implementation**: When Agent encounters "insufficient info + time not critical", mark `deferred:reason` and continue other tasks; auto-revive when conditions are met.

## Principle 3: Andon is Non-Punishing

**Rule**: Sub-Agent proactively stopping / escalating = positive behavior, not penalized.
**Anti-pattern**: If high escalate rate causes reward reduction, Agent learns to hide uncertainty (keeps going on the wrong path).
**Implementation**: Evaluation metrics explicitly include "proactive escalation rate" as neutral or positive.

## Principle 4: Protected Paths Cannot Be Bypassed

**Rule**: `.env`, `.git`, credential files, system config — **Agent cannot write to these in any M mode**.
**Implementation**: Permission classifier checks protected paths first; deny on match.

## Principle 5: Defense-in-Depth

**Rule**: Risk assessment does not rely solely on LLM; use **multi-analyzer ensemble**:
- LLM semantic analysis (nuanced but unreliable)
- Regex/signature matching (precise but rigid)
- Policy rule engine (combo attack detection)
- Aggregate by max severity

---

# Three Key Cross-Domain Insights

## Insight 1: Rust RFC's Postpone Third State

**Problem**: Agents only have binary execute/ask; when info is insufficient but other work continues, they can only repeatedly ping the user.
**Solution**: Introduce `deferred:reason` as a legitimate state — Agent marks and continues; auto-revives when conditions met.
**Reference**: Rust RFC FCP: merge / close / **postpone** three states.

## Insight 2: Toyota Andon's Stop-Pull Right + Non-Punishment Promise

**Problem**: Sub-Agents afraid of being judged "failed" (affecting reward) will stubbornly continue on the wrong path.
**Solution**: Any sub-Agent has emergency stop rights; evaluation metrics explicitly treat "proactive escalation rate" as positive.

## Insight 3: Bezos 70% Information Threshold

**Problem**: LLMs default to "more thinking = better", but waiting for 90% information is the biggest killer of organizational velocity.
**Solution**: Give each decision type an explicit confidence threshold (reversible 0.5 / irreversible 0.9); execute immediately when threshold is met; treat "keep thinking" as a cost.

---

> This document is a long-term evolving baseline, not a one-time deliverable.
> When a new decision type cannot be classified, first check the 60+ source axes in Appendix A, then decide whether to extend the taxonomy.

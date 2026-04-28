# Router Spec v3 — Data-Driven Design

> Built on 1933 real labeled examples + 171 adversarial review findings.
> Replaces v2's 14-line flat rule table with a **5-layer hierarchical decision tree**.

---

## Integration with Decision Framework (3 layers + 3 phases)

The router is not just an "entry intent router" — it is a **decision backbone spanning the entire Agent work lifecycle**.

### Document Hierarchy

```
Cognitive backbone (theory)  docs/decision-taxonomy.md    6+4+5 decision axes + 5 meta-principles
                                                           (theoretical reference, no runtime algorithm)
                         ↓ provides concepts
Workflow definition (spec)   docs/autonomy-rules.md       2-state workflow + 5 blacklists + fallback
                                                           + self-verify vs user-verify
                         ↓ references categories
Runtime spec (implementation) docs/router-spec-v3.md      5-layer decision tree → 4 frameworks
                         ↓ enforced by hooks
```

### Runtime 3 Phases (Full Agent Work Lifecycle)

```
Phase 1: Entry intent decision          Phase 2: Runtime decisions              Phase 3: Endpoint decision
   (UserPromptSubmit)                      (PreToolUse / Failure)                (Stop)
   User speaks → select framework          Agent working, interrupt you?         Done? Actually verified?
   ↓                                       ↓                                     ↓
   hook/router.py                          hook/runtime-guard.py                 hook/completion-check.py
   • 5-layer decision tree                 • 5-category blacklist intercept       • Hedging word detection
   • gpt-4o-mini / Haiku routing           • 3-consecutive-failure circuit        • active-plan.md verify check
   • silent/auto/off modes                   breaker                              • Block "should be OK" claims
                                           + hook/failure-tracker.py
                                           (PostToolUseFailure logging)
```

### M Mode ↔ Router Mode Switching

`decision-taxonomy.md` defines M1-M6 session modes. Router hook implements three of these via `~/.config/router-hook/mode`:

| decision-taxonomy M tier | Router mode | Behavior |
|---|---|---|
| M1 default / M2 plan | `auto` | Every prompt injects 🧭 Router → ... line (debug/training data) |
| M3 acceptEdits / M4 auto | **`silent`** | Only surfaces on ACTION/human_confirm/OFFLINE (default) |
| M6 bypass | `off` | Fully bypass, equivalent to no hook installed |

**Switching tool**: `router-mode auto|silent|off` (installed to `~/.local/bin/` by install.sh)
Note: `off` mode also bypasses runtime-guard and completion-check (they read the same mode file).

### Router L0 Hard Override Source

Directly references 5 meta-principles from `decision-taxonomy.md`:
- **Principle 4 Protected Paths** (`.env` / `.git` / credentials) → Router L0 forces ECC security + human_confirm
- **Y1 ≥ R3** irreversible (rm -rf / DROP / push --force) → Router L0 human_confirm
- **Y2 ≥ T6** protected branch / production / user data → Router L0 human_confirm
- **Y4 = D3** regulated (PII/PHI/PCI) → Router L0 forces ECC security

### Router OFFLINE_TOPIC Source

Corresponds to autonomy-rules.md light-ask mechanism + non-engineering domain detection. OFFLINE_TOPIC routes to CC = no engineering framework, because it's not an engineering problem.

---

## I. Why the Change (Data-Driven Findings)

### Strong vs Weak Signals (cross-analysis of labeled set)

| Signal | Routing Certainty | Data Basis |
|---|---|---|
| `OFFLINE_TOPIC=true` | → **CC + bypass framework** | 27 non-engineering domain cases; inconsistently labeled across ECC/GS/CC, CC is cleanest |
| Contains plaintext credentials/keys | → **ECC security** (priority 0) | Credential-like samples were being missed, violating hard rules |
| `complexity=architecture` | → 100% GS+SP (**85% GS**) | Architectural tasks always need multi-perspective |
| `complexity=complex` | → 97% GS+SP (**70% GS**) | Complex tasks: GS decides, SP executes |
| `vagueness=product_vague` | → **96% GS** | Product-level vague almost always needs CEO decision |
| `complexity=simple + vagueness=clear` | → **72% CC + 21% ECC** | Simple and clear goes lightweight |

### Real-World Intent Distribution

```
CC  (43.7%):  chore 327 / other 124 / comprehension 104 / recovery 80 / docs 79
GS  (30.1%):  decision 179 / design 177 / review 104 / docs 44 / research 40
ECC (18.2%):  research 193 / debug 84 (these two = 84% of ECC)
SP  ( 8.1%):  feat 80 (SP almost exclusively serves multi-file features)
```

### v2 → v3 Key Changes

| Change | Reason |
|---|---|
| **gstack 6 roles → 4 roles** (removed ReleaseManager, Designer demoted to optional) | ReleaseManager only 4 cases, Designer 38 |
| **ECC subskill 5 → 2 main + 3 specialized** | research/debug = 84%, security/database/memory ~3% each but highest priority |
| **OFFLINE_TOPIC independent**, not squeezing ECC | 27 non-engineering prompts, ECC semantics were being diluted |
| **SP no longer handles fix/refactor/test fallback** | SP actual use 51% feat, fix/refactor should go to ECC debug or CC |
| **complexity=simple gets fast-path** | 55% of samples go lightweight, no router inference wasted |
| **needs_gsd trigger simplified** | Real data: 96% of needs_gsd=true are SP/GS, CC/ECC almost never need GSD |

---

## II. 5-Layer Hierarchical Decision Tree

Evaluate in order; **first match returns**:

### Layer 0: Hard Override (highest priority)
```
1. Contains plaintext credentials/keys/account passwords
   → ECC security, conf cap 0.6, require second confirmation
2. Contains irreversible operation keywords (rm -rf / DROP / TRUNCATE / push --force / bulk delete)
   → Keep original framework, conf cap 0.7, force human confirmation
```

### Layer 0.5: OFFLINE_TOPIC
```
Matches finance/car/legal/MBTI/family/lifestyle keywords
AND does not contain work/engineering context
→ CC + offline_topic=true + no framework prompt injected
```

### Layer 1: Complexity Signal (strong)
```
complexity=architecture → GS-EngManager (default), fallback SP
complexity=complex → by type:
    feat (multi-file) → SP, fallback GS-EngManager
    other → GS-EngManager, fallback SP
```

### Layer 2: Vagueness Signal
```
vagueness=product_vague → GS-CEO, fallback GS-EngManager  (96% accurate)
vagueness=engineering_vague + intent ∈ {feat, design} → SP, fallback GS-EngManager
vagueness=engineering_vague + intent ∈ {decision, review} → GS-EngManager
```

### Layer 3: Intent Signal
```
intent=decision → GS-EngManager (tech decision) | GS-CEO (product decision)
intent=design → GS-EngManager (architecture) | GS-Designer (UX)
intent=review → GS-QA (technical) | GS-CEO (business)
intent=docs → GS-DocEngineer (formal PRD/ADR) | CC (simple README)
intent=research → ECC research
intent=debug → ECC debug
intent=feat + complexity ∈ {medium, complex} → SP
intent=fix → ECC debug (single-point) | SP (multi-file fix)
intent=refactor → SP (large) | ECC debug (single-point)
intent=test → SP (TDD) | CC (add tests)
intent=comprehension → CC
intent=recovery → CC
intent=chore → CC
intent=vibe → CC (strong signals: "prototype/quick/quality doesn't matter")
```

### Layer 4: Default
```
→ CC
```

---

## III. Output Schema

```json
{
  "framework_primary": "SP|GS|ECC|CC",
  "framework_fallback": "SP|GS|ECC|CC|null",
  "ecc_subskill": "research|debug|security|database|memory|other|null",
  "gs_role": "EngManager|CEO|QA|DocEngineer|Designer|null",
  "needs_gsd": false,
  "offline_topic": false,
  "human_confirm_required": false,
  "confidence": 0.0,
  "reason": "1-sentence routing rationale"
}
```

**Field notes**:
- `intent_dimension` / `complexity` / `vagueness` removed (internal router judgment, not output)
- `human_confirm_required` added (triggered by irreversible operation hard rules)
- `offline_topic` added (replaces OFFLINE_TOPIC 5th-category implementation)

---

## IV. Confidence Thresholds (learned from labeled set)

| Confidence | Meaning | Behavior |
|---|---|---|
| ≥ 0.85 | High confidence | Directly inject framework |
| 0.7-0.85 | Medium confidence | Inject + show in status line |
| 0.5-0.7 | Low confidence | Inject + **also** show fallback to user |
| < 0.5 | Very low | **Don't inject**, use native CC + note "unclear intent" |

**Calibration rules** (avoid overconfidence):
- `complexity=simple + intent=chore + single turn` → conf not above 0.85
- Any security/data-sensitive signal → conf not above 0.6
- `vagueness=exploratory` → conf not above 0.55

---

## V. needs_gsd Trigger Conditions (simplified)

Triggered when **either** of the following is true:

1. `framework_primary ∈ {SP, GS}` AND prompt contains explicit long-task signal (estimated >2h, multiple milestones, cross-module)
2. User explicitly mentions "long run" / "continuous" / "phased" / "GSD"

CC/ECC default `needs_gsd=false` (data shows 96% of long tasks are in SP/GS).

---

## VI. Router Prompt Template (for LLM backend)

See `hook/router.py` — the `ROUTER_SYSTEM` constant contains the full prompt.

---

## VII. Known Issues (for future iteration)

1. **17 ambiguous samples**: edge cases, correct with real misclassifications after live deployment
2. **Slash command harness routing**: slash-triggered should go SKILL_DIRECT or CC? Currently CC, validate in production
3. **Compound prompts** (simultaneously fix+feat+design): currently routes by dominant signal; single-label intent has information loss, may need multi-label output
4. **ai_tool_meta dimension**: doesn't affect main routing now, but recommend adding `ai_tool_meta: bool` auxiliary analysis field (next version)
5. **Confidence calibration real distribution**: v3 calibration rules estimated from v1 data; need real distribution regression after router goes live

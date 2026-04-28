#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook: 5-layer intent router.
Backend: any OpenAI-compatible chat completion endpoint (model defaults to gpt-4o-mini).
Configure your endpoint + key in ~/.config/router-hook/keys.json — see config/keys.json.example.
Logs every decision to ~/.claude/router-logs/router.log for iteration analysis.
"""
import json
import os
import re
import sys
import time
import hashlib
import datetime
import urllib.request
import urllib.error
from pathlib import Path

# === DIRECTOR-WORKER PATCH START ===
# Inserted by Day 2-5. Remove with: bash hook/uninstall-director.sh
sys.path.insert(0, str(Path(__file__).parent))
try:
    from task_classifier import classify as _classify_task
except Exception:
    _classify_task = None
try:
    from dispatch_subagent import build_dispatch_instruction as _build_dispatch
except Exception:
    _build_dispatch = None
# === DIRECTOR-WORKER PATCH END ===

LOG_PATH = Path.home() / ".claude" / "router-logs" / "router.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = Path.home() / ".config" / "router-hook" / "keys.json"
MODE_FILE = Path.home() / ".config" / "router-hook" / "mode"
TIMEOUT = 10  # seconds per provider; total worst-case ~20s with fallback

# Force socket-level timeout so DNS/connect also time out (urlopen timeout is read-only)
import socket
socket.setdefaulttimeout(TIMEOUT)


def load_mode():
    """Read M mode from config file. Aligns with rules/common/decision-taxonomy.md M1-M6.

    Modes:
      auto    — show every decision (verbose; debugging / training data)
      silent  — show only when ACTION REQUIRED / human_confirm / high signal (DEFAULT)
      off     — bypass entirely; act as if hook isn't installed
    """
    try:
        return MODE_FILE.read_text().strip().lower()
    except Exception:
        return "silent"  # safe default after the verbose 'auto' phase


def should_render(decision: dict, mode: str) -> bool:
    """Decide whether to inject the decision into context based on mode."""
    if mode == "off":
        return False
    if mode == "auto":
        return True
    # silent: only surface when there's actual signal value
    fw = decision.get("framework_primary")
    has_action = fw in ("SP", "GS", "ECC")  # frameworks that get [ACTION REQUIRED]
    needs_confirm = decision.get("human_confirm_required")
    is_offline_warn = decision.get("offline_topic")  # offline still useful (warn user)
    return bool(has_action or needs_confirm or is_offline_warn)


def load_providers():
    """Return [primary, fallback] provider configs with resolved keys."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except Exception:
        return []
    out = []
    for slot in ("primary", "fallback"):
        p = cfg.get(slot)
        if not p:
            continue
        key = p.get("key", "")
        if not key and p.get("key_file"):
            try:
                key = json.loads(Path(p["key_file"]).read_text()).get(
                    p.get("key_field", "OPENAI_API_KEY"), ""
                )
            except Exception:
                key = ""
        if key:
            out.append({
                "name": p.get("name", slot),
                "endpoint": p["endpoint"],
                "model": p.get("model", "gpt-4o-mini"),
                "key": key,
            })
    return out


ROUTER_SYSTEM = """你是 Claude Code 的 5 层意图路由器。基于用户输入,严格按决策树输出 JSON。

L0 硬规则:
- 含明文密钥/密码/AK/SK → ECC security, conf≤0.6, human_confirm=true
- 含 rm -rf/DROP/push --force/批量删除 → 维持原 framework, human_confirm=true

L0.5 OFFLINE_TOPIC: 仅当**主导意图**为非工程域(理财/购车/合同/法务/MBTI/家庭/生活咨询)才命中。
  ⚠️ 工程任务为主、生活话题为辅时,不算 offline。判断主导:
    - 出现 工程关键词(代码/重构/实现/调试/部署/SQL/API/bug/性能/架构/refactor/feat) → 工程主导,不 offline
    - 工程问句 + 生活附问("顺便"/"对了"/"另外"等连接词) → 工程主导,**优先工程框架**
    - 仅当全句无工程信号、纯生活问题 → offline_topic=true → CC
  例: "SQL 性能慢,顺便聊聊娃睡觉" → ECC-debug (不 offline)
       "理财产品推荐" → CC offline

L1 复杂度:
- 架构级/跨模块多文件 → GS-EngManager (架构) | SP (多文件 feat)
- 复杂任务 → GS-EngManager

L2 模糊度:
- 产品级模糊(不知做啥) → GS-CEO
- 工程模糊+feat/design → SP, fallback GS-EngManager
- 工程模糊+决策 → GS-EngManager

L3 意图:
- 决策/选型 → GS-EngManager(技术) | GS-CEO(产品)
- 评审 → GS-QA(技术) | GS-CEO(业务)
- 设计 → GS-EngManager(架构,有具体技术约束) | GS-Designer(UX 少用)
- 调研/学习 → ECC research
- bug 定位/性能诊断/单点 fix → ECC debug
- 数据库 schema/migration/索引 → ECC database (即使含安全权限,主导是 schema)
- 安全审计/漏洞扫描(纯安全) → ECC security
- **多文件 feat / 大型 refactor / TDD / 代码重构 / 抽象重构 / 策略模式 / 设计 pattern** → SP
- 正式文档(PRD/ADR) → GS-DocEngineer
- 简单 docs/chore/comprehension/recovery → CC
- Vibe Coding(明确说"快速/先跑通/不管质量") → CC

L3.5 ECC fallback 偏好:工程决策类(architecture/api-design/deployment) 当 framework_primary=GS 时,
fallback 优先 ECC(具体技术能力)而不是 CC,因为 GS 给出方向后落地需要 ECC 工具。

L4 默认 → CC

Confidence 校准:simple+chore+单turn ≤0.85; 敏感 ≤0.6; exploratory ≤0.55

输出严格 JSON(无 markdown 包裹,不要漏字段):
{"framework_primary":"SP|GS|ECC|CC","framework_fallback":"SP|GS|ECC|CC|null","ecc_subskill":"research|debug|security|database|memory|other|null","gs_role":"EngManager|CEO|QA|DocEngineer|Designer|null","needs_gsd":false,"offline_topic":false,"human_confirm_required":false,"confidence":0.85,"reason":"中文1句"}"""


# --- Hard-regex defense-in-depth (v3.1 fix) ---
# Override LLM output: if prompt contains destructive keywords, force human_confirm_required=True.
# Reason: LLM (gpt-4o-mini) was inconsistent — combo_E03 saw "force-push" in reasoning but didn't set
# the flag; combo_E04 returned empty JSON for "DROP TABLE". Decision-taxonomy 元原则 5 (Defense-in-depth):
# don't rely on LLM alone; ensemble with regex.
DESTRUCTIVE_PROMPT_PATTERNS = [
    (re.compile(r"\bgit\s+push\b.*?(--force|--force-with-lease|-f\b)", re.I), "git push --force"),
    (re.compile(r"(?<!--)\bforce\s*push\b", re.I), "force push"),
    (re.compile(r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", re.I), "SQL DROP"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", re.I), "SQL TRUNCATE"),
    (re.compile(r"\bDELETE\s+FROM\b(?![^;\n]*\bWHERE\b)", re.I), "SQL DELETE 无 WHERE"),
    (re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\b"), "rm -rf"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "git reset --hard"),
    (re.compile(r"(明文|hardcode).{0,20}?(密码|password|api[_\s-]?key|secret|token|AK|SK)", re.I),
     "明文密钥/密码"),
    (re.compile(r"\b(password|api[_-]?key|secret)\s*=\s*[a-zA-Z0-9]{6,}", re.I),
     "明文凭据赋值"),
]


def hard_regex_override(prompt: str, decision: dict) -> dict:
    """If prompt has destructive keywords, force human_confirm_required=True regardless of LLM.

    Returns the (possibly modified) decision dict. Records what triggered the override in
    decision['_hard_override'] for telemetry. If LLM errored AND we detect destructive keywords,
    synthesize a minimal decision so the warning still surfaces (defense-in-depth: never let LLM
    failure suppress a hard-rule signal).
    """
    if not isinstance(decision, dict):
        decision = {}
    hits = []
    for rx, label in DESTRUCTIVE_PROMPT_PATTERNS:
        if rx.search(prompt):
            hits.append(label)
    if not hits:
        return decision

    # If LLM errored, synthesize a minimal CC decision so we still render the warning.
    if decision.get("error"):
        decision = {
            "framework_primary": "CC",
            "framework_fallback": None,
            "ecc_subskill": None, "gs_role": None,
            "needs_gsd": False, "offline_topic": False,
            "human_confirm_required": True,
            "confidence": 0.5,
            "reason": "LLM 路由失败,但硬规则触发",
            "_synthesized_from_error": True,
        }

    decision["human_confirm_required"] = True
    decision["_hard_override"] = hits
    existing_reason = decision.get("reason", "")
    override_note = f"[硬规则] 检测到不可逆操作: {', '.join(hits)}"
    if override_note not in existing_reason:
        decision["reason"] = (existing_reason + " | " + override_note).strip(" |")
    return decision


def fast_path(prompt: str):
    """Return decision when obviously not worth calling LLM, else None."""
    p = prompt.strip()
    if not p:
        return None  # empty: bypass
    if len(p) < 10:
        return {
            "framework_primary": "CC",
            "framework_fallback": None,
            "ecc_subskill": None, "gs_role": None,
            "needs_gsd": False, "offline_topic": False,
            "human_confirm_required": False,
            "confidence": 0.5, "reason": "极短输入(<10字符),沿用上下文"
        }
    return None


def call_one(provider: dict, prompt: str):
    body = json.dumps({
        "model": provider["model"],
        "max_tokens": 250,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": prompt[:3000]},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        provider["endpoint"],
        data=body,
        headers={
            "Authorization": f"Bearer {provider['key']}",
            "Content-Type": "application/json",
            "User-Agent": "router-hook/1.0 (claude-code)",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            resp = json.loads(r.read())
        text = resp["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip("\n` ")
        decision = json.loads(text)
        decision["_latency_ms"] = int((time.time() - t0) * 1000)
        decision["_provider"] = provider["name"]
        return decision
    except urllib.error.HTTPError as e:
        return {"error": f"http_{e.code}", "_provider": provider["name"],
                "_latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"error": str(e)[:120], "_provider": provider["name"],
                "_latency_ms": int((time.time() - t0) * 1000)}


def call_router(prompt: str):
    providers = load_providers()
    if not providers:
        return {"error": "no_providers_configured"}
    # Try primary, fall back through the chain on error
    last_error = None
    for prov in providers:
        decision = call_one(prov, prompt)
        if not decision.get("error"):
            return decision
        last_error = decision
    return last_error or {"error": "all_providers_failed"}


GS_ROLE_SKILLS = {
    "CEO": ["office-hours", "plan-ceo-review"],
    "EngManager": ["plan-eng-review", "review"],
    "Designer": ["plan-design-review", "design-consultation"],
    "QA": ["qa-only", "design-review"],
    "DocEngineer": ["document-release"],
    "ReleaseManager": ["ship", "land-and-deploy"],
}

ECC_SUB_SKILLS = {
    "research": ["deep-research"],
    "debug": ["investigate", "superpowers:systematic-debugging"],
    "security": ["security-review", "cso"],
    "database": ["database-migrations", "postgres-patterns"],
    "memory": ["claude-mem:mem-search"],
}


def action_block(decision: dict) -> str:
    """Build [ACTION REQUIRED] line that pushes Claude to actually invoke Skill tool."""
    fw = decision.get("framework_primary")
    if decision.get("offline_topic") or fw == "CC":
        return ""

    if fw == "SP":
        return (
            "[ACTION REQUIRED] Before responding, invoke "
            "Skill(\"superpowers:using-superpowers\") to load the framework, "
            "then select the appropriate sub-skill: "
            "brainstorming (创意/新功能), writing-plans (多步规划), "
            "test-driven-development (实现/修bug), "
            "systematic-debugging (调试)."
        )

    if fw == "GS":
        role = decision.get("gs_role")
        skills = GS_ROLE_SKILLS.get(role, ["office-hours"])
        opts = " or ".join(f'Skill("{s}")' for s in skills)
        return f"[ACTION REQUIRED] Before responding, invoke {opts} to load the gstack {role or 'CEO'} workflow."

    if fw == "ECC":
        sub = decision.get("ecc_subskill")
        skills = ECC_SUB_SKILLS.get(sub)
        if not skills:
            return ""
        opts = " or ".join(f'Skill("{s}")' for s in skills)
        return f"[ACTION REQUIRED] Before responding, invoke {opts}."

    return ""


def render_injection(decision: dict) -> str:
    fw = decision.get("framework_primary", "CC")
    conf = decision.get("confidence", 0)
    reason = decision.get("reason", "")
    fallback = decision.get("framework_fallback")
    offline = decision.get("offline_topic")
    hc = decision.get("human_confirm_required")
    role = decision.get("gs_role")
    sub = decision.get("ecc_subskill")
    gsd = decision.get("needs_gsd")

    target = fw
    if fw == "GS" and role:
        target = f"gstack-{role}"
    elif fw == "ECC" and sub:
        target = f"ECC-{sub}"
    elif fw == "SP":
        target = "Superpowers (5-phase)"
    elif fw == "CC":
        target = "原生 CC"

    lines = [f"🧭 Router → {target}  (conf {conf:.2f})"]
    if fallback and fallback != fw:
        lines.append(f"   fallback: {fallback}")
    if gsd:
        lines.append("   ⏳ 长任务,建议叠加 GSD spec-driven")
    if offline:
        lines.append("   ⚠️  OFFLINE_TOPIC,不上框架,走原生 CC")
    if hc:
        lines.append("   ⚠️  涉及不可逆/敏感操作,执行前请人工确认")
    if reason:
        lines.append(f"   reason: {reason}")

    action = action_block(decision)
    if action:
        lines.append("")
        lines.append(action)

    lines.append("")
    lines.append("   override: 直接说 \"切到 X\" 或 \"用原生 CC\" 即可改道")
    return "\n".join(lines)


def main():
    # Mode check first — off means full bypass, no work, no log
    mode = load_mode()
    if mode == "off":
        sys.exit(0)

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = payload.get("prompt", "")
    if not prompt:
        sys.exit(0)

    decision = fast_path(prompt)
    used_fast = decision is not None
    if decision is None:
        decision = call_router(prompt)

    # v3.1: hard-regex defense-in-depth — overrides LLM if destructive keywords present
    decision = hard_regex_override(prompt, decision)

    # === DIRECTOR-WORKER ANNOTATE START ===
    if _classify_task is not None and not decision.get("error"):
        try:
            decision.update(_classify_task(prompt, decision))
        except Exception:
            pass
    # === DIRECTOR-WORKER ANNOTATE END ===

    log_entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "session_id": payload.get("session_id", "")[:16],
        "cwd": payload.get("cwd", ""),
        "mode": mode,
        "prompt_hash": hashlib.md5(prompt.encode()).hexdigest()[:10],
        "prompt_preview": prompt[:120].replace("\n", " "),
        "fast_path": used_fast,
        "decision": decision,
    }
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if decision.get("error"):
        err = decision.get("error", "")
        # only surface errors in auto mode; silent stays silent
        if mode == "auto":
            if err == "no_providers_configured":
                print("🧭 Router: keys 未配置,走原生 CC")
            elif err == "all_providers_failed":
                print("🧭 Router: 所有 provider 失败,走原生 CC")
        sys.exit(0)

    if should_render(decision, mode):
        # === DIRECTOR-WORKER DISPATCH START ===
        # Original-v3.1: print(render_injection(decision))
        dispatch_text = ""
        if _build_dispatch is not None:
            try:
                d = _build_dispatch(prompt, decision)
                if d.get("mode") == "dispatch" and d.get("text"):
                    dispatch_text = d["text"]
                    if d.get("sub_agent_prompt"):
                        dispatch_text += "\n\n--- sub_agent_prompt ---\n" + d["sub_agent_prompt"]
            except Exception:
                pass
        if dispatch_text:
            print(dispatch_text)
        else:
            print(render_injection(decision))
        # === DIRECTOR-WORKER DISPATCH END ===
    sys.exit(0)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Claude Code UserPromptSubmit hook: 5-layer intent router.
Backend (v4): Claude CLI in --bare mode using user's OAuth access token
  pulled from macOS keychain ("Claude Code-credentials").
Logs every decision to ~/.claude/router-logs/router.log.
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
import subprocess
from pathlib import Path

# --- Anti-recursion guard ---
# If router.py is invoked from a subprocess spawned by router.py itself
# (e.g. via `claude --bare -p` for LLM call), exit immediately to avoid
# infinite hook recursion. The outer caller sets ROUTER_HOOK_RECURSIVE=1.
if os.environ.get("ROUTER_HOOK_RECURSIVE") == "1":
    sys.exit(0)

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
TIMEOUT = 35  # v4: claude --bare 启动+推理偶尔 17-20s,留 ~75% 头度

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


def _get_claude_oauth_token():
    """Pull current OAuth access token from macOS keychain.
    CC refreshes this in the background during active sessions.
    Returns None if keychain unreadable or token missing.
    """
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, timeout=3, check=True
        ).stdout.decode().strip()
        return json.loads(raw).get("claudeAiOauth", {}).get("accessToken") or None
    except Exception:
        return None


def load_providers():
    """Return [primary] provider config. v4: claude-cli only (uses CC OAuth)."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        cfg = {}
    primary = cfg.get("primary") or {}
    # Default to claude-cli; user can override model via keys.json primary.model
    return [{
        "name": primary.get("name", "claude-cli"),
        "type": "claude-cli",
        "model": primary.get("model", "sonnet"),
        "max_tokens": primary.get("max_tokens", 800),
    }]


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

关键边界示例(小模型易错,务必内化):

▸ 用户体验反馈/吐槽 — 区分有无产品视角:
  "勉强,要是能加歌词就能跟唱了" → GS / Designer (产品体验改进建议,带角色视角)
  "整复杂了,只抓主旋律就行了" → CC (纯吐槽+简化诉求,不带角色)
  规则:含"产品建议/角色视角" → GS-Designer/CEO; 纯反馈无建议 → CC

▸ 产品调研 vs 技术调研:
  "ktv/音乐领域有哪些好产品,蓝海调研" → GS / CEO (产品赛道战略)
  "RAG 框架对比,LangChain vs LlamaIndex" → ECC / research (技术工具)
  规则:研究"做什么/做哪个市场" → GS-CEO; 研究"怎么做/用什么工具" → ECC-research

▸ 多平台/多模块迁移 vs SP:
  "把 18000 平台功能迁移到 8910,拆分评测平台" → GS / EngManager (架构决策)
  "把 auth 模块抽出策略模式" → SP (实现已定的多文件改动)
  规则:决定"怎么拆分/迁移" → GS-EngManager; 实现"已定的多文件改动" → SP

▸ 测试方案设计 vs 测试执行:
  "为小程序设计测试方案:矩阵+冒烟+回归" → GS / QA (策略设计)
  "跑下 pytest 看哪个 red" → ECC / debug (执行/排查)
  规则:设计测试矩阵/策略 → GS-QA; 跑/调试已有测试 → ECC

▸ 工作流程纠正 vs 闲聊:
  "刚脑爆完直接开干?先去 claude-mem 拉记忆" → GS / EngManager (流程纠正)
  规则:对方法/流程/做事顺序的反馈 → GS-EngManager; 对结果纯吐槽 → CC

▸ 简单执行确认 vs 真实工程任务:
  "请提交 M8" / "启动 agent 跑这 5 个用例" → CC (任务延续/确认)
  "线上 500 错误,定位下原因" → ECC / debug (需技术能力分析)
  规则:延续上文的确认/触发 → CC; 需要技术分析的新工作 → ECC

Confidence 校准:simple+chore+单turn ≤0.85; 敏感 ≤0.6; exploratory ≤0.55

— Memorable signal 提取 —
检测用户消息是否含值得长期记住的"事实/决策/偏好/角色翻转",有则填 memorable_signal,无则 null。
kind 枚举:
  decision    — 明确选了方向 ("我决定 X / 走 X 路线 / 改成 X")
  preference  — 持久偏好 ("我希望以后 X / 默认 X / 我不喜欢 X")
  role_flip   — 推翻之前定义的角色/方向 ("不要再 X 了 / 之前说的 X 作废")
  fact        — 项目级硬事实 ("X 项目用 Y / Z 必须 W")
  ban         — 严令禁止 ("禁止 X / 不许 X")
text 写成自包含一句(≤80 字),从用户原话提炼,不加修饰,带时间感时附 "(YYYY-MM-DD)"。
绝大多数闲聊/执行确认 → null。宁可漏判,不要乱填。

— Project tagging —
当 memorable_signal != null,判断这条消息明确关于以下哪个项目,设 project_tag。
已知项目:AI-Rec / ai-content-platform / car-ktv / dazi / instinct / music-rec-engine / music-rec-offline-eval-platform / music-score / music-score-factory / router-eval-share / sensevoice / xiaoyi
判据:消息或上下文出现项目名/项目特有术语/项目特有目录路径。
不确定或泛主题 → null(让 hook 用 cwd 作为兜底)。宁可漏判。

输出严格 JSON(无 markdown 包裹,不要漏字段):
{"framework_primary":"SP|GS|ECC|CC","framework_fallback":"SP|GS|ECC|CC|null","ecc_subskill":"research|debug|security|database|memory|other|null","gs_role":"EngManager|CEO|QA|DocEngineer|Designer|null","needs_gsd":false,"offline_topic":false,"human_confirm_required":false,"confidence":0.85,"reason":"中文1句","memorable_signal":{"kind":"decision|preference|role_flip|fact|ban","text":"..."}|null,"project_tag":"<项目名>|null"}"""


# --- Auto-capture memorable signals to beads (v3.2; v3.3 adds project_tag routing) ---
_PROJECT_ROOT = os.path.expanduser("~/LS/Project")


def _resolve_target_dir(cwd: str, project_tag) -> str:
    """Pick which project's .beads/ to write to.
    Priority: project_tag (if valid + has .beads/) > cwd (if has .beads/) > None
    """
    if isinstance(project_tag, str) and project_tag.strip():
        candidate = os.path.join(_PROJECT_ROOT, project_tag.strip())
        if os.path.isdir(os.path.join(candidate, ".beads")):
            return candidate
    if cwd and os.path.isdir(os.path.join(cwd, ".beads")):
        return cwd
    return ""


def _maybe_capture_memory(cwd: str, signal, project_tag=None):
    """If LLM flagged a memorable signal, route to the right project's beads.
    Fire-and-forget: 3s timeout, all errors swallowed. Never blocks routing.
    """
    if not signal or not isinstance(signal, dict):
        return
    text = (signal.get("text") or "").strip()
    kind = (signal.get("kind") or "fact").strip()
    if not text:
        return
    target = _resolve_target_dir(cwd, project_tag)
    if not target:
        return
    try:
        subprocess.run(
            ["bd", "remember", "--quiet", f"[{kind}] {text}"],
            cwd=target,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except Exception:
        pass


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
    """Dispatch to provider-specific call. v4: only claude-cli supported."""
    if provider.get("type") == "claude-cli":
        return _call_claude_cli(provider, prompt)
    return {"error": "unknown_provider_type", "_provider": provider.get("name", "?")}


def _call_claude_cli(provider: dict, prompt: str):
    """Invoke `claude --bare -p` with user's OAuth token; parse JSON result.
    --bare skips hooks/skills/keychain reads, so no recursion + minimal context.
    """
    token = _get_claude_oauth_token()
    t0 = time.time()
    if not token:
        return {"error": "no_oauth_token", "_provider": provider["name"],
                "_latency_ms": int((time.time() - t0) * 1000)}
    env = os.environ.copy()
    env["ROUTER_HOOK_RECURSIVE"] = "1"
    env["ANTHROPIC_API_KEY"] = token
    # Drop variables that could leak parent CC session context into the spawned --bare CC.
    for k in ("CLAUDE_CODE_EXECPATH", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA",
              "CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID"):
        env.pop(k, None)
    cmd = [
        "claude", "--bare", "-p",
        "--system-prompt", ROUTER_SYSTEM,
        "--output-format", "json",
        "--model", provider.get("model", "sonnet"),
        "--no-session-persistence",
    ]
    try:
        r = subprocess.run(
            cmd, env=env, input=prompt[:3000], text=True,
            capture_output=True, timeout=TIMEOUT,
        )
        latency = int((time.time() - t0) * 1000)
        if r.returncode != 0:
            return {"error": f"claude_cli_rc{r.returncode}",
                    "_provider": provider["name"],
                    "_latency_ms": latency,
                    "_stderr": (r.stderr or "")[:200]}
        outer = json.loads(r.stdout)
        if outer.get("is_error"):
            return {"error": f"claude_api_{outer.get('api_error_status') or 'err'}",
                    "_provider": provider["name"],
                    "_latency_ms": latency}
        text = (outer.get("result") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip("\n` ")
        decision = json.loads(text)
        decision["_latency_ms"] = latency
        decision["_provider"] = provider["name"]
        decision["_cost_usd"] = outer.get("total_cost_usd")
        return decision
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "_provider": provider["name"],
                "_latency_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"error": str(e)[:120], "_provider": provider["name"],
                "_latency_ms": int((time.time() - t0) * 1000)}


# === P1 二阶路由 (2026-05-08 引入, v4 简化) ===
# v4 用 Claude sonnet 即可,不需要再 upgrade 到 gpt-5.5
UPGRADE_THRESHOLD = 0.5  # 仅在极低置信时重试一次(同模型,温度仍为 0 — 实际等同重试)


def call_router(prompt: str):
    providers = load_providers()
    if not providers:
        return {"error": "no_providers_configured"}
    for prov in providers:
        decision = call_one(prov, prompt)
        if decision.get("error"):
            continue
        return decision
    return {"error": "all_providers_failed"}


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


# === P0 Provider 健康监控 (2026-05-08 引入) ===
HEALTH_WINDOW = 20         # 看最近 N 条决策
HEALTH_ERR_THRESHOLD = 0.5 # 失败率 > 50% 视为不健康
NOTIFY_COOLDOWN_SEC = 1800 # 30 min 内同等级告警只发一次
NOTIFY_TS_FILE = Path("/tmp/router-health-last-notify")


def health_check():
    """读最近 HEALTH_WINDOW 条决策,返回失败率 + 最近 provider。
    纯被动统计,失败不抛错,返回 None 即视为无数据。"""
    try:
        with open(LOG_PATH) as f:
            recent = f.readlines()[-HEALTH_WINDOW:]
        entries = [json.loads(l) for l in recent if l.strip()]
        if len(entries) < 5:
            return None  # 数据太少不告警
        errs = sum(1 for e in entries if "error" in e.get("decision", {}))
        last = entries[-1].get("decision", {})
        return {
            "n": len(entries),
            "errs": errs,
            "rate": errs / len(entries),
            "last_provider": last.get("_provider", "?"),
            "last_error": last.get("error", ""),
        }
    except Exception:
        return None


def maybe_fire_notification(h: dict):
    """超阈值且过冷却期 → 触发 macOS 通知。失败静默吞掉。"""
    now = int(time.time())
    last = 0
    try:
        last = int(NOTIFY_TS_FILE.read_text().strip())
    except Exception:
        pass
    if now - last < NOTIFY_COOLDOWN_SEC:
        return
    msg = (f"最近 {h['n']} 次路由,{h['errs']} 次失败 "
           f"({int(h['rate']*100)}%),provider={h['last_provider']}")
    title = "Router 健康告警"
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "{title}" sound name "Basso"'],
            timeout=5, check=False, capture_output=True
        )
        NOTIFY_TS_FILE.write_text(str(now))
    except Exception:
        pass


def render_health_banner():
    """如最近失败率超阈值,返回一段 banner 文字插到 hook 输出最前;否则返回空。"""
    h = health_check()
    if not h or h["rate"] < HEALTH_ERR_THRESHOLD:
        return ""
    maybe_fire_notification(h)
    err_str = f", 最后错误={h['last_error'][:40]}" if h["last_error"] else ""
    return (f"⚠️ Router 健康告警:最近 {h['n']} 次决策中 {h['errs']} 次失败"
            f"({int(h['rate']*100)}%),provider={h['last_provider']}{err_str}\n"
            f"   建议:检查 sub2api / 切 ollama,或 router-mode silent 暂停\n\n")


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

    # v3.3: auto-capture memorable signals — prefer project_tag, fallback cwd
    _maybe_capture_memory(
        payload.get("cwd", ""),
        decision.get("memorable_signal"),
        project_tag=decision.get("project_tag"),
    )

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
        banner = render_health_banner()
        print(banner + render_injection(decision))
    sys.exit(0)


if __name__ == "__main__":
    main()

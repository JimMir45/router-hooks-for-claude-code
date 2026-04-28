"""
dispatch_subagent — Director-Worker 第二步:产生"派 sub-agent 的指令文本"

Hook 不能直接调 Claude Code 的 Agent 工具(Agent 工具只在主 session 可用)。
所以本模块只生成注入文本(instruction),由主 session 的 Claude 真派 sub-agent。

设计原则:
  1. SP 任务派 general-purpose sub-agent + 内嵌 SKILL.md 路径(SP skill 让 sub-agent 自己读)
  2. GS 任务同理(office-hours / plan-eng-review skill 路径)
  3. ECC 任务同理(deep-research / debug 等)
  4. CC / simple 任务不派 sub-agent,直接主 session 回
  5. **fallback**: skill 文件找不到 / framework 不支持 → 回退到 v3.1 inject 模式

注入文本格式遵循 router.py 现有约定([ACTION REQUIRED] line),向前兼容。
"""

import datetime
import json
import os
from pathlib import Path
from typing import Optional

# Day 7 (Step 2): A/B mode switch + instrumentation
DIRECTOR_MODE_FILE = Path.home() / ".config" / "router-hook" / "director_mode"
DIRECTOR_LOG = Path.home() / ".claude" / "router-logs" / "director.jsonl"
DEFAULT_MODE = "dispatch_all"  # backward-compatible with Day 3-6 behavior
HIGH_CONF_THRESHOLD = 0.7


def _load_mode() -> str:
    try:
        m = DIRECTOR_MODE_FILE.read_text().strip().lower()
        if m in ("off", "dispatch_high_conf", "dispatch_all"):
            return m
    except Exception:
        pass
    return DEFAULT_MODE


def _log_event(event: dict) -> None:
    try:
        DIRECTOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DIRECTOR_LOG.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass

# SP skill root(基于 marketplace 5.0.7 的实际路径)
SP_ROOT_CANDIDATES = [
    Path.home() / ".claude" / "plugins" / "cache" / "superpowers-marketplace" / "superpowers",
    Path.home() / ".claude" / "plugins" / "cache" / "claude-plugins-official" / "superpowers",
]

# gstack / ECC skills 直接住在 ~/.claude/skills/<name>/SKILL.md
USER_SKILLS_ROOT = Path.home() / ".claude" / "skills"

# gstack: gs_role → skill name
GS_ROLE_TO_SKILL = {
    "EngManager": "plan-eng-review",
    "CEO": "plan-ceo-review",
    "QA": "plan-eng-review",  # QA 沿用 eng-review 的 forcing question
    "Designer": "design-consultation",
    "DocEngineer": "office-hours",  # doc 类问题走 office-hours 头脑风暴
}
GS_DEFAULT_SKILL = "office-hours"  # gs_role=null 时默认 office-hours

# ECC: ecc_subskill → skill name
ECC_SUB_TO_SKILL = {
    "research": "deep-research",
    "debug": "investigate",
    "database": "database-migrations",
    "security": "investigate",  # ECC security 复用 investigate
    "memory": None,  # memory 是轻量系统类,不派 sub-agent
    "other": "investigate",
}
ECC_DEFAULT_SKILL = "investigate"


def _resolve_sp_skill(skill_name: str) -> Optional[str]:
    """找 SP skill 的 SKILL.md 路径。返回字符串或 None。"""
    for root in SP_ROOT_CANDIDATES:
        if not root.exists():
            continue
        versions = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
        for v in versions:
            skill_path = v / "skills" / skill_name / "SKILL.md"
            if skill_path.exists():
                return str(skill_path)
    return None


def _resolve_user_skill(skill_name: str) -> Optional[str]:
    """找 gstack/ECC skill 在 ~/.claude/skills/<name>/SKILL.md 的路径。"""
    if not skill_name:
        return None
    p = USER_SKILLS_ROOT / skill_name / "SKILL.md"
    return str(p) if p.exists() else None


def _pick_sp_skill(prompt: str) -> str:
    """从 prompt 关键词挑 SP 子 skill。默认 TDD。"""
    p = prompt.lower()
    if any(k in prompt for k in ["头脑风暴", "brainstorm", "想想", "讨论"]):
        return "brainstorming"
    if any(k in p for k in ["debug", "bug", "为什么.*报错", "异常", "stack"]):
        return "systematic-debugging"
    if any(k in p for k in ["plan", "规划", "拆任务"]):
        return "writing-plans"
    return "test-driven-development"


def build_dispatch_instruction(prompt: str, decision: dict) -> dict:
    """
    返回 dict:
      - mode: "dispatch" | "inject" | "none"
      - text: 注入到主 session 的指令文本(给 Claude 看)
      - sub_agent_prompt: 若 mode=dispatch,sub-agent 收到的实际 prompt
      - reason: 为什么走这条路径
    """
    task_type = decision.get("task_type", "simple")
    fw = decision.get("framework_primary", "CC")
    gs_role = decision.get("gs_role")
    ecc_sub = decision.get("ecc_subskill")
    confidence = decision.get("confidence", 0)
    mode = _load_mode()

    base_event = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "prompt_hash": str(abs(hash(prompt)))[:10],
        "framework": fw,
        "task_type": task_type,
        "confidence": confidence,
        "director_mode": mode,
    }

    # 决定 result(还不返回,先汇总到 base_event 一起埋点)
    if mode == "off":
        result = _fallback_inject(fw, "mode_off", decision)
    elif task_type == "simple" or fw == "CC":
        result = {
            "mode": "none",
            "text": "",
            "sub_agent_prompt": None,
            "reason": "simple_task_no_dispatch",
        }
    elif mode == "dispatch_high_conf" and confidence < HIGH_CONF_THRESHOLD:
        result = _fallback_inject(fw, f"low_conf_{confidence:.2f}<{HIGH_CONF_THRESHOLD}", decision)
    elif fw == "SP":
        skill_name = _pick_sp_skill(prompt)
        skill_path = _resolve_sp_skill(skill_name)
        result = (
            _fallback_inject(fw, "SP_skill_not_found", decision)
            if not skill_path
            else _make_dispatch(
                fw="SP", skill_name=skill_name, skill_path=skill_path,
                prompt=prompt, description=f"SP {skill_name} sub-task",
                workflow_hint="5-phase TDD / brainstorming / systematic debugging — strictly follow phases",
            )
        )
    elif fw == "GS":
        skill_name = GS_ROLE_TO_SKILL.get(gs_role, GS_DEFAULT_SKILL)
        skill_path = _resolve_user_skill(skill_name)
        result = (
            _fallback_inject(fw, f"GS_skill_not_found:{skill_name}", decision)
            if not skill_path
            else _make_dispatch(
                fw="GS", skill_name=skill_name, skill_path=skill_path,
                prompt=prompt, description=f"GS {skill_name} sub-task (role={gs_role or 'default'})",
                workflow_hint="gstack forcing-question + Garry Tan 视角的判断,先问再做,不直接执行",
            )
        )
    elif fw == "ECC":
        skill_name = ECC_SUB_TO_SKILL.get(ecc_sub, ECC_DEFAULT_SKILL)
        if skill_name is None:
            result = _fallback_inject(fw, f"ECC_lightweight:{ecc_sub}", decision)
        else:
            skill_path = _resolve_user_skill(skill_name)
            result = (
                _fallback_inject(fw, f"ECC_skill_not_found:{skill_name}", decision)
                if not skill_path
                else _make_dispatch(
                    fw="ECC", skill_name=skill_name, skill_path=skill_path,
                    prompt=prompt, description=f"ECC {skill_name} sub-task (sub={ecc_sub or 'default'})",
                    workflow_hint="ECC 领域 task: 调研/调试/数据库分析,可能需要 MCP 工具,允许较长执行时间",
                )
            )
    else:
        result = _fallback_inject(fw, f"unknown_framework:{fw}", decision)

    # 统一埋点
    _log_event({
        **base_event,
        "dispatch_mode": result["mode"],
        "reason": result["reason"],
    })
    return result


def _make_dispatch(fw, skill_name, skill_path, prompt, workflow_hint, description):
    # Day 5/7: OUTCOME envelope is the HARD contract; PROGRESS is best-effort.
    # Empirical: strict PROGRESS schema (phase=N/TOTAL) is not reliably followed
    # across SP/GS/ECC; loosened to free-form milestone strings.
    sub_prompt = (
        f"You are a Director-Worker sub-agent.\n\n"
        f"# OUTCOME CONTRACT (REQUIRED — main session parses this)\n\n"
        f"End your reply with exactly this envelope:\n\n"
        f"  [OUTCOME status=success|partial|failed]\n"
        f"  <1 paragraph: what you did + the actual result the user asked for.>\n"
        f"  [/OUTCOME]\n\n"
        f"For tasks that produce a deliverable (code/answer/decision), put the DELIVERABLE\n"
        f"itself before the OUTCOME envelope, not in it. The envelope summarizes; the body delivers.\n\n"
        f"# Progress (optional, best-effort)\n\n"
        f"If the task spans clear milestones, you MAY emit single-line markers:\n"
        f"  [STATUS] <short milestone description>\n"
        f"Skip if not natural. Main session does not require these.\n\n"
        f"# Workflow\n\n"
        f"1. Read {skill_path}\n"
        f"2. Follow it — {workflow_hint}\n"
        f"3. Execute the user task using tools.\n\n"
        f"# User task\n\n{prompt}\n"
    )
    text = (
        f"🧭 Router → {fw} [Director-Worker dispatch] (skill={skill_name})\n"
        f"   [ACTION REQUIRED] Dispatch via Agent tool:\n"
        f"     subagent_type: general-purpose\n"
        f"     model: sonnet\n"
        f"     description: {description}\n"
        f"     prompt: <see sub_agent_prompt below>\n"
        f"   Supervisor protocol (main session MUST follow):\n"
        f"     1. Parse the [OUTCOME status=...] envelope from Agent's return (hard contract).\n"
        f"     2. If status=failed OR envelope missing OR Agent returns empty:\n"
        f"        Fallback to v3.1 inject — invoke Skill('{skill_name}') in main session.\n"
        f"     3. If status=success: keep the deliverable body intact for the user;\n"
        f"        the OUTCOME paragraph is a summary, not a replacement for the work.\n"
        f"     4. Surface any [STATUS] lines as inline progress (best-effort, may be absent).\n"
        f"     5. After step 1-3, RUN this Bash to record outcome (one-line, fire-and-forget):\n"
        f"        ~/router-eval-share/hook/director-record-outcome --status <success|partial|failed|empty> \\\n"
        f"          --framework {fw} --skill {skill_name} \\\n"
        f"          --tokens <from Agent usage> --duration-ms <from Agent usage> --tool-uses <from Agent usage> \\\n"
        f"          [--fallback-to inject|none]"
    )
    return {
        "mode": "dispatch",
        "text": text,
        "sub_agent_prompt": sub_prompt,
        "reason": f"{fw}_dispatch_skill={skill_name}",
    }


def _fallback_inject(fw: str, reason: str, decision: dict) -> dict:
    """回退:不派 sub-agent,让 router.py 走原 v3.1 inject 路径。
    返回 mode=inject 的标记,告诉 router.py 用现有 render_injection。"""
    return {
        "mode": "inject",
        "text": "",
        "sub_agent_prompt": None,
        "reason": f"fallback_to_inject:{reason}",
    }

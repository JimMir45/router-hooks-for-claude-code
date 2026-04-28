"""
task_classifier — Director-Worker 第一步:把 router 的 framework 决策映射成 task_type + dispatch_target

输入: prompt + router.py 出的 decision dict
输出: 给 decision 加 2 个字段:
  - task_type: execution | decision | domain | simple
  - dispatch_target: 字符串,标记"应派给谁"(本期不真派,只标注)

设计原则(Day 2):
  1. 默认按 framework_primary 1:1 映射(简单可靠)
  2. 几条 override 规则处理边缘情况(offline / 评审类 / 诊断类)
  3. 不调 LLM,纯规则,延迟 < 1ms
  4. Director-Worker 真派 sub-agent 在 Day 3+ 实现,这一步只标注
"""

import re

# framework_primary → task_type 默认映射
FW_TO_TYPE = {
    "SP": "execution",
    "GS": "decision",
    "ECC": "domain",
    "CC": "simple",
}

# 应派给谁(描述性,Day 3 真派 sub-agent 时用)
DISPATCH_MAP = {
    "execution": "superpowers:tdd / superpowers:brainstorming / SP 5-phase sub-agent",
    "decision": "gstack:office-hours / gstack:plan-ceo-review / gstack:plan-eng-review",
    "domain": "ECC sub-skill (research/debug/security/database/memory)",
    "simple": "main session (no sub-agent)",
}

# Override 信号:即使 framework 路对,某些 prompt 模式应改 task_type
REVIEW_PATTERNS = re.compile(r"(审|review|检查|看看|帮我看|过一下)", re.IGNORECASE)
DEBUG_PATTERNS = re.compile(r"(bug|报错|错误|异常|crash|hang|不工作|为啥|为什么.*失败|stack ?trace)", re.IGNORECASE)
RESEARCH_PATTERNS = re.compile(r"(调研|research|了解一下|有哪些|对比|什么是)", re.IGNORECASE)


def classify(prompt: str, decision: dict) -> dict:
    """返回 {task_type, dispatch_target, classifier_reason}。
    不修改入参,产出独立 dict 给 router.py 合并。
    """
    fw = decision.get("framework_primary", "CC")
    offline = decision.get("offline_topic", False)
    ecc_sub = decision.get("ecc_subskill")
    gs_role = decision.get("gs_role")

    # 优先级 1: offline → simple(不动用任何框架)
    if offline:
        return _result("simple", "offline_topic")

    # 优先级 2: framework 默认映射
    base_type = FW_TO_TYPE.get(fw, "simple")

    # 优先级 3: 信号 override
    # GS 但 prompt 是评审/调试 → 领域类(给 ECC 而非 GS forcing question)
    if fw == "GS" and DEBUG_PATTERNS.search(prompt):
        return _result("domain", "GS_routed_but_debug_signal")
    if fw == "GS" and REVIEW_PATTERNS.search(prompt) and ecc_sub:
        return _result("domain", f"GS_routed_but_review+ecc_sub={ecc_sub}")

    # ECC 但有调研意图且其他都没说 → 仍是 domain
    # ECC 但 ecc_subskill=memory 这种系统类 → simple(不需要重型 sub-agent)
    if fw == "ECC" and ecc_sub == "memory":
        return _result("simple", "ecc_memory_is_lightweight")

    # SP 但 confidence < 0.5 → 降级为 simple(SP 接管成本高,低置信不值得)
    if fw == "SP" and decision.get("confidence", 1.0) < 0.5:
        return _result("simple", "SP_low_confidence_demote")

    return _result(base_type, f"default_from_fw={fw}")


def _result(task_type: str, reason: str) -> dict:
    return {
        "task_type": task_type,
        "dispatch_target": DISPATCH_MAP[task_type],
        "classifier_reason": reason,
    }

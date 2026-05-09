#!/usr/bin/env python3
"""
Stop hook: completion sanity check.
Implements rules/common/autonomy-rules.md §自验 vs 你验.

Goal: catch假宣告完成 — when Claude says 'done' but:
  (a) used hedging words: 应该OK / 估计 / 看起来 / 可能没问题
  (b) active-plan.md still has unchecked [Agent 自验] items

Block decision injects a reason string back into Claude, asking it to
actually run the verification before claiming completion.

Exit semantics:
  - exit 0 + empty stdout = pass
  - JSON {"decision":"block","reason":"..."} = block, force re-think
"""
import json
import os
import re
import sys
import time
from pathlib import Path

LOG = Path.home() / ".claude" / "router-logs" / "completion-check.log"
LOG.parent.mkdir(parents=True, exist_ok=True)
MODE_FILE = Path.home() / ".config" / "router-hook" / "mode"


def load_mode():
    try:
        return MODE_FILE.read_text().strip().lower()
    except Exception:
        return "silent"


# Hedging words that often hide unverified claims.
# v2.1 (2026-04-27): added English hedges (should/probably/likely) and "没问题" as completion-state word.
# v2 → v2.1: B2 unit test (80 cases) found 2 FN — both fixed here.
HEDGE_PATTERNS = [
    # (a) 完成宣告 + 模糊词紧邻(中文 + 英文)
    (re.compile(r"(完成|done|完毕|搞定|结束|跑完|跑通|改完|修完|done了|finished|fixed|实现完|做完)[^\n]{0,25}?(应该|估计|看起来|可能|大概|大概率|理论上|should|probably|likely|maybe|perhaps|might\s*be)", re.I),
     "完成宣告含模糊词"),
    (re.compile(r"(应该|估计|看起来|可能|大概|理论上|should|probably|likely|maybe|might\s*be)[^\n]{0,25}?(完成了|done\s*了?|完毕了|搞定了|跑完了|跑通了|改完了|修完了|做完了|finished|fixed|ok|fine|good|work|no\s*issue)", re.I),
     "完成宣告含模糊词(模糊词在前)"),
    # (b) 紧凑乐观短语 — "应该没问题 / should be ok / 估计搞定 / 看起来没事"
    (re.compile(r"(应该|估计|看起来|大概|should|probably|likely|maybe|might\s*be)[^\n]{0,3}?(没问题|没事|没错|没毛病|不会有问题|ok\s*了?|fine|good|可以了|搞定了|没什么问题|no\s*issue|all\s*good)", re.I),
     "未验证的乐观断言(含'应该没问题/should be ok'套语)"),
    # (c) 显式说"没自验/没跑测试"还宣告完成(完成动作 + 完成状态都覆盖)
    (re.compile(r"(没.{0,3}(测试|verify|跑|试|验证|run|tested)).{0,30}?(完成|done|搞定|可以|没问题|没事|fine|ok|work|fixed)", re.I),
     "未自验就宣告完成"),
]


# 剥 ```code``` 和 `inline` 块,防止函数名/变量名/示例代码里的关键词被误触发
CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```|`[^`\n]*`')


def strip_code_blocks(text: str) -> str:
    """把 markdown 代码块/inline code 替换为空格,保留长度避免破坏后续匹配的偏移。"""
    return CODE_BLOCK_RE.sub(lambda m: ' ' * len(m.group(0)), text)


def hedge_check(last_assistant_text: str):
    if not last_assistant_text:
        return None
    text_for_match = strip_code_blocks(last_assistant_text)
    for rx, label in HEDGE_PATTERNS:
        m = rx.search(text_for_match)
        if m:
            return {
                "decision": "block",
                "reason": f"自验未过: 检测到[{label}]\n"
                          f"按 autonomy-rules.md §自验 vs 你验,完成宣告必须基于退出码可判断的脚本。\n"
                          f"请实际跑 verify/test 命令并贴出 exit code,而不是说'应该 OK'。",
                "_pattern_label": label,
                "_matched_text": m.group(0)[:120],
            }
    return None


def detect_execution_mode(transcript_path: str, lookback: int = 5) -> str:
    """Scan recent transcript for tool uses.
    - 'execution' if found Edit/Write/Bash/MultiEdit in last N assistant turns
    - 'discussion' otherwise (long pure-discussion tail downgrades to discussion mode)
    Returns 'unknown' if transcript unreadable.

    lookback default tightened from 30 → 5 (2026-05-09): a long session that started
    with code edits then shifted to pure discussion was being misclassified as execution
    because tool_use blocks lingered in the 30-turn window. 5 turns is enough to track
    the immediate work mode without dragging in stale execution traces.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return "unknown"
    EXECUTION_TOOLS = {"Edit", "Write", "Bash", "MultiEdit", "NotebookEdit"}
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except Exception:
        return "unknown"
    asst_seen = 0
    for line in reversed(lines[-200:]):  # 上限避免超长 transcript
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "assistant":
            continue
        asst_seen += 1
        msg = obj.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    if c.get("name") in EXECUTION_TOOLS:
                        return "execution"
        if asst_seen >= lookback:
            break
    return "discussion"


# === Approval-Gated Continue Nudge (2026-05-09) ===
# 软提示:用户已对齐方案 + agent 还在执行 + 没说完 → 在 Stop 时注入"继续推进"
# 解决"用户每次都要手动说'继续干'"的痛点。
NUDGE_COOLDOWN_FILE = Path("/tmp/.completion-check-nudge-cooldown")
NUDGE_COOLDOWN_SEC = 120

# 用户表达"按你方案干"的关键词。刻意偏严:不收"好""对""行"等过短语句,避免误判。
APPROVAL_PATTERNS = re.compile(
    r"(继续(干|做|推进|跑)?|干吧|开干|开始(干|做)?|"
    r"按你?(说的|的来|的方案|的思路|提议)|按这(个|思路|方案|来)|"
    r"没问题(就|,)|就这样(干|办)?|一起干|一起做|去做|执行)",
    re.I,
)
# 完成/收尾标记 — 命中即不 nudge(尊重 agent 真完成的判断)
COMPLETION_MARKERS = re.compile(
    r"(已完成|已搞定|已收工|跑通了|跑完了|改完了|修完了|做完了|"
    r"全部完成|全部通过|全部 ok|exit:?\s*0|✅\s*$|"
    r"finished|all\s*done|all\s*set|done\s*[\.。]?$)",
    re.I,
)


def get_recent_user_messages(transcript_path, n: int = 5):
    """返回最近 n 条 user 消息文本(从新到旧)。"""
    if not transcript_path or not os.path.exists(transcript_path):
        return []
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in reversed(lines[-200:]):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "user":
            continue
        msg = obj.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    out.append(c.get("text", ""))
        elif isinstance(content, str):
            out.append(content)
        if len(out) >= n:
            break
    return out


def should_nudge_continue(transcript_path: str, last_assistant_text: str):
    """判断是否注入"继续推进"软提示。返回 dict (block + reason) 或 None。
    五个条件全过才推:
      1. 最后一条 assistant 文本不是完成宣告
      2. 最后一条 assistant 文本不在向用户提问
      3. 最近 5 条用户消息至少 1 条含 approval 词
      4. exec_mode 是 execution(说明在干活,不是纯讨论)
      5. cooldown 没激活(防 loop)
    """
    if not last_assistant_text:
        return None
    text_for_match = strip_code_blocks(last_assistant_text)
    last_500 = text_for_match[-500:]

    # 1. 完成宣告 → 放行
    if COMPLETION_MARKERS.search(last_500):
        return None

    # 2. 在问用户 → 放行
    tail_200 = text_for_match[-200:]
    if "?" in tail_200 or "?" in tail_200:
        return None

    # 3. 最近 5 条用户消息找 approval
    recent_user = get_recent_user_messages(transcript_path, n=5)
    if not any(APPROVAL_PATTERNS.search(m) for m in recent_user):
        return None

    # 4. 必须是执行模式
    if detect_execution_mode(transcript_path, lookback=5) != "execution":
        return None

    # 5. cooldown
    now = int(time.time())
    try:
        last = int(NUDGE_COOLDOWN_FILE.read_text().strip())
        if now - last < NUDGE_COOLDOWN_SEC:
            return None
    except Exception:
        pass
    try:
        NUDGE_COOLDOWN_FILE.write_text(str(now))
    except Exception:
        pass

    return {
        "decision": "block",
        "reason": (
            "看起来还在执行,刚才已对齐过方案。如有未做完的步骤,直接继续;"
            "如真已完成,turn 结束即可,不必再回复。"
        ),
        "_pattern_label": "approval-gated-continue-nudge",
        "_matched_text": "(soft nudge)",
    }


def active_plan_check(transcript_path: str):
    """If cwd has active-plan.md with unchecked [Agent 自验] items, block."""
    # transcript_path is in /tmp, try to find cwd from latest user message? skip.
    # Instead, check current working dir
    cwd = os.getcwd()
    plan = Path(cwd) / ".claude" / "active-plan.md"
    if not plan.exists():
        return None
    try:
        text = plan.read_text()
    except Exception:
        return None
    # Find lines with `[Agent 自验]` and check if checkbox is `[ ]`
    unchecked = re.findall(r"^\s*-\s*\[\s*\].*\[Agent\s*自验\].*$", text, re.M)
    if unchecked:
        return {
            "decision": "block",
            "reason": f"active-plan.md 还有 {len(unchecked)} 项 [Agent 自验] 未勾选:\n"
                      + "\n".join(f"  • {u.strip()[:120]}" for u in unchecked[:5])
                      + "\n请逐项跑验证脚本,通过后再勾选,然后再宣告完成。",
        }
    return None


def get_last_assistant_text(transcript_path: str) -> str:
    """Read last assistant message from transcript jsonl."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
        # walk back to find last assistant text
        for line in reversed(lines[-40:]):
            try:
                obj = json.loads(line)
                if obj.get("type") == "assistant":
                    msg = obj.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                return c.get("text", "")
                    elif isinstance(content, str):
                        return content
            except Exception:
                continue
    except Exception:
        return ""
    return ""


def log(decision, mode_hint: str = ""):
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "decision": decision.get("decision", "pass") if decision else "pass",
            "reason": decision.get("reason", "")[:200] if decision else "",
        }
        if decision:
            if decision.get("_pattern_label"):
                entry["pattern_label"] = decision["_pattern_label"]
            if decision.get("_matched_text"):
                entry["matched_text"] = decision["_matched_text"]
        if mode_hint:
            entry["exec_mode"] = mode_hint
        with open(LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main():
    if load_mode() == "off":
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    # Avoid infinite loops: if Stop hook itself is the trigger, don't re-block
    if payload.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = payload.get("transcript_path", "")
    last_text = get_last_assistant_text(transcript_path)
    mode = detect_execution_mode(transcript_path)

    for check_name, check in (
        ("hedge", lambda: hedge_check(last_text)),
        ("active_plan", lambda: active_plan_check(transcript_path)),
    ):
        result = check()
        if not result or result.get("decision") != "block":
            continue
        # 讨论模式 + hedge 类拦截 → 降级为 pass(active_plan 仍 block,因为 plan 跨模式都该卡)
        if mode == "discussion" and check_name == "hedge":
            # 不 print decision(放行),但留下日志便于复盘
            result_pass = {
                "decision": "pass",
                "reason": f"[downgraded from block] mode=discussion, label={result.get('_pattern_label','?')}",
                "_pattern_label": result.get("_pattern_label"),
                "_matched_text": result.get("_matched_text"),
            }
            log(result_pass, mode_hint=mode)
            sys.exit(0)
        log(result, mode_hint=mode)
        print(json.dumps({"decision": result["decision"], "reason": result["reason"]},
                         ensure_ascii=False))
        sys.exit(0)

    # Continue nudge — 防御性的"对齐后没做完就停"软提示
    nudge = should_nudge_continue(transcript_path, last_text)
    if nudge:
        log(nudge, mode_hint=mode)
        print(json.dumps({"decision": nudge["decision"], "reason": nudge["reason"]},
                         ensure_ascii=False))
        sys.exit(0)

    log(None, mode_hint=mode)
    sys.exit(0)


if __name__ == "__main__":
    main()

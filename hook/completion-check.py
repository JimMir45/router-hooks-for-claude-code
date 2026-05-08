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


def hedge_check(last_assistant_text: str):
    if not last_assistant_text:
        return None
    for rx, label in HEDGE_PATTERNS:
        m = rx.search(last_assistant_text)
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


def detect_execution_mode(transcript_path: str, lookback: int = 30) -> str:
    """Scan recent transcript for tool uses.
    - 'execution' if found Edit/Write/Bash/MultiEdit in last N assistant turns
    - 'discussion' otherwise
    Returns 'unknown' if transcript unreadable."""
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

    log(None, mode_hint=mode)
    sys.exit(0)


if __name__ == "__main__":
    main()

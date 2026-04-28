#!/usr/bin/env bash
# uninstall-director.sh — 一键回滚 Director-Worker 改造
# 目标: 把 router.py 还原到 Day 1 之前的 v3.1 状态,删除 Day 2-5 新增模块
# 验收门槛: < 10s 完成

set -e
START=$(date +%s.%N)

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
ROUTER="$HOOK_DIR/router.py"

# 备份当前 router.py(rollback-of-rollback 留一手)
cp "$ROUTER" "$ROUTER.preuninstall.bak"

# 用 Python 做安全的 fence 删除 + 行替换
python3 - "$ROUTER" <<'PY'
import re
import sys
from pathlib import Path

p = Path(sys.argv[1])
src = p.read_text()

# 1. 删除 PATCH 块(import 注入)
src = re.sub(
    r"# === DIRECTOR-WORKER PATCH START ===.*?# === DIRECTOR-WORKER PATCH END ===\n",
    "",
    src,
    flags=re.DOTALL,
)

# 2. 删除 ANNOTATE 块(decision.update)
src = re.sub(
    r"\s*# === DIRECTOR-WORKER ANNOTATE START ===.*?# === DIRECTOR-WORKER ANNOTATE END ===\n",
    "",
    src,
    flags=re.DOTALL,
)

# 3. 替换 DISPATCH 块为原 v3.1 行
# fence 内首行注释 # Original-v3.1: <code> 给我们要还原的代码
m = re.search(
    r"# === DIRECTOR-WORKER DISPATCH START ===\s*\n\s*# Original-v3\.1:\s*(.+?)\n.*?# === DIRECTOR-WORKER DISPATCH END ===",
    src,
    flags=re.DOTALL,
)
if m:
    original_line = m.group(1).strip()
    # 保持原缩进 8 空格
    src = src[:m.start()] + original_line + src[m.end():]

p.write_text(src)
print(f"router.py reverted ({p})")
PY

# 删除新增模块
rm -f "$HOOK_DIR/task_classifier.py"
rm -f "$HOOK_DIR/dispatch_subagent.py"
rm -f "$HOOK_DIR/__pycache__/task_classifier.cpython-"*.pyc 2>/dev/null || true
rm -f "$HOOK_DIR/__pycache__/dispatch_subagent.cpython-"*.pyc 2>/dev/null || true

# 健全检查:回滚后 router.py 仍可 import + 运行 --help / 无入参
python3 -c "
import ast, sys
src = open('$ROUTER').read()
ast.parse(src)  # 语法对吗
assert 'DIRECTOR-WORKER' not in src, 'Director-Worker fence remnant'
assert 'task_classifier' not in src, 'task_classifier import remnant'
assert 'dispatch_subagent' not in src, 'dispatch_subagent import remnant'
print('post-uninstall syntax+残留 OK')
"

END=$(date +%s.%N)
ELAPSED=$(python3 -c "print(f'{${END}-${START}:.3f}')")
echo "Director-Worker uninstall complete in ${ELAPSED}s"
echo "  Backup: $ROUTER.preuninstall.bak"
echo "  Removed: task_classifier.py, dispatch_subagent.py"
echo "  Re-install: git restore from main branch"

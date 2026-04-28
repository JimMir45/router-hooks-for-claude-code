# Router Hook for Claude Code

[English](README.md) · **中文**

一套 **Claude Code hook 系统**:每次你输入一句话,自动用 5 层决策树判断意图、选对应的 AI 工作流;它干活时硬规则拦截危险操作、自动停下连续失败;它说"完成了"时强制核验。

**目标**:让 Claude 少问你"该不该做",多干"做对的事"。

---

## 架构

3 个阶段覆盖 AI Agent 工作的全周期:

```
阶段 1: 入口                 阶段 2: 运行时              阶段 3: 终点
UserPromptSubmit            PreToolUse / Failure         Stop
─────────────────           ──────────────────────       ─────────────────
router.py                   runtime-guard.py             completion-check.py
                            failure-tracker.py

你输入一句话                 Claude 准备调工具            Claude 说"完成了"
      ↓                            ↓                            ↓
5 层决策树选框架:           黑名单检查:                  模糊词检查:
  SP  → Superpowers           • rm -rf / DROP SQL          • "应该 OK"
  GS  → gstack 角色            • git push --force           • "估计可以"
  ECC → deep-research          • 邮件/IM API                • 未验证的断言
  CC  → 原生 Claude           连续失败熔断:                active-plan 校验:
                                 • 同一工具连续 3 次失败       • 自验项还有未勾选
                              范围溢出检测:
                                 • 修改 active-plan 范围外的文件
```

---

## 前置依赖

| 依赖 | 版本 | 说明 |
|---|---|---|
| [Claude Code](https://claude.ai/code) | 任何版本 | Claude Code 命令行工具(`claude` 命令) |
| Python 3 | 3.8+ | 仅用标准库,不需要 pip 安装 |
| API key | — | 任何 OpenAI 兼容的 endpoint(OpenAI、Together.ai、本地 Ollama 等) |

---

## 安装

```bash
git clone https://github.com/JimMir45/router-hooks-for-claude-code
cd router-hooks-for-claude-code
./install.sh
```

或者一行搞定(信任源代码后):

```bash
git clone https://github.com/JimMir45/router-hooks-for-claude-code && cd router-hooks-for-claude-code && ./install.sh
```

安装脚本是幂等的 — 跑两遍没问题,不会覆盖你已有的配置。

---

## 5 行快速上手

```bash
# 1. 装完之后,填 API key:
cp ~/.config/router-hook/keys.json.example ~/.config/router-hook/keys.json
# 编辑 keys.json,填你的真实 key

# 2. 启动 Claude Code:
claude

# 3. 输入任意 prompt — router 在后台自动跑。
#    默认 silent 模式下,只在选中某个 framework 时才有提示。

# 4. 看 router 上一次的决策:
tail -1 ~/.claude/router-logs/router.log | python3 -m json.tool

# 5. 切换模式:
router-mode auto    # 啰嗦模式: 每次决策都打印
router-mode silent  # 安静模式: 只在需要提醒时才输出(默认)
router-mode off     # 全关
```

---

## 排错

**Hook 没触发**
- 重启 Claude Code(`claude`) — hook 只在新 session 生效
- 检查注册:`cat ~/.claude/settings.json | python3 -m json.tool | grep router`
- 重跑安装:`./install.sh`(幂等,安全)

**API 调用失败 / router 退回原生 CC**
- 检查 key:`cat ~/.config/router-hook/keys.json`
- 手动测 endpoint:
  ```bash
  curl -s https://api.openai.com/v1/chat/completions \
    -H "Authorization: Bearer YOUR_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hi"}],"max_tokens":5}'
  ```
- 看 router 日志的错误详情:`tail -5 ~/.claude/router-logs/router.log`

**Hook 拦了不该拦的**
- 看 runtime-guard 日志:`tail -10 ~/.claude/router-logs/runtime-guard.log`
- 临时关掉:`router-mode off`
- 如果黑名单 regex 太激进,提个 issue 把具体命令贴出来

**我想暂时关掉**
```bash
router-mode off    # 关掉 router + runtime-guard + completion-check
router-mode silent # 重新启用(默认)
```

**怎么彻底卸载?**
```bash
./uninstall.sh
```
会把所有 hook 从 `settings.json` 移除、删 `~/.router-hook/`、把 router 规则从 `CLAUDE.md` 拿掉。

---

## 配置

### API Provider(`~/.config/router-hook/keys.json`)

支持任何 OpenAI 兼容的 endpoint:

```json
{
  "primary": {
    "name": "openai",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "sk-..."
  },
  "fallback": {
    "name": "openai-fallback",
    "endpoint": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "key": "sk-..."
  }
}
```

更多例子(Together.ai、本地 Ollama、key-file 间接配置)见 `config/keys.json.example`。

### Router 模式

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `silent` | 只在需要 ACTION 时输出(默认) | 日常使用 |
| `auto` | 每次决策都打印路由结果 | 调试 / 评测 router |
| `off` | 完全关闭 | 想要原生 Claude,不要任何干预 |

---

## 文档

- `docs/router-spec-v3.md` — 5 层决策树规范 + 数据分析
- `docs/autonomy-rules.md` — 双态工作流 + 黑名单 + 验证规则
- `docs/decision-taxonomy.md` — 理论骨架(6 + 4 + 5 决策轴)
- `INSTALL.md` — 详细安装指南
- `docs/ARCHITECTURE.md` — 架构深度解析

---

## 测试与可复现性

- `tests/cases/` — 6 套测试 case 数据(runtime-guard 100 个 / completion-check 80 个 / E2E 12 个 / ECC 89 skill 159 个 / SP+gstack 35 个 / combo 边界 43 个)
- `tests/results/` — v3.0 baseline + v3.1 主稿数据,可逐项对比
- `tests/run-*.py` — 6 套 runner,自己跑可复现 — `python3 tests/run-runtime-guard.py` 之类

主要数字:
- Runtime-guard 单元测试:100/100
- Completion-check 单元测试:80/80
- E2E 12 工程师场景:12/12
- ECC 89 skill 触发覆盖:strict 40.4% / reasonable 89.9%
- SP+gstack 35 skill 触发覆盖:strict 62.9% / reasonable 80.0%
- Combo 边界 case:v3.0 70.0% → v3.1 93.3%(+23.3pp)

---

## License

MIT — 见 [LICENSE](LICENSE)。

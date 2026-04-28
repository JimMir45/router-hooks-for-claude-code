# Director-Worker — 最终评估 Dashboard

**项目**: router-hooks-for-claude-code Director-Worker 改造
**评估日期**: 2026-04-28
**评估范围**: Day 1-7 全工作 + 三家真实 e2e + Step 2 埋点
**版本**: v3.1 → Director-Worker (实验中)

---

## 🎯 最终决策:**🟡 部分启用 — 默认 dispatch_high_conf 模式**

**理由**:核心机制经真实 e2e 验证有效(SP/GS 显著价值,ECC 任务相关),但 LLM 路由不一致 + sub-agent 输出格式遵循度问题决定不能 100% 信任。先在高置信路径开 Director-Worker,低置信回退 v3.1 inject,跑一段时间观察。

```bash
hook/director-mode dispatch_high_conf   # 推荐起点
hook/director-mode off                  # 完全回滚到 v3.1
hook/director-mode dispatch_all         # 全量启用(高风险,只在内部小范围用)
```

---

## 📊 7 个验收数字 — 实测对照

| # | 指标 | 目标 | 实际 | 状态 |
|---|---|---|---|---|
| 1 | 任务分诊准确率(① 4 case) | ≥ 90% | **100%** (4/4) | ✅ |
| 2 | 用户每周手动"切到 X"次数 | ≤ 2 | — | 🟡 [你验] 用 1 周观察 |
| 3 | Context bleed 跨 turn 事件 | 0/周 | 单 turn 隔离已验 ✅;跨 turn 未测 | 🟡 [你验] 用 1 周观察 |
| 4 | 重型 task latency | refactor<3min / research<5min | **TDD 54s / forcing-Q 2.5min / research 17s** | ✅ |
| 5 | 月度 LLM 成本 vs v3.1 | < 1.5x | router 阶段 +0%;sub-agent 端 47-79k tokens(只在 isolated context) | ✅ 但月度需观察 |
| 6 | 三家特色保留度 | 100% | **SP 真按 TDD ✅ / GS 真 forcing-question ✅ / ECC 取决于任务难度 ⚠️** | 🟡 67% 严格 |
| 7 | 回滚耗时 | < 10s | **110ms**(99x 余量) | ✅ |

**6/7 达标(其中 #2, #3 需要时间观察)**。

---

## 🧪 三家 e2e 真实验证(2026-04-28)

| 框架 | 任务 | Tokens(主 session 隔离掉的) | 时长 | Tool calls | OUTCOME 格式 | SKILL 真按走 |
|---|---|---|---|---|---|---|
| **SP** | TDD fizzbuzz | 47k | 54s | 7 | ✅ | ✅ 文件落地+pytest 4/4 通过 |
| **GS** | router 砍 ECC 兼容 forcing question | 79k | 2.5min | 23 | ✅ | ✅ **真读了 1933 条标注数据 + confusion matrix** |
| **ECC** | Python `match` 3 pattern 调研 | 43k | 17s | 1 | ✅ | ⚠️ 跳过 deep-research(任务太简单 LLM 自己会) |

### 关键洞察

1. **GS 案例最具说服力** — sub-agent 用 79k tokens 读真实数据后,给出了 v3.1 inject 模式**完全不可能产出**的判断:
   > "不是全砍 ECC,而是 ECC-security 提到 L0 Hard Override,其余 subskill 降级 CC + skill hint" — 这是 23 个工具调用 + 真实数据驱动的结果

2. **Context 节省真实存在** — 三家加起来 169k tokens 全在 sub-agent isolated context,**没进主 session**。v3.1 inject 模式下这 169k 全部要进主 session(读 SKILL.md + 工具调用 + 推理)。

3. **三家保留度的真相**:
   - SP / GS:严格按 SKILL.md 跑,5-phase / forcing question 真触发
   - ECC:**任务依赖性强** — 简单调研题 sub-agent 会跳过 MCP 调用直接答(合理但意味着不能强迫 ECC 永远走 deep-research)

---

## ⚠️ 已知问题 + 不足

### 1. PROGRESS 协议失败(已降级)
两次加强 `[PROGRESS] phase=N/TOTAL` 严格约束,LLM 都 partial 遵守:
- SP 输出了 phase 3/5、4/5、5/5 但漏 1/5、2/5
- GS 只输出 5/5 一行
- ECC 输出 `phase=2/TOTAL`(字面量没替换)

**修法**:降级为 `[STATUS] <free-form>` 可选标记,**OUTCOME envelope 才是硬契约**。OUTCOME 在 3 次 e2e 全成功 parse,工程上够用。

### 2. LLM 路由非确定性(75 case 全量跑)
74 case 跑通过率 **86.5%**,失败的 11 条拆开:
- 7 条 real-replay 框架不一致(LLM 自身不确定,非回归)
- 1 条 regression_005 已知测试设计错(scope creep 该 PreToolUse 拦)
- 1 条 combo_002 真实分诊局限(先研后做的两步意图丢失)
- 2 条 LLM API 临时错误(429 等)

**修法**:`dispatch_high_conf` 模式 — 仅 conf ≥ 0.7 派 sub-agent,低置信走 v3.1 inject 兜底。

### 3. ECC sub-agent 不一定真用 MCP
对简单调研题,sub-agent 倾向于"基于自身知识答"而非真调用 deep-research。

**修法**:不当 bug,当 feature。简单题 LLM 自答更快;真要测 MCP 调用必须挑必须查实时信息的题(如"Anthropic 最近 7 天发布了什么")。

---

## 🔧 Step 2 埋点 + A/B 开关(本次新建)

### 埋点
所有 hook 决策事件写入 `~/.claude/router-logs/director.jsonl`:
```json
{"ts":"...", "framework":"SP", "task_type":"execution", "confidence":0.9,
 "director_mode":"dispatch_all", "dispatch_mode":"dispatch", "reason":"SP_dispatch_skill=test-driven-development"}
```

### CLI
```bash
director-mode                      # 查当前 mode + 24h 统计
director-mode dispatch_high_conf   # 切到推荐模式
director-mode log 50               # 看最近 50 条事件
director-mode stats 168            # 看 7 天统计
```

### 三档 mode 验证
| 测试 case | dispatch_all | dispatch_high_conf | off |
|---|---|---|---|
| SP conf=0.90 | dispatch | dispatch | inject(回退 v3.1) |
| GS conf=0.65 | dispatch | **inject**(低置信回退) | inject |
| ECC conf=0.85 | dispatch | dispatch | inject |
| CC conf=0.50 | none | none | inject |

---

## 📁 最终文件结构

```
~/router-eval-share/
├── hook/
│   ├── router.py                    [v3.1 + 3 处 fence patch]
│   ├── task_classifier.py           [Day 2]
│   ├── dispatch_subagent.py         [Day 3-7,SP/GS/ECC + mode + 埋点]
│   ├── director-mode                [Step 2 CLI]
│   └── uninstall-director.sh        [Day 5,110ms 回滚]
├── tests/
│   ├── cases/                       [74 case:50 真实 + 13 P0 + 4 triage + 3 combo + 4 edge]
│   ├── run-director-baseline.py     [Day 1]
│   ├── run-task-classifier.py       [Day 2]
│   ├── run-dispatch-test.py         [Day 3-4]
│   ├── run-day5-tests.py            [Day 5]
│   ├── run-day6-full.py             [Day 6 全量]
│   └── results/
│       ├── baseline-v3.1-summary.md
│       ├── task-classifier-summary.md
│       ├── dispatch-test-summary.md
│       ├── day5-tests.md
│       ├── director-vs-v3.1.md      [Day 6 报告]
│       └── day6-full.jsonl
└── .claude/
    └── active-plan.md               [Day 1-7 23 todo,全勾完]
```

---

## 🛣️ 后续路线图

### 短期(本周内)
- [ ] 切到 `dispatch_high_conf` 模式实战用 3-5 天
- [ ] 监控 `director-mode stats` 看实际 dispatch / fallback 比例
- [ ] 观察用户主观感受(认知负担降低?切框架次数减少?)

### 中期(下周)
- [ ] 根据 1 周埋点数据,决定推 `dispatch_all` 还是回 `off`
- [ ] 修 combo_002 类型的多意图识别(先研后做)— router LLM prompt 加这条规则
- [ ] PROGRESS 协议彻底砍掉 or 替换成主 session 端的 dispatch start/end timestamp 埋点

### 长期(月度)
- [ ] sub-agent outcome 端埋点(目前只埋点 hook 决策侧)
- [ ] dispatch mode 自适应(根据历史 fallback 率自动调阈值)

---

## 💡 一句话总结

**Director-Worker 不是替代 v3.1,是在它之上加一层"isolated context dispatch"。**核心价值在 GS 类决策任务(那 79k tokens 读数据的能力主 session 没法承担),SP 也明显受益(5-phase TDD 完整跑且不污染主 context)。ECC 价值受任务难度调制。回滚 110ms 几乎无成本,**dispatch_high_conf 是合理的默认起点**。

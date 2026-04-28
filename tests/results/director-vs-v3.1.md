# Director-Worker vs v3.1 — 全量评估报告

**生成时间**: 2026-04-28 21:24:17
**case 总数**: 74 (脚本可验) + ② ③ ⑤ ⑥ 部分需 [你验]
**通过**: 64/74 = 86.5%
**失败**: 10 | **跳过/无 expected**: 0

## 分类通过率

| 来源 | 通过/总数 | 通过率 |
|---|---|---|
| 真实日志 replay | 43/50 | 86% |
| P0 关键 case | 12/13 | 92% |
| ① 任务分诊 | 4/4 | 100% |
| ⑩ 多意图 | 2/3 | 67% |
| ⑪ 边缘格式 | 3/4 | 75% |

## 7 验收数字

| # | 指标 | 目标 | 实际 | 状态 |
|---|---|---|---|---|
| 1 | 任务分诊准确率(75 case) | ≥ 90% | **100%** (4/4) | ✅ |
| 2 | 用户每周手动"切到 X"次数 | ≤ 2 次 | — | 🟡 [你验] 需运行 1 周观察 |
| 3 | Context bleed 跨 turn 事件 | 0 起/周 | — | 🟡 [你验] 需 chat 实测 |
| 4 | 重型 task latency | refactor<3min / research<5min | router 平均 6171ms / p95 16875ms | 🟡 sub-agent 真跑 latency 待 chat 测 |
| 5 | 月度 LLM 成本 vs v3.1 | < 1.5x | router 阶段 +0%(共用 LLM)| 🟡 sub-agent 真跑后才能算月度 |
| 6 | 三家特色保留度 | 100% | dispatch text 含 SKILL.md 路径 + 5-phase/forcing/MCP 提示 | 🟡 [你验] chat 实跑 SP/GS/ECC sub-agent |
| 7 | 回滚耗时 | < 10s | **110ms** (Day 5 测) | ✅ |

## Context 节省估算

- v3.1 inject 平均文本: **131** 字符
- Director-Worker dispatch 平均文本: **1740** 字符 (Δ +1608)

> 注:dispatch text 比 inject 长一些(因为含 sub_agent_prompt + supervisor 协议),
> 但**真正的 context 节省**在于 sub-agent 跑掉的 SKILL.md / 中间步骤都不进主 session。
> 需要在 chat 里实跑才能测主 session token 占用降幅。

## 失败明细

| ID | category | expected | actual | prompt |
|---|---|---|---|---|
| real_002 | real-replay | GS/? | ECC/domain | 这个事是这样的，我们调研的目的呢，我是想知道ECCC是不是才是重点。就是我我的意我的这个决策意图集成到ECCC的整套流程里面。然后通过他的这套有效机制，把我们想… |
| real_003 | real-replay | GS/? | SP/execution | 帮我从头做一个技术分享的 web 演示稿,要有动效… |
| real_007 | real-replay | GS/? | SP/execution | 很好很好，再加一些其他的这个功能吧。比如说嗯我们这个主进程在起subagent，或者是在code的时候，能不能让它切到sonnat的模型去。我看我的usage全… |
| real_020 | real-replay | CC/? | ECC/domain | ECCC能覆盖我90%的需求，剩下10%是什么它就覆盖不了，是因为什么覆盖不了？… |
| real_029 | real-replay | CC/? | GS/decision | AI native 团队的开发流程应该怎么设计,和传统团队有什么不同… |
| real_032 | real-replay | ECC/? | CC/simple | 检查 /Users/jiangyi/LS/Project/AI-Rec/data/carplay/_transfer.stdout 的进度。如果 user_so… |
| real_036 | real-replay | ECC/? | SP/execution | 大重构之后用户操作路径不对,每个功能单独测没问题但组合起来不对… |
| regression_005 | v3.1-regression | CC/? | SP/execution | 在修这个 bug 的同时,顺便把整个 auth 模块和 session 管理都重构了吧,反正都要动… |
| combo_002 | combo-multi-intent | ?/domain | SP/execution | 先调研一下市面上 LRU 实现的几种思路,然后用 TDD 把最合适的实现出来… |
| edge_003 | edge-format | ECC/domain | ?/? | 这个函数为啥一直返回 undefined? ```typescript function findUser(id: number) {   const user… |

## [你验] 留单(脚本不能验,必须 chat 里手动测)

- ② 认知负担:连续 3 turn 不同任务自动切;同主题深入不切
- ③ Context 隔离:SP 跑完后改 typo / 中途切话题,主 session token 不增
- ⑤ 性能成本:重型 task latency / 月 LLM 成本对比
- ⑥ 三家特色保留:SP 5-phase 完整 / gstack forcing question 完整 / ECC MCP 真调用

**测法**:在 chat 主 session 实际发对应 prompt,看 sub-agent 是否真派 + 是否完整跑完。

## Day 7 决策点

🟡 **部分启用**(70-90%)— 仅对高置信路径开 Director-Worker,低置信回退 inject

实际脚本通过率: **86.5%**
⚠️ 决策必须等 [你验] 项也过了才算最终判定 — 脚本通过率只覆盖 router/dispatch 层。
# Task Classifier 测试结果

**总通过率**: 15/17 = 88.2%

## 分类目标

- ① 任务分诊 (synthetic-triage): **4/4** ✅
- P0 case 衍生 (p0-cases): **11/13**

## 失败明细

| ID | prompt | expected | actual | classifier_reason |
|---|---|---|---|---|
| regression_005 | 在修这个 bug 的同时,顺便把整个 auth 模块和 session 管理都重构了吧,反正都要动… | simple | execution | default_from_fw=SP |
| replay_001 | 帮我检查这个 PR 改了 CSS 之后有没有破坏设计系统的一致性… | decision | domain | GS_routed_but_review+ecc_sub=other |

## Day 2 验收门槛

- ① 任务分诊 4/4 必须过(active-plan 明文要求)
- 实际: **4/4** — ✅ 通过
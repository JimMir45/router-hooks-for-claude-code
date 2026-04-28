# Baseline v3.1 — P0 13 case 测试结果

**总通过率**: 11/13 = 84.6%

## 分类准确率

| 类别 | 通过/总数 | 通过率 |
|---|---|---|
| failure-mode | 2/3 | 67% |
| real-replay | 5/5 | 100% |
| v3.1-regression | 4/5 | 80% |

## 失败明细

### failure_003
- prompt: `用 office-hours 帮我理一下这个 idea 的可行性…`
- ❌ confidence_min: expected=0.7 actual=0.55

### regression_005
- prompt: `在修这个 bug 的同时,顺便把整个 auth 模块和 session 管理都重构了吧,反正都要动…`
- ❌ framework: expected=CC actual=SP

## 用作 Director-Worker 对照基线

Day 6 全量跑时,Director-Worker 的同组 case 准确率必须 ≥ 此基线。
低于基线意味着 Director-Worker 引入了回归。

# completion-check.py 测试报告

**运行时间**: 2026-04-27T16:29:57
**总耗时**: 1862 ms

## 汇总

| 指标 | 值 |
|------|-----|
| 总用例 | 80 |
| 通过 | 80 |
| 失败 | 0 |
| 准确率 | 100.0% |
| 误报 (FP: 应放行被拦截) | 0 |
| 漏报 (FN: 应拦截被放行) | 0 |

## 分类统计

| 类别 | 总计 | 通过 | 失败 |
|------|------|------|------|
| edge | 10 | 10 | 0 |
| hedge | 30 | 30 | 0 |
| pass_verified | 20 | 20 | 0 |
| plan_all_checked | 10 | 10 | 0 |
| plan_unchecked | 10 | 10 | 0 |
## 值得讨论的 Case

1. **case_071** (edge) — 空transcript,无内容
   - 期望 `pass`, 实际 `pass` → 通过

2. **case_072** (edge) — 只有user消息无assistant消息
   - 期望 `pass`, 实际 `pass` → 通过

3. **case_073** (edge) — content为空字符串
   - 期望 `pass`, 实际 `pass` → 通过

4. **case_074** (edge) — 应该+问句,非完成宣告
   - 期望 `pass`, 实际 `pass` → 通过

5. **case_075** (edge) — 可能+时间估计,非完成
   - 期望 `pass`, 实际 `pass` → 通过

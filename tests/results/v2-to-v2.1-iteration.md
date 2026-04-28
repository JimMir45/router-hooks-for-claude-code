# Completion-check v2 → v2.1 迭代

**真实测试驱动的修复案例**(可放演示页)。

## 时间线

| 时间 | 版本 | 测试结果 | 备注 |
|---|---|---|---|
| 2026-04-27 ~10:30 | v2 | — | 初始版本上线 |
| ~12:00 | v2 | **78/80 (97.5%)** | B2 单元测试发现 2 个 false negative |
| ~12:05 | v2.1 | **80/80 (100%)** | 修复后全过 |

## v2 漏的 2 条

### case_006 [FN]
```
text: "fixed, should be ok"
expect: block
v2 实际: pass(漏报)
```
**根因**:HEDGE_PATTERNS 只覆盖中文(应该/估计/看起来/可能/大概/理论上),英文 hedge 词(should/probably/likely/maybe)未在词表内。

### case_079 [FN]
```
text: "OK,没跑 verify 但代码没问题"
expect: block
v2 实际: pass(漏报)
```
**根因**:规则 (c)「未自验+完成」第三捕获组只有动作词(完成/done/搞定/可以),`没问题` 是状态词,语义等价但词法不同,落网。

## v2.1 修复

```python
# 三处改动:
# (a) 中文 hedge 词组扩 + 英文 should/probably/likely/maybe/perhaps/might be
# (b) 紧凑乐观短语补 fine/good/no issue/all good
# (c) 状态词加入完成判定:完成|done|搞定|可以|没问题|没事|fine|ok|work|fixed
```

## 验证

修复后**80/80 全过**:
- 80 测试用例全部 PASS
- v2 漏的 2 条都拦住
- 无新 false positive(讨论用语依然放行)

## 这个迭代的元价值

**测试驱动的真实证据**:不是"理论上应该好",是"测出来 2 个 bug → 修了 → 测了 → 真好了"。这正是这套系统主张的"自验未过不算完成"的现场示范。

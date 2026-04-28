# ECC 89-Skill 路由覆盖矩阵 — 分析摘要

> 生成于 2026-04-26 | 测试集: 159 个 prompt,覆盖 89 个已启用 ECC skill (每个 1-2 个 prompt)
> Router: `hook/router.py` (gpt-4o-mini via any OpenAI-compatible endpoint)

---

## 核心数字

| 指标 | 数值 |
|---|---|
| 总 skill 数 | 89 |
| 总测试 prompt 数 | 159 |
| **Skill 严格命中率** (路由到 ECC) | **30/89 = 33.7%** |
| Skill 合理命中率 (ECC/SP/GS 均可) | 73/89 = 82.0% |
| Case 严格命中率 | 25.8% (41/159) |
| Case 合理命中率 | 73.6% (117/159) |
| 错误路由分布 | GS: 59次, CC: 39次, SP: 17次, 无返回: 3次 |

**核心结论:** Router 对 ECC skill 的精确识别率仅 **33.7%**,但有 **82%** 的 skill 被路由到合理框架(ECC/SP/GS),而非退化到纯 CC 或完全失败。

---

## 按类别分析

| 类别 | Skill 数 | 严格命中 | 合理命中 |
|---|---|---|---|
| frontend_ux | 6 | 4/6 (67%) | 6/6 (100%) |
| language_frameworks | 14 | 7/14 (50%) | 13/14 (93%) |
| backend_infra | 11 | 5/11 (45%) | 11/11 (100%) |
| ai_llm_pipeline | 9 | 3/9 (33%) | 7/9 (78%) |
| ecc_meta_tools | 18 | 5/18 (28%) | 11/18 (61%) |
| agent_ai_tooling | 19 | 3/19 (16%) | 17/19 (89%) |
| content_media | 7 | 1/7 (14%) | 4/7 (57%) |

**最强**: frontend_ux 和 backend_infra 类 skill 路由合理率 100%
**最弱**: content_media 和 ecc_meta_tools 严格命中率最低

---

## 10 个最有意思的发现

### 1. Router 把大量 ECC skill 错判为 GS-EngManager (18 个 skill)
`agent-eval`, `api-design`, `autonomous-loops`, `deployment-patterns`, `enterprise-agent-ops` 等全部被路由到 GS-EngManager。说明 router 把"工程决策类问题"的模式匹配权重过高,凡是架构/设计/方案类问题都往 GS 走。

### 2. 16 个 skill 完全退化到原生 CC
这些 skill 的 prompt 被 router 认定"不需要框架",走了 CC:
- `ck`, `configure-ecc`, `content-engine`, `context-budget`, `continuous-learning`
- `crosspost`, `fal-ai-media`, `golang-patterns`, `nanoclaw-repl`, `plankton-code-quality`
- `prompt-optimizer`, `rules-distill`, `skill-comply`, `strategic-compact`, `verification-loop`

其中 `golang-patterns` 和 `content-engine` 是最意外的——这些应该是 ECC 最典型的使用场景。

### 3. `token-budget-advisor` 3 次测试均无返回 (空 framework)
该 skill 的触发 prompt 甚至无法让 router 产生有效决策,说明这个 skill 的使用场景太模糊或 prompt 设计问题。

### 4. `architecture-decision-records` 100% 路由到 GS-DocEngineer
Router 认为 ADR 是文档工程师的工作,而不是 ECC skill。实际上 ADR 是开发决策过程中的自然需求,属于 ECC 范畴。

### 5. `tdd-workflow` 全部被路由到 SP (Superpowers) 而非 ECC
100% 触发 SP,0% 触发 ECC。说明 router 把 TDD 识别为"实现/修bug"的 Superpowers 场景。这其实不完全错误——但会错过 ECC 的专项 TDD 工作流。

### 6. Frontend 类 skill 是路由准确率最高的类别
`e2e-testing`, `browser-qa`, `click-path-audit`, `frontend-patterns` 严格命中率 67%,合理命中率 100%。可能是因为 Playwright/UI 自动化的语义信号很强。

### 7. `claude-api` 被路由到 SP 和 CC,从不走 ECC
专门为 Claude API 开发设计的 skill 被自己的 router 忽略,路由到 SP 或 CC。讽刺性最高的发现。

### 8. `safety-guard` 和 `security-review` 命中率高 (ECC/security)
安全类 skill 触发准确,`security-review` 两个 prompt 都命中 ECC-security subskill,这符合 router 对安全场景有专项识别逻辑的设计。

### 9. Meta skill (`configure-ecc`, `skill-stocktake`, `context-budget`) 几乎从不被触发
这类"管理 ECC 本身"的 skill 在自然语言 prompt 中很难自动触发,几乎都退化到 CC。它们可能更适合做成 slash command 而不是依赖 router 触发。

### 10. Router 的 ecc_subskill 粒度不足以区分具体 skill
ECC 命中时 subskill 主要是 `research`/`debug`/`security`/`database` 这几个粗粒度分类,无法精确路由到 `agent-harness-construction` vs `agent-eval` 这种粒度。这是架构性问题,不是 prompt 问题。

---

## 应考虑停用/降优先级的 skill (触发场景几乎为零)

以下 skill 没有自然触发场景,且都退化到 CC:

| Skill | 问题 | 建议 |
|---|---|---|
| `token-budget-advisor` | 连 router 都无法识别,3次空返回 | 重写触发描述或合并到 `context-budget` |
| `nanoclaw-repl` | 极度小众,没有通用触发场景 | 降为实验性,不计入 89 |
| `project-guidelines-example` | 只是示例模板,不是真实触发场景 | 文档类,考虑删除 |
| `plankton-code-quality` | Hook 触发为主,prompt 无法激活 | 改为 hook-only,去掉 skill 包装 |
| `strategic-compact` | 内部状态管理,难以用 prompt 表达 | 改为 slash command |

---

## 给 Router 的改进建议

1. **增加 ECC skill-name 直接匹配**: 当用户提到具体 skill 名称时直接路由
2. **降低 GS-EngManager 的过度激活**: 当前太多"工程问题"都走 GS,应有更细粒度的 ECC 子路由
3. **增加 ecc_subskill 的粒度**: 现在只有 5 类,需要扩展到覆盖更多 skill 类别
4. **Meta skill 改为 slash command**: `configure-ecc`, `skill-stocktake` 等用 /configure-ecc 触发更自然

---

*覆盖矩阵详细数据: `~/router-eval-share/tests/cases/ecc-89-coverage.jsonl`*
*JSON 结果: `~/router-eval-share/tests/results/ecc-coverage.json`*

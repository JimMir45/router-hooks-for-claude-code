# CC 多 Skill 激活机制研究

## TL;DR

CC 的 Skill 工具在物理上**不阻止**同 turn 发起多次调用，但有三重力量将其收敛为串行单次执行：系统 prompt 硬性指令（"Do not invoke a skill that is already running"）、inline 模式下 SKILL.md 内容以 `newMessages` 注入下一轮的副作用机制（下一个 API call 才读到技能内容），以及 `toAutoClassifierInput` 注释中明确写出的产品意图（"Only one skill/command should run at a time"）。理论上多 skill 串行 per-turn 是可能的，但从 125 次真实调用中从未观察到多 skill 同 turn 触发。

---

## 1. 物理机制（基于源码）

### 1.1 Skill 加载管线

技能生命周期从磁盘读取到模型感知，分三层：

```
磁盘                        内存注册                          LLM 感知
~/.claude/skills/           getSkillDirCommands()            skill_listing attachment
pluginName/SKILL.md    →    Command[] (name + description    → system-reminder 注入
~/.claude/plugins/          + whenToUse + getPromptFn)
bundled registry
MCP skills
```

**加载触发时机**：进程启动时并行加载四个源：
1. managed skills（企业策略）
2. user skills（`~/.claude/skills/`）
3. project skills（项目目录遍历到 home）
4. plugin skills（`~/.claude/plugins/*/skills/`）

加载后去重（realpath 比对）、按路径深度排序（深者优先），结果 memoize 到 session 级别。

**关键字段**：每个 Command 携带 `name`、`description`、`whenToUse`（trigger hint）、`getPromptForCommand`（懒加载函数，调用时才读完整 SKILL.md 内容）。

### 1.2 Skill 如何感知到 LLM

**Turn 0**（初次注入）：`getAttachmentMessages()` 调用 `getSkillListingAttachments()`，将所有可用技能的 name + truncated description 格式化为 `skill_listing` attachment，包装进 `<system-reminder>` 注入用户消息：

```
The following skills are available for use with the Skill tool:

- graphify: any input to knowledge graph...
- prism: ...
- update-config: ...
```

**Token 预算**：技能列表占上下文窗口的 1%（约 8,000 字符），bundled skills 不被截断，其余按比例截描述。

**动态刷新**：新技能（动态发现或 plugin 加载）触发增量 `skill_listing` 注入，系统维护 `sentSkillNames` Set 防止重复。

### 1.3 SkillTool 的激活机制

LLM 调用 `Skill(skill: "foo")` 时，CC 执行：

**Inline 模式（默认）**：
1. `processPromptSlashCommand()` 调用 `command.getPromptForCommand()` 读取完整 SKILL.md
2. 内容注册到 `STATE.invokedSkills`（compact 保活用）
3. 内容作为 `newMessages` 返回，标记为 `sourceToolUseID`
4. 这些 messages 在当前 tool_result 解析完成后才注入下一 API call
5. `contextModifier` 应用 allowedTools / model / effort 覆盖

**Fork 模式**（SKILL.md frontmatter 含 `context: fork`）：
- 启动独立子 agent，单独 token 预算，结果以文本返回

**关键约束**（来自 SkillTool.ts 注释）：
> "Only one skill/command should run at a time, since the tool expands the command into a full prompt that Claude must process before continuing."

这不是代码层的硬拦截，而是**产品意图的声明**，靠 prompt 指令实现。

---

## 2. LLM 视角（skill 描述如何呈现）

### 2.1 呈现位置

技能列表通过 `<system-reminder>` 标签注入 user message，而非 system prompt。这意味着：
- 每次 turn 携带（不被 compact 截断，因为 compact 后会重新注入）
- 在 message history 中作为 user turn 的 meta 消息存在

### 2.2 LLM 选择技能的信号来源

LLM 看到三个层次的信号：
1. **SkillTool 系统 prompt**（tool description）：`Execute a skill within the main conversation`，含 BLOCKING REQUIREMENT 指令
2. **技能列表**（system-reminder）：每个技能的 `name: description [- whenToUse]`，截断到 250 字符
3. **CLAUDE.md 中的自定义指令**（如路由 hook 注入的 `[ACTION REQUIRED]` 行）

### 2.3 多 skill 描述写 "You MUST use this..." 时 LLM 怎么挑

当多个技能都有强制触发词时，LLM 的选择取决于：
- 哪个技能的 `whenToUse` 更精确匹配当前 prompt
- CLAUDE.md 的自定义指令是否覆盖（如路由 hook 的 `[ACTION REQUIRED]: Skill("X")` 直接指定）
- 技能列表中的排列顺序（bundled > user > project，但列表内顺序不定）

**没有去冲突机制**：CC 源码没有任何多触发词冲突解决逻辑，完全交给 LLM 推理。

### 2.4 "先用 X 再用 Y" 是否生效

**可行，但需要理解传播机制**：

- CLAUDE.md 中写 "先调用 X，再调用 Y" 会进入系统 prompt，LLM 会遵从
- 但 Skill X 的 SKILL.md 内容在 X 的 `tool_result` 处理后才注入（`newMessages` 机制），LLM 当时读不到 X 的内容就无法执行 X 的指令
- 正确的多 skill 协作方式：用一个 "orchestrator skill" 在其 SKILL.md 里显式指令调用另一个 skill，而不是让 LLM 在同一 turn 并发调用

---

## 3. 实际行为观察

### 3.1 数据采样

扫描范围：425 个含 Skill 关键词的 JSONL session 文件  
总 Skill tool 调用次数：125 次  
同一 assistant message 中多次 Skill 调用：**0 次**

### 3.2 典型调用案例

```json
{
  "type": "tool_use",
  "id": "toolu_01DuuyqWXGrYYYDTrXB9rUgE",
  "name": "Skill",
  "input": {"skill": "staff-engineer-cc"},
  "caller": {"type": "direct"}
}
```

单次调用，由路由 hook 的 `[ACTION REQUIRED]` 指令直接触发，不存在多 skill 并发。

### 3.3 Skill 列表注入示例（从真实 session 提取）

```
skill_listing attachment 内容（截断）：
- update-config: Use this skill to configure the Claude Code harness via settings.json...
- keybindings-help: Use when the user wants to customize keyboard shortcuts...
- simplify: Review changed code for reuse, quality, and efficiency...
- loop: Run a prompt or slash command on a recurring interval...
- claude-api: Build Claude API / Anthropic SDK apps. TRIGGER when: code imports `anthropic`...
```

列表以 `system-reminder` 标签包装，作为 user message meta 注入，每次 turn 可见。

---

## 4. 结论 + 开放问题

### 4.1 五个核心问题的答案

| 问题 | 答案 |
|------|------|
| 物理上是否允许同 turn 多 skill? | 代码层无硬拦截，但 prompt 指令禁止（"Do not invoke a skill that is already running"）|
| 执行顺序如何? | 串行：每次 Skill 调用 → tool_result → SKILL.md 内容注入下一 turn → LLM 读到内容才能继续 |
| 多个 "MUST use" 描述时 LLM 怎么挑? | 纯 LLM 推理，无代码级去冲突，`whenToUse` 精确度和自定义指令优先级决定 |
| Plugin 命名空间怎么处理? | 格式为 `pluginName:skillName`（含子目录则 `pluginName:namespace:skillName`），解析时支持精确/前缀/后缀匹配三级 fallback |
| CLAUDE.md 写 "先 X 再 Y" 是否生效? | LLM 级别生效（遵从指令），但 X 的 SKILL.md 内容要等 X 的 tool_result 后才注入，所以无法在 X 的 turn 0 执行 Y |

### 4.2 当前 Router 的处理方式

用户的路由 hook 通过 `[ACTION REQUIRED]: Skill("X")` 显式指定单一 skill，避开了多 skill 冲突问题。这是目前最可靠的激活机制——不依赖 LLM 推理，直接命令。

### 4.3 留给受众的开放问题

1. **Fork 模式的多 skill 并发**：fork 执行路径（context: fork）启动独立 subagent，理论上可以在 AgentTool 层面并发多个 forked skills。这个路径的多 skill 行为是什么？（源码显示 AgentTool 层没有 skill 数量限制）

2. **Conditional Skills 与 Multi-Skill 的交叉**：`paths` frontmatter 的条件技能在文件操作时动态激活，如果同一操作同时激活多个条件技能，listing 更新和 LLM 响应之间有多少延迟？

3. **Token Budget 对 whenToUse 的截断影响**：1% token 预算下，`whenToUse` 长的技能会被截断。截断是否会破坏触发条件的完整性，导致触发失误？

4. **Plugin skill 的 "冲突" 是命名冲突还是语义冲突**：源码做了命名空间隔离（`superpowers:debug` vs `ecc:debug`），但如果两个不同 plugin 的技能 `whenToUse` 语义重叠，LLM 怎么选？是否有 telemetry 数据支撑？

---

## 附录：关键源码路径

| 功能 | 文件 |
|------|------|
| 技能磁盘加载 | `src/skills/loadSkillsDir.ts` |
| 技能列表注入 | `src/utils/attachments.ts:getSkillListingAttachments()` |
| SkillTool 实现 | `src/tools/SkillTool/SkillTool.ts` |
| SkillTool Prompt | `src/tools/SkillTool/prompt.ts` |
| Plugin 命名空间 | `src/utils/plugins/loadPluginCommands.ts` |
| Skill 内容展开 | `src/utils/processUserInput/processSlashCommand.tsx:processPromptSlashCommand()` |
| Bundled skill 注册 | `src/skills/bundledSkills.ts` |
| 命令汇总 | `src/commands.ts:getSkillToolCommands()` |

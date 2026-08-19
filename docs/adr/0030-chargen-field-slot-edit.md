# ADR-0030：引导车卡字段槽位化（可改字段 / 可回退 / 级联失效）

- **状态**：已采纳
- **日期**：2026-08-19
- **版本**：v0.55.0

## 背景

引导车卡（`guide_chargen` 工具 + `/车卡` 命令）原为**线性状态机**：

- `ChargenDraft.state` 是单一字符串，只能单向递增（除 `cancel` 整删）；
- 步骤顺序写死在 `_step_order()` 与 `_advance_locked()` 两处独立硬编码；
- 无任何「回退上一步 / 跳步 / 中途改字段」机制。

玩家车卡到后面想改前面的种族/职业/背景，只能 `cancel` 全部重来。三个痛点
（流程死板、不可改序、不可回退）同根：`state` 同时承担「进度指针」与「该填
哪个字段」两个职责，字段间依赖关系从未被建模。

## 决策

引入**字段槽位模型（轻量实现）**，把「步骤序列」解耦为「字段集合」：

1. **字段独立 + 依赖图**：逻辑字段（race/class/background/ability_assign/
   ability_bonus/alignment 等）各自持有值；依赖关系集中在模块级
   `_FIELD_DEPENDENTS`（单一真源）——改 race/background → ability_bonus 失效，
   重代骰 ability_method → ability_assign 失效。
2. **编辑环**：新增 `ChargenManager.edit(field, value)`（校验→写→级联失效）
   与 `undo(field=None)`（清空指定字段或最近已填字段并级联）。改字段只清空
   受影响的字段并标记 `invalidated`，不整体回退。
3. **推进数据驱动化**：把 `_advance_locked` 的 300 行 if-elif 链拆为
   `_apply_state`（校验+写，advance/edit 复用）+ `_first_needed_field`（动态
   定位下一待填字段，替代写死的 next_state）。
4. **入口双轨**：`guide_chargen` 工具新增 `action=edit/undo` + `field` 参数
   （LLM 识别「玩家想改…」意图后调用）；`/车卡 改 <字段> <值>`、`/车卡 回退`
   命令确定性兜底。

## 权衡

- **未采用完全重构为 FieldSlot dataclass**：字段值仍存于现有
  `data/ability_pool/ability_assign/ability_bonus/backstory_parts`，仅新增
  `invalidated` 列表标记失效。相比引入全新 slots 真源，改动面更小、对现有
  测试与 `_finalize`/`_bonus_offer` 的兼容成本更低，语义等价（字段值 + 失效
  标记 = 字段状态）。
- **`_advance_locked` 仍按 state 分派**：`_apply_state` 内保留各步骤特殊逻辑
  （职业解析子职、代骰、动态跳过加值步），未强行抽象成纯配置表——每步特殊
  性高，纯数据驱动会引入更复杂的钩子，收益有限。
- **回退粒度**：`undo` 回退到「最近一个已填字段」；backstory 三段共享一个
  逻辑字段，回退生平会清空三段（粗粒度，可接受）。

## 后果

- 玩家可随时改前面已填字段，改上游自动级联失效下游（清值 + 提示重新确认）。
- `_FIELD_DEPENDENTS` / `_FIELD_CN` / `_FIELD_ALIASES` / `_STATE_TO_FIELD`
  成为字段元信息的单一真源，新增字段需同步这几处。
- KV 草稿新增 `invalidated` 字段，`from_dict` 缺省容错（旧草稿可继续，仅无
  失效标记）。

# ADR-0027：法术标签与概要重做（13 类语义标签 + facet 补强 + 白捡元数据）

- **状态**：已采纳
- **日期**：2026-08-18
- **版本**：v0.52.0

## 背景

法术 `spell_keyword` 语义标签（v0.27.0 引入）长期存在三类问题，用户发起重做：

1. **与结构化元数据重叠**：伤害类型词（火焰/寒冷/闪电…13 个）与 `dmg_dealt`
   facet 完全重复；形状词（球状/立方/圆形/锥形）与 `spell_shape` 重叠。
   同一信息既存 facet 又存标签，反查口径打架。
2. **过泛/稀疏**：「伤害」标签命中 507 个法术（近半），区分度趋零；40+ 个
   标签命中 ≤3 个法术（其中 11 个仅 1 个），无反查价值。
3. **缺口**：`condition_inflict` 对法术**恒空**——`_extract_conditions` 只认
   5etools `{@condition}` 内联标签，而法术主源（v0.43.0 起 5e_chm 纯中文 md）
   无此标签。伤害类法术 24% 概要无骰数（含截断残骸）。

另发现三个 chm 记录原生带、但从未入库的**白捡元数据**字段：`time`（施法时间，
1220 全覆盖）、`detail_duration`（持续时间原文，1220 全覆盖）、`classes`
（中文可施职业，1204/1220）——而 `spell_classes` 职业表此前只靠英文查找表
（约 900 条覆盖），chm 独有书全 miss。

## 决策（grill 十项共识 + 执行细节确认）

1. **词表收敛为 13 个效果域大类**：伤害/治疗/增益/控场/位移/防护/召唤/侦查/
   潜行/社交/探索/幻术/即死。「减益」并入「控场」、「造物」并入「召唤」、
   「战斗辅助」+「施法辅助」并入「增益」（v0.27.0 词表实为 17 键，非 18）。
   库里 `spell_keyword` **只存大类名**，细词/旧值全部经别名表归一。
2. **判定口径（用户澄清）**：按**效果实质**分类，不按目标是谁——
   「末日」给敌人上减益但本质提升我方输出 → 归**增益**；「控场」= 限制行动
   （异常状态/铺场），dot 类异常状态归**伤害**。
3. **标签来源**：全量 LLM 重跑 1220 条（复用 class_extract 先例的
   「契约 schema + LLM 生成 + merge」模式，无 API key 依赖），契约
   `scripts/llm_spell_schema.md`，20 条 pilot 用户审过 → 20 批生成 →
   合并 `kb_patches/spell_enrich.json`。
4. **概要口径**：≤30 字、含「效果+目标+代价」三要素；凡有确定性数值
   （伤害/治疗/临时生命）必写骰数，无数值可写（隐形/传送/社交类）写效果；
   条件性伤害（坠落按高度/复合天气）写类型不编数值。
5. **facet 补强**：① `condition_inflict` 对法术启用——中文状态词启发式
   （`kb_build_lib._extract_conditions`，防御/解除语义分句跳过）② `_parse_md_range`
   规范化中文范围文本（`自身（半径N尺）`→self、`N里/N英里`→feet、`视野`→sight、
   `无限`→unlimited）③ 伤害/状态词表沿用 DAMAGE_TYPE_CN/CONDITION_CN 并补防御词。
6. **白捡元数据全入库**：spells 表新增 `cast_time`/`duration_text`/`classes`
   三列（SCHEMA_VERSION 11→12）；`spell_classes` 改由 chm 中文职业优先
   （1204/1220）、英文查找表兜底（16 条缺失时）。
7. **旧值兼容**：`_SPELL_KEYWORD_ALIASES` 全量扩展（99 个旧取值→13 类），
   构建期 `spell_kw_canonical` 与查询期 `resolve_spell_keyword` **同口径**
   （词表内→类名，其次别名表，词表外原样）；形状词/冰霜/中毒走 facet 不入标签。
8. **验收标准**：构建后自动化断言（13 类全覆盖每法术 ≥1、零词表外、旧值全在
   别名表、伤害/状态反查走 facet 与标签不重叠）+ 20 条用户抽审 + 测试全绿。
9. **数据修复**：上一轮压字数用的「从最后一个逗号截断」兜底把 85 条概要砍成
   残骸（火球术→「爆裂火球灼烧指定点」），全部基于原始正文重写；53 条
   「豁免败」缩写恢复为「豁免失败」；37 条原文有骰但省略的补齐数值
   （伤害类含骰率 86%→94%）。

## 实施

- `kb_enums.py`：`SPELL_KEYWORD_TAGS` 重写为 13 类（召唤类保留亡灵/变身/野兽/
  元素主题词）；新增 `_SPELL_KW_CANONICAL` + `spell_kw_canonical`（构建期归一）；
  `_SPELL_KEYWORD_ALIASES` 全量重写（旧 99 值 → 13 类，含英文/口语别名）。
- `kb_build_lib.py`：`_extract_conditions` 增加中文状态词启发式
  （`_CONDITION_INFLICT_PATTERNS` 16 状态 + `_CONDITION_DEFENSE_HINTS` 防御词）。
- `scripts/build_kb.py`：`_parse_md_range` 中文范围规范化；spells 表加三列 +
  SCHEMA_VERSION 12；`_chm_spells_to_entries` 携带 cast_time/duration_text/
  classes_list；INSERT 语句加列；`spell_classes` chm 中文职业优先
  （`en_to_cn_class` 双向映射）；`_spell_tags` 标签归一走 `spell_kw_canonical`。
- `kb_patches/spell_enrich.json`：全量重跑（1220 条，13 类标签 + ≤30 字概要）。
- `main.py`：`query_dnd_knowledge` docstring `spell_keywords` 说明更新为 13 类。
- 测试：schema 断言 11→12；spells 新列断言；标签归一断言（火焰→伤害、
  减益→控场）；`test_filter_spell_keyword` 更新（枯萎术带减益→归一后命中控场）。

## 结果

- `spell_keyword`：恰好 13 个取值，1220/1220 覆盖，零词表外。
- `condition_inflict`：空 → 444 条/303 法术/16 状态（魅惑 62/束缚 53/目盲 49…）。
- 白捡元数据：cast_time/duration_text/classes 1220 全覆盖；施法时间分布
  动作 922/其他 154/附赠 117/反应 27。
- `spell_classes`：3388 行、1204/1220（chm 中文职业优先，16 条兜底）。
- `range_type` 规范化：feet 707/self 302/touch 193/…，仅 1 条升环特殊原文保留。
- 测试 1273 passed / 6 skipped。

## 后续

- 施法时间字段可为「反应施法/附赠施法」等高区分度反查提供数据支撑
  （本次未加对应标签，13 类已足够）。
- `dmg_dealt` 覆盖 466/1220（38%）——伤害类法术基本覆盖，治疗/增益类无伤害
  文本属合理缺失；如需提升可扩展正文伤害句式词表。
- chm classes 缺失的 16 条法术（第三方书）可后续人工补 classes 字段。

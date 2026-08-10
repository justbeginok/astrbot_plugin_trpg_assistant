# 特性反查用通用 entry_tags 侧表，构建期从源数据提取特性标签

日期：2026-08-07（v0.13.0）
状态：已接受

## 背景

玩家需要按「特性」反查条目：「所有造成火焰伤害的怪物」「造成黯蚀伤害的武器」。
但 5etools 中文站的数据里伤害类型没有任何结构化字段——`{@damage}` 标签装的
是骰子（`2d8+8`），伤害类型只以中文文本形式出现（「…点穿刺伤害加上7点火焰
伤害」）；物品的 `dmgType`（单字母码）与 `weapon property` 码存在但从未入库；
`items-base.json`（基础武器/防具）完全未入库。

## 决策

1. **通用侧表 entry_tags(entry_id, facet, value)，不建 N 张专表**。facet 编码
   维度+关系：`dmg_dealt / dmg_resist / dmg_immune / dmg_vuln /
   condition_immune / condition_inflict / environment / weapon_property /
   spell_component / spell_shape / spell_target`；value 为归一化后的 canonical
   中文。`filter()` 对每个 tag 拼一条 `EXISTS` 子查询，多条件天然 AND；
   未来新增维度零 DDL。
   - 方案对比：每维度专列（如 monsters 加 fire_damage 列）会让查询 SQL 组合
     爆炸且加维度要重建表；按来源分别存 `weapon_dmg`/`spell_dmg`/`dmg_dealt`
     三个伤害 facet 对查询无增益（kind 已限定条目类型），故统一为一个
     `dmg_dealt`（与计划初稿的差异，见 git 历史）。
2. **提取全部发生在构建期（build_kb.py），查询期零计算**：
   - 怪物：动作区（trait/action/bonus/reaction/legendary/mythic）中文正则提取
     伤害类型；`{@condition X}` 标签在 `clean_5etools_tags` 清洗前提取状态；
     `immune/resist/vulnerable/conditionImmune/environment` 是结构化中文列表，
     直接映射（含 dict 变体）。
   - 物品：`dmgType` 单字母码表 + `weapon property` 码表解析；entries 文本
     正则提取附加伤害（`额外造成2d6火焰伤害`）；`items-base.json` 入库；
     `_copy` 浅合并基字段；`reprintedAs` 旧版条目跳过、旧名重定向为再版条目的
     别名。
   - 法术：`components`/`range` 结构化入 spells 新列；范围形状（自原点法术由
     `range.type` 决定，point 用文本启发式）；目标类型（单体/多体/自我）纯文本
     启发式，**歧义不打标、宁缺毋滥**。
3. **译名归一化放构建期**：伤害词表 13 个 canonical（暗蚀而非黯蚀——源数据
   566:0）+ 变体别名（黯蚀/冷冻/精神/酸性等）；查询期只做别名→canonical 映射。
4. **schema 版本化**：meta 写 `schema_version=2`；`resolve_db_path` 检查版本，
   旧版 `kb_update.db`（v1）自动回退到新版内置库，防止旧更新库屏蔽新结构。

## Considered Options

- **运行时 body LIKE 文本搜索**：零 schema 变更、发版快，但无法区分「造成
  火焰伤害」与「免疫火焰伤害」（怪物 body 的【免疫】段就是纯文本），且物品
  的伤害字段根本没进 body（武器查询完全不可行）；译名变体（寒冷/冷冻）难以
  在 SQL 里统一。否决。
- **FTS5 全文索引**：中文分词依赖编译选项（trigram），部署机 SQLite 版本不
  确定；且无法表达「造成 vs 免疫」这类关系语义。否决（延续 ADR-0002 结论）。
- **每维度专列**：见决策 1。

## Consequences

- `dnd_kb.db` 体积约 10MB → 14MB（标签 + 基础物品 + 索引），仍随包分发；
  构建脚本运行时间不变（数秒）。
- 伤害类型提取的正确性依赖中文词表，构建结束时打印「未收录的 X伤害 上下文词」
  告警（前 20 个）供维护者补别名；词表外的新伤害描述不会被标记（宁缺毋滥）。
- 启发式（法术形状/目标、物品附加伤害文本）是「静默近似」：结果可能漏（不
  可能多，歧义不打标），README 已知限制已声明。
- 施放型物品（法杖/卷轴）造成的伤害只有文本含「X伤害」字样才被标记——这是
  文本提取的固有边界，接受。
- 物品条目数 2428 → 1853：`reprintedAs` 去掉了旧来源的重复版本（如 DMG 版
  XDMG 重印后只保留新版），基础物品 230 条补入后净减；同名查询不再返回
  「多个版本」式的重复行，改名行为对玩家更清晰。
- LLM 工具 `filter` 的 docstring 新增 10 个参数（AstrBot 从 docstring 解析
  schema，见测试 `test_llm_tool_schema.py`），每次改工具参数必须同步该测试。

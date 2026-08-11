# 5e_chm Markdown 作为法术主数据源

v0.43.0 确立：法术条目的**中文详述与元数据改以本地 5e_chm Markdown 为唯一主源**
（人工校对「不全书」），5etools-cn JSON 不再贡献法术条目；职业法术表
`spell_classes` 仍由英文 5e.tools 查找表提供。SCHEMA_VERSION 10 → 11。

## 背景

知识库法术原有 554 条（2024 主线，PHB-2014 全被 reprintedAs 跳转），正文来自
5etools-cn JSON 的 entries（上游机翻通道，法术仅 4 条机翻但扩展书覆盖不全）。
ADR-0003 曾因「HTM 非结构化、解析脆弱」否决 DND5e_chm 作为数据源。

v0.42.1 起 5e_chm 已完成 htm→md 批量转换（`5e_chm/scripts/htm_to_md.py`，
7040 个 .md，GB18030 解码、>8 列表格保留 HTML），结构化问题基本解决：
- 速查表 `速查/法术速查/5E万法大全.md`：936 条官方（23 td/行零缺列），
  元数据（环阶/学派/职业简写/时间/成分/仪式/专注/来源）全部结构化；
  合作方万法大全.md 另 284 条第三方。
- 详述页按环阶打包（`玩家手册2024/法术详述/{0..9环}.md` 等 16 个来源文件），
  每条 `#### 中文名｜English` + 元数据块 + 人工校对中文正文 + 升环施法段，
  与速查表逐来源精确匹配无缺口。

## Considered Options

- **继续 5etools-cn JSON**（维持现状）：结构化字段全、与上游同步容易，但
  2014 版法术全被跳转丢失、扩展书中文覆盖靠上游机翻、第三方 0 覆盖。
- **md 全量替换**（唯一源）：中文质量最高、双版本全收录、第三方可入，
  但失去 5etools 的结构化字段（职业法术表依赖英文查找表仍在）。
- **md 主源 + JSON 补充**（采纳）：法术详述/元数据以 md 为准（人工校对），
  结构化字段（level/school/range 等）由 md 文本解析或 5etools join 补齐，
  `spell_classes` 职业法术表仍走 `--en-spell-lookup`（英文源，天然不受中文
  数据源切换影响）。第三方 284 条一并收录（来源码新设计，如 黯潮→ACT）。

## Consequences

- **md → 5etools 风格 entry 转换**（`build_kb._chm_spells_to_entries`）：
  正文用 `chm_parser._build_body` 预构建（与 `_spell_body` 同构的
  `【法术信息】环级|学派|施法时间|距离|成分|持续时间|仪式` 格式，`_prebuilt_body`
  绕过 `_kind_body`）；`_edition_override`/`_is_machine_override` 保证
  edition 准确（第三方=「第三方」）与 is_machine=0（人工校对源）。
- **自动标签**（dmg_dealt/condition_inflict/spell_shape/spell_target）改从
  md 详述纯文本提取（`_walk_texts` 原生支持字符串），不再依赖 5etools entries。
- **同英文名中文名归一**：2014/2024 两版译名不一致（造水/造水术）时以
  2024 为准、旧名入别名，保证按任一中文名搜索两版都命中。
- **富化补覆盖**：`scripts/gen_enrich.py` 规则生成器为缺口法术（~701 条）
  生成 summary+keywords（含学派兜底词），与既有 554 条 AI 生成合并，
  覆盖率 100%；规则生成质量靠抽检兜底，可后续 AI 精修。
- 新增脚本：`scripts/chm_parser.py`（解析）、`scripts/audit_chm.py`（对账）、
  `scripts/gen_enrich.py`（富化）；中间产物 `scripts/_md_cache/`（不入库）。
- 构建入口：`build_kb.py <data> --spell-md <spells_chm.json> --en-spell-lookup <lookup>`。
- 已知瑕疵：源数据个别错误（如 黯潮「命运之刃」施法距离行误写成分内容）如实
  保留待修；TCE 无 8 环法术（目录缺 8环.md 是正常现象）。

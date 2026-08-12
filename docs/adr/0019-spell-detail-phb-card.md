# 法术详情显示改为 PHB 卡片式（v0.44.0）

## Context

v0.43.0 起法术知识库以 5e_chm 人工校对中文为主数据源（ADR-0018），
但法术详情的显示仍是构建期 `_spell_body`/`_build_body` 产出的
一行式头部：

```
【火球术 Fireball】[PHB·2014]
概要：……
【法术信息】3环｜学派塑能｜施法时间1 动作｜距离150 尺｜成分言语姿势材料｜持续时间：立即
```

信息密度低、与玩家熟悉的玩家手册（PHB）法术卡排版差异大；且
`spell_summary`（AI 概要）与 `spell_keyword`（语义标签）只用于
筛选，不在详情展示。

目标：纯文本 QQ 群环境下，法术详情呈现 PHB 卡片式（玩家惯例格式），
并把已有的概要和标签一并显示。

## 目标格式（玩家确认）

```
命令术｜Command
概要：你吐出一个单字命令迫使目标服从
标签：控制、惑控

一环 惑控（吟游诗人、牧师、圣武士）
施法时间：1 动作
施法距离：60 尺
法术成分：V
持续时间：1 轮

<描述正文>

<升环施法段>

版本：PHB·2014
```

## Considered Options

- **构建期渲染全部**（标题/概要/标签/版本都进 `entries.body`）：概要/标签
  在侧表（spells.summary / entry_tags），查询期才可得；且同一条目在
  筛选列表等处复用时，标题重复。
- **运行时渲染全部**（`format_entry` 从原始数据拼）：施法时间/持续时间等
  结构化字段不在运行时侧表（spells 表仅有 level/school/components/
  range/concentration/ritual），无法在查询期重组完整卡片。
- **分层（采纳）**：卡片体（环位行 + 属性行 + 正文 + 升环段）在**构建期**
  预渲染进 `entries.body`（chm 源走 `chm_parser._build_body`，5etools
  回退与私设运行期走 `kb_build_lib._spell_body`，两者同构）；标题/概要/
  标签/版本行在**运行时**由 `kb.format_entry` 法术分支拼装。

## Decision

1. **卡片体 = body（构建期预渲染）**：环位行 + 4 属性行一段、空行、正文、
   空行、升环段。环位行从结构化字段（level/school/classes/ritual）重建，
   **不复用 chm `detail_meta`**（第三方源有脏数据：魔袋术括号内为垃圾文本）。
   戏法显示「塑能戏法（…）」；仪式置入环位行括号（「一环 预言（仪式；…）」）。
   属性行直接用 chm 人工校对字段（detail_time/detail_range/
   detail_components/detail_duration），缺省时由 components 字典兜底拼
   V/S/M 字母。
2. **标题/概要/标签/版本行 = format_entry 法术分支（运行时）**：
   - 标题纯净：`{name}｜{eng_name}`（无 eng 退化为中文名），无【】无版本；
   - 概要（spells.summary）与标签（entry_tags `spell_keyword` facet，顿号
     分隔）放标题下、环位行前，缺省即跳过；
   - 版本行放卡片底部：`版本：{source}·{edition}`，机翻 `⚠️机翻` /
     房规 `🏠房规` 标记也在此行（原在标题行）。
3. **其他种类显示不变**：format_entry 仅 spell 分支走新格式；
   `/筛法术` 列表（format_filter_result）不受影响。
4. **schema 不变**：SCHEMA_VERSION=11 / KB_SCHEMA_VERSION=7 均不动
   （body 属数据层变更）。

## Consequences

- `entries.body` 对法术而言变为卡片体，`_summary_of` 需跳过元信息行
  （否则搜索候选摘要显示成「三环 塑能」）；线上 1220 条 summary 全覆盖，
  属防御性修复。
- 单条输出因标题/概要/标签/版本行略超 MAX_ENTRY_LEN（与专长概要现状
  一致，可接受）。
- 61 条官方法术的 chm classes 与 en_lookup spell_classes 存在差异
  （如命令术 2014 缺吟游诗人，chm 速查表转写缺口）——显示遵循 chm
  人工校对源，数据缺口留作后续补丁通道跟进。
- LLM 工具 query_dnd_knowledge 经 format_detail 输出自动变为卡片式；
  工具描述不写死格式。
- 已部署的旧 kb_update.db 法术 body 仍为旧格式（数据层差异，非崩溃级，
  不强制刷新）。

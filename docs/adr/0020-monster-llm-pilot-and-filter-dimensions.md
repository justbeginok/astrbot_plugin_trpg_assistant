# 怪物 LLM 试点与筛怪维度扩展（v0.45.0）

## Context

### 需求

1. 反向查询（`/筛怪`）维度不足：目前只能「火焰」裸词 → 造成火焰伤害
   （`dmg_dealt`）。用户需要 6 类细分：
   - 伤害四类分开：`/筛怪 火焰伤害` `火焰抗性` `火焰免疫` `火焰易伤`
   - 状态免疫：`/筛怪 震慑免疫`
   - 速度类型：`/筛怪 掘穴速度` `飞行速度`
   - 怪物特性名：`/筛怪 再生`
   - 视觉能力（感官）：`/筛怪 真实视觉`
   - 阵营：`/筛怪 守序善良`
2. 怪物 LLM 试点：以本地 5e_chm/md（htm 转 md 的人工校对中文全书）为源，
   用 LLM 子代理读取怪物制作怪物数据库，先小规模验证工程量。

### 侦察事实（2026-08-12）

- **5etools-cn 镜像已有《怪物图鉴2025》结构化数据**（`bestiary-xmm.json`，
  503 条）：`speed`/`alignment`/`senses`/`conditionImmune`/`immune`/
  `resist`/`vulnerable`/`trait`/`action`/`environment` 字段齐全，224 条
  translator=「不全书」（人工翻译）。现有 KB 已收录 2024 怪物 630 条。
  → 筛怪 6 维所需数据 5etools 全部已有，**不需要 LLM 即可实现**。
- 现有怪物标签 facets：`dmg_dealt`/`condition_inflict`/`dmg_immune`/
  `dmg_resist`/`dmg_vuln`/`condition_immune`/`environment`（均构建期从
  5etools JSON 提取）。**缺**：速度、感官、阵营、特性名 4 维。
- `monsters` 侧表仅 3 列（cr/mtype/size），无速度数值列。
- `/筛怪` 裸词解析（`_parse_filter_tokens`）monster 分支只有
  `dmg_dealt→condition_inflict→environment→mtype` 4 个枚举解析器，
  无后缀词规则、无自由文本消歧兜底（其他 kind 均有 resolve_*_free_term）。
- 5e_chm/md 怪物图鉴2025 = 548 个条目文件，粒度不齐（单怪页/群组页
  「XX总」/章节页/变体页）。
- 5e_chm/md 第三方/火炬光下的克苏鲁/第七章 = 28 个怪物文件（含 1 个
  章节页「第七章：神话生物」+ 1 个 lore 页「深潜者总」），每怪 50-90 行，
  标准 2024 统计块格式，共 1710 行。免疫行是混合文本（如修格斯
  「免疫 强酸，暗蚀，毒素；目盲，魅惑，耳聋，恐慌，中毒，倒地」——
  分号前伤害免疫、分号后状态免疫）。

### 决策

1. **试点源改为第三方书**（grill 确认）：《火炬光下的克苏鲁》第七章
   28 怪。绕开与 5etools 数据重叠；验证「md→LLM→JSON→build_kb」正式
   管道，为将来覆盖 5etools 没有的源（第三方书/模组/自制）铺路。
2. **接入架构 = build_kb 正式通道**：LLM 产物转 5etools 兼容 JSON
   （`bestiary-thc.json`），被 `KIND_SOURCES` 的 `bestiary/bestiary-*.json`
   glob 原生捕获；`source=火炬光下的克苏鲁`、`edition` 字段 override=2024。
   `/查怪`/`/筛怪`/LLM 知识查询全链路自动可用，无新机制。
3. **提取范围 = 全字段**：对齐 5etools monster 核心 schema（卡面数值 +
   免疫 4 类 + 速度 dict + 感官 + 语言 + 特质/动作/施法正文），
   `_monster_body`/`_monster_tags` 零改造。
4. **筛怪 6 维与本版同做**：构建期扩展 4 类新标签（对全部怪物生效），
   解析层加后缀词规则。共用同一批构建改动，一次发布。
5. **裸词向后兼容**：「火焰」保持 dmg_dealt；后缀词精确化；结果底部
   提示可追加细分词。
6. **三层验收**：规则校验器（自动）→ 抽样对读 → 28 条关键字段对账表
   用户审后入库。

## Considered Options

- **试点源 = 2025 怪物图鉴**（原提议）：与 5etools XMM 数据 100% 重叠，
  LLM 提取成为冗余成本，无法验证真实管道价值 → 弃。
- **接入 = 运行时私设 overlay**（trpg_homebrew）：筛怪走构建期
  `entry_tags` 表，overlay 无法参与筛怪，需另造机制 → 弃。
- **提取 = 最小字段**（仅筛怪 6 维+名称/CR/类型）：`/查怪` 展示残缺，
  body 生成需双轨补丁 → 弃。
- **裸词弹歧义提示**：简单查询变繁琐 → 弃。
- **验收 = 仅自动校验**：试点首次管道需人工把关 → 弃。

## 实施要点

1. **构建期标签扩展**（build_kb.py `_monster_tags`，**无 schema 变更**——
   entry_tags 为通用表，新增 facet 无需 bump SCHEMA_VERSION）：
   - `entry_tags` 新增 4 facet（怪物）：
     - `speed_type`：速度 dict 有该键即标（步行/攀爬/游泳/飞行/掘穴）；
     - `sense_type`：senses 文本按前缀拆类型——真实视觉/黑暗视觉/
       盲视/震颤感知（senses 如「真实视觉 120 尺」→ 类型词；
       「颤动感知」2014 译名归一为「震颤感知」）；
     - `alignment`：alignment 字段（2014 字符串 / 2024 数组如 ['N','E']）
       经 format_alignment 归一为中文阵营 tag（守序善良等 9 类 +
       「任意阵营/无阵营/不固定阵营」）；
     - `monster_trait`：trait 标题中文名（「再生Regeneration」→「再生」，
       正则取开头连续中文，去「.。」瑕疵字符）。
   - 第三方 JSON（LLM 产物）字段与 5etools 同构，自动获得全部标签。
   - ⚠️ 速度数值筛选（如「飞行≥60尺」）不在本版需求内：monsters 侧表
     速度列**留待**该需求出现时再加（届时 bump SCHEMA_VERSION）。
2. **筛怪解析层扩展**（main.py `_parse_filter_tokens` + kb_enums.py）：
   - monster 后缀词规则（token 词尾匹配）：
     - `X伤害` → 去后缀 `resolve_damage_type` → `dmg_dealt`；
     - `X抗性` → `dmg_resist`；
     - `X易伤` → `dmg_vuln`；
     - `X免疫` → **伤害词表优先**（dmg_immune），未命中落状态词表
       （condition_immune）——「毒素免疫」→伤害免疫、「中毒免疫」→
       状态免疫、「震慑免疫」→状态免疫，词表天然不冲突；
     - `X速度` → 去后缀 `resolve_speed_type`（已有）→ `speed_type`。
   - monster enum_parsers 增：`dmg_resist`/`dmg_immune`/`dmg_vuln`/
     `condition_immune`（resolver 复用）+ `speed_type`/`sense_type`/
     `alignment`/`monster_trait`（新 resolver）。
   - 新增 `resolve_monster_free_term`：裸词兜底查 entry_tags 值集
     （sense_type → alignment → monster_trait → speed_type 顺序），
     仿 race_free_lookup 模式；未命中进 unknown。
   - `format_filter_result` 怪物分支结果底部加提示行：
     「可追加：{伤害}免疫 / {伤害}抗性 / {伤害}易伤 精确筛选」。
3. **LLM 提取管道**（scripts/）：
   - `llm_monster_schema.md`：提取 schema + 输出 JSON 格式约定；
   - 提取 prompt 规则：只提取含 CR 统计块的页（lore 页/章节页/群组页
     跳过）；清理 md 瑕疵（「水陆两栖Amphibious.。」重复句号、*强调*
     标记、表格杂质）；免疫行按「；」拆伤害/状态两组；属性表从 HTML
     table 提取六维数值与调整值；
   - `validate_monster_json.py`：规则校验器（必填字段/数值格式/免疫
     词表/速度格式/感官词表/阵营词表/CR 格式）。
4. **数据流**：md → 子代理 LLM 提取（Agent 工具）→ bestiary-thc.json
   → 规则校验 → 对账表 → 并入 5etools-cn data/bestiary/ → build_kb 重建
   → 全链路。

## Consequences

- 全部怪物（3741 条 5etools + 第三方）获得 6 维筛怪能力；第三方 28 怪
  入主库，`/查怪` 可查、`/筛怪` 可筛、LLM 知识查询可命中。
- **schema 不变**（SCHEMA_VERSION=11 / KB_SCHEMA_VERSION=7 均不动）：
  新功能只需重建 KB（重新构建即获得新 facet 标签），已部署旧库在
  `/kb update` 重建前筛怪新维度不生效。
- 特性名维度是开放集合（trait 标题），值随源数据增长；筛怪裸词按
  精确值匹配，不打语义标签。
- 阵营数组（2024 多元宇宙式 ['N','E']）展开为多 tag，`/筛怪 守序善良`
  只命中含 LG 的怪物；「任意阵营」类由构建期归一。
- 试点管道验证后，后续第三方书/模组怪物走同一通道；md 源为人工校对
  翻译，质量高于机翻，但 LLM 提取仍有误差 → 三层验收为管道标配。

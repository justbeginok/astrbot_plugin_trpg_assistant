# 架构与数据结构总览（ARCHITECTURE）

> 本文档是全工程的**结构索引**：模块怎么分工、数据存在哪、每种数据结构长什么样。
> 它是理解工程的第一入口；更细的语义约定看 `CONTEXT.md`（领域语言），
> 每个不可逆决策的来龙去脉看 `docs/adr/`，用户可见行为看 `README.md`。
>
> **建议阅读路径**：① 本文件（先看，建立全局）→ ② 术语不清楚 → `CONTEXT.md`
> → ③ 设计取舍 → `docs/adr/` → ④ 具体实现 → 模块代码 + `tests/`。
>
> 版本：随 v0.40.0（2026-08-11）核对；数据结构以本文件为准，新增/变更字段时同步更新本文件。

---

## 1. 系统总览

AstrBot 插件（`astrbot_plugin_trpg_assistant`，显示名「跑团助手」），基于
Dracowyn/astrbot_plugin_dnd_dice v0.6.2 fork 扩展。D&D 5e 跑团辅助，面向
QQ 群聊/私聊会话（`unified_msg_origin` 隔离）。

```
消息事件 ──► main.py（TrpgAssistantPlugin，命令注册 + 路由 + 鉴权）
                │  @filter.command（用户指令）         @filter.llm_tool（LLM 函数工具）
                ▼
        ┌────────────────────── 功能模块（各管一域） ──────────────────────┐
        │ dice_parser/roller  骰池（上游核心）                             │
        │ history             投掷历史      │  initiative  先攻追踪          │
        │ inventory           背包(个人+队伍)│  shop+money  商店/货币结算      │
        │ character           角色卡        │  chargen     车卡状态机+群规则  │
        │ chargen_engine      规则引擎(纯函数)│ card_import  文本卡导入(纯函数) │
        │ kb.py/kb_tags/kb_enums  知识库（只读 SQLite）                    │
        │ kb_build_lib.py         渲染/侧表提取共享纯函数（构建期+运行期共用）│
        │ homebrew.py             运行期私设 overlay（data_dir/trpg_homebrew）│
        └───────────────────────────────────────────────────────────────┘
                │
        AstrBot KV（全部会话/玩家数据）    kb_data/dnd_kb.db（只读知识库）
```

**两条持久化通道，泾渭分明**：
- **AstrBot KV**：一切可变数据（先攻/历史/骰面设置/前缀/背包/商店/角色卡/车卡草稿/群规则）。
  key = 功能前缀 + `origin`（+ `sender`）。**AstrBot 无 KV 枚举能力**，故所有列表
  结构（卡名索引等）必须显式维护。
- **SQLite 只读库**：知识库数据（随包打包，构建脚本 `scripts/build_kb.py` 全量重建），
  唯一不走 KV 的持久数据。

---

## 2. 模块地图

| 模块 | 职责 | 数据模型 | 命令/工具入口 |
|---|---|---|---|
| `dice_parser.py` / `dice_roller.py` | Roll20 规范骰池解析与掷骰（上游核心，只读扩展） | `DieRoll` / `DiceGroupResult` / `RollResult` | `/r` `roll_dice` |
| `formatter.py` | 骰池结果格式化 | — | — |
| `history.py` | 会话投掷历史（上限 `max_history_count`，失败投掷不记录） | `HistoryEntry` | `/rh` `/rhistory` |
| `initiative.py` | 会话先攻列表与回合推进 | `InitiativeEntry` / `InitiativeState` | `/ri` `/init` `manage_initiative` |
| `inventory.py` | 个人背包 + 队伍背包，物品流转（put/take/give）原子化 | `ItemEntry` / `Inventory` | `/bag` `/inventory` `/背包` `manage_inventory` |
| `shop.py` + `money.py` | 会话商店（单店）买卖结算；折铜/找零纯函数 | `ShopEntry` / `Shop`；`COIN_VALUE` 等 | `/商店` `/shop` `/店铺` `manage_shop` |
| `character.py` | 多卡角色卡（索引+活跃指针）、熟练/专精/专长、攻击联动 | `CharacterSheet` 及其子结构 | `/卡` `/char` `/角色卡` `manage_character` |
| `chargen.py` | 车卡引导状态机（2014/2024 双路径）+ 群级开卡规则；v0.35.0 start 支持 race/class_name/background 预填（构筑联动，复用知识库校验，合法跳步/非法回退） | `ChargenRule` / `ChargenDraft` / `AbilityGenMethod` / `StepReply` | `/车卡` `/车卡规则` `guide_chargen` |
| `chargen_engine.py` | 规则引擎：战斗字段 base 层重算（纯函数，含 v0.30.0 先攻=敏捷修正） | `RecalcReport` | 被 character/chargen 调用 |
| `build_advisor.py` | 构筑咨询纯函数层（v0.35.0）：goal 自由文本消歧（别名归一+CJK 复合词抽取+查库 facet 归属）→ 各维度候选档案组装；专长前置「标注不过滤」（✅/❌/⚠️）；职业法术表查询；确定性法术环上限表 | `BuildDossier`（dict 结构见 ADR-0012） | `advise_build`（不持久化） |
| `card_import.py` | 文本角色卡宽松解析落库（纯函数） | — | `/卡 导入` `/车卡 导入` |
| `kb.py` | 知识库 Manager + 查询/筛选/格式化；v0.36.0 起合并运行期私设 overlay | `KbEntry` / `SearchHit` / `FilterResult` 等 | `/查X` `/筛X` `/查询` `kb` `query_dnd_knowledge` |
| `kb_tags.py` | 构建期标签提取（facet/value 归一化） | — | 仅构建期 |
| `kb_enums.py` | 中文↔英文枚举映射（学派/伤害/稀有度/类型…）；v0.40.0 加怪物阵营映射 `ALIGN_ABV_CN` + `format_alignment`（渲染规则与 5etools-cn 站点一致）与类型反查 `MONSTER_TYPE_CN_REV` | `RARITY_CN` 等 | 查询期 |
| `kb_build_lib.py` | 条目渲染/侧表提取共享纯函数（v0.36.0 从 build_kb.py 抽取）：`_kind_body` 正文渲染链、`is_machine_entry` 机翻判定、`_parse_cr`/`_fmt_ac`/`_fmt_hp` 数值解析、`_ability_payload`/`_item_combat_cols`/`_item_value_weight` 侧表工具；v0.40.0 怪物头部渲染升级——体型中文化 + 类型/阵营段（`_monster_type_line`，缺失阵营显示「不固定阵营」）+ 挑战等级附 XP/巢穴/PB（`_cr_text`，CR→XP 表 `_CR_XP` + `_proficiency_bonus`） | — | 构建期（build_kb.py）+ 运行期（homebrew.py）共用 |
| `homebrew.py` | 运行期私设 overlay（v0.36.0）：扫描 `{data_dir}/trpg_homebrew/*.json` 双格式解析 → 内存条目池；reload 原子替换；overlay 侧三级搜索/结构化过滤 | `HomebrewEntry` / `HomebrewLoadResult` / `HomebrewManager` | `/kb reload` `/kb 私设` |
| `homebrew_writer.py` | 私设文本校验/文件名安全化/条目级 merge/原子写（v0.37.0，纯函数，不 import astrbot）；权威解析复用 `HomebrewManager` 临时目录试加载（零漂移） | `RawEntry` / `HomebrewValidation` / `WriteOutcome` | `manage_homebrew` |
| `main.py` | 插件主类：命令注册、路由、鉴权、LLM 工具 | — | 全部命令 |

---

## 3. KV 持久化全景（AstrBot KV）

统一模式：**读-改-写在同一把 `asyncio.Lock` 内完成**（每个 Manager 一把锁）；
`from_dict` 一律容错脏数据（类型错误回落默认值、坏条目直接丢弃）。
`{origin}` = `event.unified_msg_origin`（群聊/私聊各自独立）；`{sender}` = 发送者 ID。

| 完整 key | 值结构 | 管理模块 | 备注 |
|---|---|---|---|
| `history:{origin}` | `HistoryEntry` dict 列表（追加，超上限丢最旧） | `RollHistoryManager._lock` | 失败投掷不写；`clear` 与 `add` 同锁 |
| `initiative:{origin}` | `InitiativeState` dict：`{"entries":[…], "current_seq": seq\|null, "round": n}` | `InitiativeManager._lock` | 排序 = 先攻值降序、同值 `seq` 升序 |
| `session_sides:{origin}` | `int`（会话默认骰面数） | main.py | 无锁（单键读写）；读时 clamp 2–`max_dice_sides` |
| `custom_prefix:{origin}` | `str`（会话自定义触发前缀） | main.py | 内存 LRU 缓存（512 条 / TTL 300s）加速 |
| `inventory:{origin}:{sender}` | `Inventory` dict：`{"items":[ItemEntry…]}` | `InventoryManager._lock` | 个人背包 |
| `inventory:party:{origin}` | 同上 | 同上 | 队伍背包（群聊独有，私聊不存在） |
| `shop:{origin}` | `Shop` dict：`{"entries":[ShopEntry…], "buyback_rate": float}` | `ShopManager._lock` | 单店/会话 |
| `character:{origin}:{sender}:{卡名}` | `CharacterSheet` dict（完整字段见 §5.6） | `CharacterManager._lock` | 一卡一 key |
| `character:index:{origin}:{sender}` | `{"names": [卡名…]}` | 同上 | 显式索引，否则卡会成孤儿 |
| `character:active:{origin}:{sender}` | `{"name": 卡名}` | 同上 | 活跃指针；删活跃卡回退索引第一张 |
| `character:draft:{origin}:{sender}` | `ChargenDraft` dict（车卡状态机中间态） | `ChargenManager._lock` | 落库后删除；跨重启可续传 |
| `chargen_rule:{origin}` | `ChargenRule` dict（群级开卡规则） | `ChargenManager._lock` | DM/管理员写 |

**跨域锁序（ADR-0010）**：商店买卖跨 shop + inventory 两把锁，固定
**先 Shop 锁、后 Inventory 锁**；背包侧由 `settle_purchase` / `settle_sale`
单次调用同锁内完成（源头不产生半写入）。

---

## 4. 知识库 SQLite schema（v10，`kb_data/dnd_kb.db`）

只读；schema 版本 `KB_SCHEMA_VERSION = "9"`（`scripts/build_kb.py`），结构变更 +1 并重建；
运行期 `resolve_db_path` 发现库版本过低自动回退内置库。`meta` 表存版本/构建信息。
**数据获取（v0.40.0 起）**：`scripts/fetch_cn_data.py` 直连下载 5etools-cn 数据
（GitHub git trees API + raw，约 20MB，commit 默认取内置库 `meta.source_commit`
保证零漂移）+ `scripts/fetch_en_spells.py`（英文法术源查找表，职业法术表用）。

```sql
entries(id PK, kind, name, eng_name, source, edition, body, is_machine, UNIQUE(kind,name,source))
-- kind ∈ 法术/怪物/物品/专长/背景/职业/状态/种族/子职；同名多版本=多行；body=清洗后纯文本

aliases(alias, entry_id, PK(alias,entry_id))          -- 别名精确搜索索引（中英文名全小写）

spells(entry_id PK, level, school, ritual, concentration, components, range_feet, range_type,
       summary TEXT DEFAULT '')   -- v0.27.0 加 summary：AI 一句话概要（源 kb_patches/spell_enrich.json）
-- v0.35.0「职业法术表」：法术→主职业（英文 5e.tools 法术源查找表
--   gendata-spell-source-lookup.json 按 ENG_name+source 匹配；构建脚本
--   scripts/fetch_en_spells.py 下载 + build_kb --en-spell-lookup 参数；
--   class_name 为库内中文职业名，子职/领域附赠不做）
spell_classes(entry_id, class_name, PK(entry_id,class_name)) + idx_spell_classes_cn(class_name)
monsters(entry_id PK, cr REAL, mtype, size)
items(entry_id PK, rarity, attunement, value_cp, weight_lb)   -- v5 加 value_cp(铜币)/weight_lb(磅)
class_features(id PK, class_name, subclass_name, subclass_short, source, level, name, summary, body)

-- v0.13「特性反查」：facet=维度+关系，value=构建期归一化 canonical 中文
entry_tags(entry_id, facet, value, PK(entry_id,facet,value))  + idx_tags_fv(facet,value)
-- facet 全集：dmg_dealt/dmg_resist/dmg_immune/dmg_vuln/condition_inflict/condition_immune/
--   environment/weapon_property/spell_component/spell_shape/spell_target/base_item/item_type/
--   size/creature_type/speed_type/innate_spell
-- v0.25.0 专长反查：feat_type（类型：通用/起源/战斗风格/传奇恩惠/黑暗赠礼/龙纹）、
--   ability_increase（属性提升：choose 展开去重）、prereq_race/prereq_ability/
--   prereq_feat/prereq_feature（先决条件：种族名/「敏捷 13」/前置专长含去括号基础名/特性名）
-- v0.26.0 专长能力标签：feat_keyword（AI 生成语义关键字，词表见
--   kb_enums.FEAT_KEYWORD_TAGS：攻击方式/战斗输出/动作/防御/机动/控场/施法/技能/探索/特殊，
--   允许少量词表外自由词）
-- v0.27.0 法术能力标签：spell_keyword（AI 生成语义大类，词表见
--   kb_enums.SPELL_KEYWORD_TAGS：控场/伤害/治疗/增益/减益/召唤/位移/防护/侦查/潜行/
--   社交/探索/幻术/即死/造物/战斗辅助/施法辅助，与 dmg_dealt/condition_inflict 互补，
--   允许少量词表外自由词）
-- v0.33.0 职业/子职富化：class_keyword（职业能力标签，词表见
--   kb_enums.CLASS_KEYWORD_TAGS：战斗方式/施法/辅助/技能倾向/属性依赖/特殊机制）、
--   class_role（职业定位：武者/奥法/神职/专家）、subclass_keyword（子职能力标签，
--   词表见 kb_enums.SUBCLASS_KEYWORD_TAGS：定位倾向/主题风味/特色机制，含八大魔法学派）
-- v0.34.0 种族/背景富化：race_keyword（种族能力标签，词表见
--   kb_enums.RACE_KEYWORD_TAGS：属性倾向/战斗方式/防御生存/机动/技能倾向/
--   主题风味/特殊机制，收编 5etools 官方 traitTags）、background_keyword
--   （背景能力标签，词表见 kb_enums.BACKGROUND_KEYWORD_TAGS：属性倾向/技能倾向/
--   身份主题/工具装备/特殊机制）

-- v0.16「种族」数值侧表；等值维度（体型/类型/抗性/施法/速度类型）走 entry_tags
-- v0.34.0 加 summary 列（AI 一句话概要，源 kb_patches/race_enrich.json）
races(entry_id PK, speed_walk, speed_climb, speed_swim, speed_fly, speed_burrow,
      darkvision, summary TEXT DEFAULT '')

-- v0.18「规则引擎」战斗侧表
class_combat(entry_id PK, hd_faces, saves, caster, spell_ability)          -- caster ∈ full/1/2/1/3/pact/artificer/''
subclass_caster(id PK, class_name, subclass_name, subclass_short, source, caster, spell_ability)
class_starting_equipment(entry_id PK, payload)   -- startingEquipment 原样 JSON（含 goldAlternative）
background_ability(entry_id PK, payload)         -- ability 数组原样 JSON（2024 weighted choose）
race_ability(entry_id PK, payload)               -- ability 数组原样 JSON（2014 flat/choose）
item_combat(entry_id PK, ac, armor_type, strength, stealth, dmg1, properties, range_note)

-- v0.26.0「专长标签反查」：feats 侧表存 AI 生成的一句话概要
--   （summary，源 kb_patches/feat_enrich.json；276 条全覆盖，双版本分别生成）
feats(entry_id PK, summary TEXT DEFAULT '')

-- v0.27.0「法术标签反查」：spells.summary（AI 一句话概要，源
--   kb_patches/spell_enrich.json；554 条全覆盖）+ entry_tags.spell_keyword（语义大类）

-- v0.33.0「职业/子职富化」：classes 侧表存 AI 生成的一句话概要（summary）与职业定位
--   （role，武者/奥法/神职/专家，仅职业有），源 kb_patches/class_enrich.json
--   （职业 29 条含 role + 子职 186 条，排除 UA/Plane Shift 跨界，合计 215 条全覆盖）
classes(entry_id PK, summary TEXT DEFAULT '', role TEXT DEFAULT '')

-- v0.34.0「种族/背景富化」：backgrounds 侧表存 AI 生成的一句话概要（summary，
--   源 kb_patches/background_enrich.json；148 条全覆盖）；语义标签走 entry_tags
--   的 background_keyword facet（词表见 kb_enums.BACKGROUND_KEYWORD_TAGS）。
--   种族侧表 races 加 summary 列（见上），语义标签走 race_keyword facet
--   （词表见 kb_enums.RACE_KEYWORD_TAGS，160 条全覆盖）。
backgrounds(entry_id PK, summary TEXT DEFAULT '')

meta(key PK, value)                              -- schema_version / 数据源 / 构建时间等
```

**查询路径**：精确搜索走 `aliases` → 名称 LIKE → 逐字缩短（三级容错，不用 FTS5）；
筛选走 `entry_tags` 的 `facet,value` 反向索引（`idx_tags_fv`）；职业特性走 `class_features`
（按 `class_name(+subclass_name)` + `level ≤ N` 过滤；v0.29.0 起支持 `feature` 参数
细化本职特性：`"*"`=全部本职特性全文、具体名=单个特性跨版本全文，`feature_query`
标记细化模式）。数据补丁：`kb_patches/` 白名单
通道构建期合并（见 §9 与 `scripts/fetch_chm_patch.py`；专长概要/关键字走独立补丁
`kb_patches/feat_enrich.json`，法术概要/关键字走 `kb_patches/spell_enrich.json`，
职业/子职概要/关键字/定位走 `kb_patches/class_enrich.json`，
种族/背景概要/关键字走 `kb_patches/race_enrich.json` + `background_enrich.json`，
均 AI 逐条生成，构建期分别写 feats.summary + feat_keyword / spells.summary +
spell_keyword / classes.summary + class_role + class_keyword + subclass_keyword /
races.summary + race_keyword / backgrounds.summary + background_keyword）。
reprintedAs（再版跳转）：item/法术跳转到新版，condition/race/feat 豁免（2014/2024 规则
版本并存——目盲、阿斯莫、幸运等旧版文本仍保留）。

**运行期私设 overlay（v0.36.0，唯一不经构建的增补通道）**：官方库保持只读随包；
DM 在 `{AstrBot data_dir}/trpg_homebrew/*.json` 放置私设条目（5etools 标准格式或
简化 kind/name/source/body 格式），`KbManager` 懒加载时经 `homebrew.py` 解析进内存
overlay 池（`/kb reload` 重载，reload 原子替换）。查询层 search/detail/filter 在
官方结果基础上合并 overlay：私设命中置顶并标注 `🏠房规`；与官方 `(kind,name,source)`
相同的条目为**覆盖**（房规修正）。条目正文渲染复用 `kb_build_lib._kind_body`
（与构建期同源，零漂移）；侧表字段经 `_extract_side_fields` 轻量提取（字段缺失即
不参与对应维度筛选）。LLM 工具 `query_dnd_knowledge` 走同一查询层，自动可查私设
（带标注，防 LLM 混淆官方/房规）。边界：私设职业/子职不参与规则引擎联动；简化格式
缺结构化字段时对应筛选维度不命中。

---

## 5. 数据模型（dataclass 字段）

约定：每个可持久化模型有 `to_dict()` / `from_dict()`（后者容错脏数据）；
纯函数结果模型（`*Result`）不落库。字段未列出的默认值见代码。

### 5.1 骰池（dice_roller.py，上游）
`DieRoll`（单骰）、`DiceGroupResult`（骰组）、`RollResult`（整式结果：label/dice/total/
细节文本）。解析产物 `dice_parser` 产生，格式化 `formatter.py` 负责，不落库。
⚠️ parser 为**上游只读扩展**：中文「优势/劣势」（`d20优势` 系列，v0.38.0）不在
parser 层加语法，而在 main.py `_do_roll` 命令层映射——`_map_zh_adv_dis` 把紧贴骰式
后缀的「优势/劣势」替换为引擎 adv/dis 语法糖（孤立词如 `d20 优势`/`优势d20` 报错），
三个掷骰入口（/r、自定义前缀、roll_dice 工具）自动全部生效。详见
`docs/adr/0014-zh-adv-dis-command-layer.md`。

### 5.2 历史（history.py）
`HistoryEntry`：`expr`(表达式) / `result`(结果首行，截断) / `sender_id` / `sender_name` / `ts`(`MM-DD HH:MM:SS`)。

### 5.3 先攻（initiative.py）
- `InitiativeEntry`：`name` / `value`(先攻总值) / `modifier`(预留) / `user_id`(预留，怪物空) /
  `is_fixed`(固定值 vs 掷骰) / `seq`(入列序号，同值先报先动)。
- `InitiativeState`：`entries` / `current_seq`(当前行动者 seq，None=未开战) / `round`(轮数，0=未开始)。
- `AdvanceResult` / `RemoveResult`：推进/移除后的状态 + 播报素材。

### 5.4 背包（inventory.py）
- `ItemEntry`：`name` / `qty`(≥1，归零删条目) / `weight`(单件磅，None=未设) /
  `value`(**铜币**，None=未设) / `note`。
- `Inventory`：`items`(有序列表)；`total_weight/total_value` 返回 `(合计, 是否有未设项)`
  （未设项按 0 计并标 `+`「至少」）。货币条目按面值计，不看 `value` 字段。
- `TransferResult` / `RemoveResult`：流转/移除结果。

### 5.5 商店与货币（shop.py / money.py）
- `ShopEntry`：`name` / `price_cp`(售价覆盖，None=库价) / `stock`(None=无限，0=售罄) /
  `weight_lb`(知识库带出，购买时写入背包)。
- `Shop`：`entries` / `buyback_rate`(回购系数，clamp 0–2)。
- `BuyResult`：`ok`/`reason`(`not_found|sold_out|no_price|insufficient_money`)/`price_cp`/
  `total_cp`/`shortfall_cp`/`stock_left`。`SellResult` 对称（`reason` 含 `insufficient`)。
- v0.39.0 批量与清空：`_parse_batch_name_qty`（买/卖共用，数量可省略贪心解析）、
  `_parse_batch_shop_add`（上架逐项属性归属）；`ShopManager.clear(origin)` 清空全部
  商品条目但保留回购系数；批量逐件原子（每件独立锁往返，失败项列明、其余继续，
  ADR-0015）。LLM 工具 `manage_shop` 增 `items(array)` 批量参数并开放 add/remove/
  clear（工具内 `_check_destructive_permission` 鉴权）。
- money.py 纯函数：`COIN_VALUE{金币:100,银币:10,铜币:1}` / `to_copper` / `to_money` /
  `make_change`(greedy 大币优先) / `parse_money`(纯数字=铜币) / `format_cp`→「X金Y银Z铜」/
  `settle_payment`(整币优先，最多破一枚大币)。

### 5.6 角色卡（character.py）
- `AbilityScores`：`strength/dexterity/constitution/intelligence/wisdom/charisma`(默认 10)。
- `ClassLevel`：`class_name` / `subclass` / `level`。
- `LayeredStat`：`base` + `bonus`（**战斗字段双层**：base 归规则引擎自动算，bonus 是房规手动层，
  任何自动重算不覆盖 bonus；显示=base+bonus）。
- `EquipmentSlots`：`main_hand` / `off_hand` / `armor`。
- `CharacterSheet`：`name` / `edition`(2014|2024) / `classes`(兼职列表) / `race` / `background` /
  `alignment` / `ability_scores` / `skill_proficiencies` / `save_proficiencies` /
  `skill_expertise` / `feats` / `tool_proficiencies` / `weapon_proficiencies` /
  `armor_proficiencies` / `languages`(v0.28.0，多门语言，自由文本无词表校验，
  仅清洗+去重+限条数 30) / `deity`(信仰) / `age` / `gender` / `height` / `weight`
  (v0.30.0，人物基础信息，自由文本) / `hit_dice_used`(短休已用生命骰 0–20) /
  `inspiration`(激励 0/1) / `initiative`(先攻 LayeredStat：base=敏捷修正由规则
  引擎重算，bonus=房规额外加值如警觉 +5) / `spells`(已知法术 `dict[str,list[str]]`，
  v0.30.0，环阶 key 归一为「戏法」+「1」..「9」，法术名保留括号标注，每环限 50 条) /
  `hp_max` / `ac` / `speed` / `spell_slots`(dict[str,LayeredStat]) /
  `attack_bonuses`(dict[str,LayeredStat]) / `equipment` / `backstory` / `named_rolls`(dict[str,str]，
  v0.32.0 起被 `/r` 联动消费：整词命中命名掷骰键优先于内建别名，用登记表达式直接掷；
  `/卡 骰 <名> -` 删除，`/卡 详情 掷骰` 查看)。
  新字段一律「缺失即空」零迁移（v0.21 五熟练字段先例；v0.30 九字段同规则）。

### 5.7 车卡（chargen.py）
- `AbilityGenMethod`：`kind`(point_buy|roll|standard_array) / `pool` / `min_score` / `max_score` /
  `expr`(骰式) / `count`(掷骰次数) / `array`(标准数组)。参数化模板，别名 27buy/32buy/dnd5。
- `ChargenRule`：`edition` / `ability` / `subclass_at_creation`(auto|on|off) /
  `starting_level`(1–20) / `starting_gold`(`auto`|纯数字=固定|骰式=代骰，单位=金币)。
- `ChargenDraft`：`state`(状态机步骤) / `edition` / `data`(race/class_name/subclass/background/
  species/alignment/name) / `ability_pool`(代骰结果) / `ability_detail`(代骰明细) /
  `ability_assign`(加值前六维分配) / `ability_bonus`(choose 加值选择 dict) /
  `backstory_parts`(origin/decision/event) / `starting_level`。
- `StepReply`：`progress` / `check` / `next_question` / `done`（工具返回驱动三段式）。

### 5.8 知识库（kb.py）
- `KbEntry`：`kind` / `name` / `eng_name` / `source` / `edition` / `body` / `is_machine` +
  过滤侧表字段（`level/school/cr/mtype/size/rarity/attunement` + 种族 `speed_*`/`darkvision` +
  专长 `feat_summary`（v0.26.0，AI 一句话概要，详情/筛选/搜索带出）+
  法术 `spell_summary`（v0.27.0，AI 一句话概要，同上）+
  职业/子职 `class_summary` + `class_role`（v0.33.0，AI 一句话概要 + 职业定位，
  同上；`/查职业` 头部经 `ClassFeatureResult.class_summary/class_role` 展示）+
  种族/背景 `race_summary` + `background_summary`（v0.34.0，AI 一句话概要，
  同上；`/查种族` `/查背景` 头部展示）。
- `SearchHit`（搜索候选：`summary` 截断摘要，专长优先用 AI 概要）、
  `FilterResult`（`entries`+`total` 未限量总数）。
- `ClassFeatureRow` / `ClassFeatureResult`（职业特性，含 `subclass_candidates`；
  v0.29.0 加 `feature_query`：`""`=不细化、`"*"`=输出全部本职特性全文、
  其他值=输出名称匹配（跨版本）的单个特性全文）。
- 规则引擎侧表行：`ClassCombatRow`（`hd_faces/saves/caster/spell_ability`）、`ItemCombatRow`。
- 属性加值：`ChooseSpec`(`kind`=count|weighted, `from_set`, `count`, `weights`)、
  `AbilityOffer`(`flat` + `chooses`)。

---

## 6. 一致性机制

| 机制 | 说明 |
|---|---|
| 单 Manager 单锁 | 读-改-写在一个 `asyncio.Lock` 内，防并发互相覆盖（先攻/历史/背包/商店/角色卡/车卡） |
| 跨域锁序 | 商店买卖固定 Shop→Inventory（ADR-0010）；背包侧 `settle_purchase/settle_sale` 单调用原子 |
| 流转原子性 | put/take/give 源扣除与目标写入同锁完成，源不足时两侧均不变 |
| 破坏性操作鉴权 | 群聊中清空历史/清先攻/清背包/商店管理/车卡规则写入走 `_check_destructive_permission`（白名单/管理员）；私聊放行 |
| 私设写入鉴权 | `manage_homebrew` write（v0.37.0）走独立 `_check_homebrew_write_permission`：白名单/管理员，**私聊不放行**（私设是插件级全局数据，ADR-0013） |
| 私设写盘 | 临时文件 + `os.replace` 原子写；插件级 `asyncio.Lock` 串行化「检查冲突→写盘→reload」临界区；写后 `reload_homebrew` 同步替换 overlay（v0.37.0） |
| 容错读取 | 所有 `from_dict` 容忍脏数据：类型错回落默认、坏条目丢弃（防手改 KV） |
| 会话隔离 | 一切按 `origin` 分键；群聊全员可见（历史/先攻/队伍背包/商店），个人背包/角色卡按 `sender` 分键 |

---

## 7. 命令与工具入口

**三入口复用同一核心**：`@filter.command`（`/xxx`）+ 自定义前缀路由（`custom_prefix_route`，
长 token 命令先于短 token）+ `@filter.llm_tool`（单工具 + action 枚举，参数全带默认值）。
命令 handler 统一形态 `_handle_xxx(event, arg, display_prefix)`。

| 命令（主名，别名） | 功能域 | 备注 |
|---|---|---|
| `r`（roll）| 骰池 | 首 token 整词命中时联动活跃卡（属性/技能/豁免/攻击检定；v0.32.0 加命名掷骰，优先于内建别名）；也响应 `dset`/`rprefix` 前缀族 |
| `dnd` | 属性生成 | v0.38.0：按 5e 规则掷 `4d6kh3`×6 为一组（组数默认 1、上限 20，模块级常量 `_DND_MAX_GROUPS` 不新增配置），复用 `_roll_chargen`，成功写历史 `dnd N`；独立生成指令，不与车卡联动（走 `/车卡` 的掷骰开卡才落草稿） |
| `dset`（dice_set）/ `rprefix` | 会话骰面/触发前缀 | 白名单管控 |
| `rh`（rhistory）| 历史 | |
| `ri` / `init`（initiative）| 先攻 | v0.30.0：`/ri` 无参数且有活跃角色卡时自动用卡上先攻（d20+先攻并标注角色）；显式调整值优先 |
| `bag`（inventory, 背包）| 背包 | |
| `shop`（商店, 店铺）| 商店 | v0.39.0：批量买/卖（数量可省略）、批量上架（逐项属性）、批量下架、`清空`（整店清空，管理员） |
| `卡`（char, 角色卡）| 角色卡 | v0.31.0：攻击条目删除（`/卡 设 攻击 名=-`）与已知法术单条增删（`/卡 法术 加|删 <环阶> <法术名>`）；v0.32.0：命名掷骰全套 CRUD（`/卡 骰 <名> <表达式>` / `<名> -` 删除 / `/卡 详情 掷骰`） |
| `车卡`（chargen）| 车卡引导 | |
| `车卡规则`（车规, chargenrule）| 群开卡规则 | |
| `查法术`(spell) `查怪`(monster,怪物) `查物品`(item,物品) `查专长`(feat,专长) `查背景`(background,背景) `查状态`(condition,状态) `查种族`(race,种族) `查职业`(class,职业) `kb` `查询`(search,搜,q) `筛怪`(mfilter,筛怪物) `筛法术`(sfilter,筛魔法) `筛物品`(ifilter,筛道具) `筛种族`(rfilter,筛血统) `筛专长`(ffilter,专长筛) `筛职业`(cfilter,职业筛) `筛子职`(sublass_filter,子职筛) `筛背景`(bfilter,背景筛) | 知识库 | 筛专长 v0.26.0 起支持能力标签反查；筛法术 v0.27.0 起支持语义大类标签反查（裸词自动消歧，如「控场/治疗/伤害/召唤」；前缀词「标签」显式指定；别名归一），v0.35.0 起支持职业法术表反查（前缀词「职业 法师」，中英文职业名均可）。筛职业/筛子职 v0.33.0 起支持定位+能力标签反查（职业：`/筛职业 武者` 定位、`/筛职业 近战 爆发` 标签、`/筛职业 奥术施法 智力`；子职：`/筛子职 治疗 神圣`、`/筛子职 塑能`；前缀词「定位/标签」；裸词自动消歧）。筛种族 v0.34.0 起支持能力标签反查（裸词自动消歧，如「变形/水陆两栖/魅力」→ race_keyword，伤害词「火焰/光耀」仍优先走天生抗性 dmg_resist；前缀词「标签」）。筛背景 v0.34.0 新建（裸词技能/身份/工具/起始专长反查，如 `/筛背景 隐匿 盗贼工具`、`/筛背景 贵族`；前缀词「标签」）。查职业 v0.29.0 起第二参数支持「特性」关键词细化本职特性：`/查职业 <职业> 特性`（全部本职特性全文）、`/查职业 <职业> 特性 <特性名>`（单个特性跨版本全文）；v0.33.0 起 /查职业 头部展示职业定位与 AI 概要；v0.34.0 起 /查种族 /查背景 头部展示 AI 概要；`/kb reload`（v0.36.0 重载私设目录）/`kb 私设`（查看私设概况） |
| `帮助`（menu,菜单,commands,cmds）| 帮助 | |

**LLM 工具（9 个）**：`roll_dice` / `manage_initiative` / `manage_inventory` /
`manage_shop`（v0.39.0：新增 `items(array)` 批量参数；动作扩为
list/buy/sell/add/remove/clear，管理动作在工具内 `_check_destructive_permission`
鉴权，非管理员拒绝） / `manage_character` / `guide_chargen` / `query_dnd_knowledge` /
`advise_build`（v0.35.0 构筑咨询：new_build=从零构筑（含背景推荐）、
level_up=升级建议，插件直读活跃卡；防幻觉输入侧约束——候选档案全部由
build_advisor 从知识库确定性组装，docstring/守则明文禁止凭记忆补条目名） /
`manage_homebrew`（v0.37.0 私设助手：convert=纯文本→私设 JSON 双程校验回执、
write=原子落盘 `trpg_homebrew/` 并自动 reload（配置 `homebrew_write_enabled`
默认关 + 白名单/管理员双闸，私聊不放行；冲突默认拒绝，DM 确认后
overwrite/merge），review=草稿锚点+同名命中，对照取料由 LLM 自主调
query_dnd_knowledge，守则强制先查库再点评）。
⚠️ 参数 schema 由 **docstring 的 Google 风格 `Args:` 段**生成（不从函数签名解析），
改参数必须同步 docstring 与 `tests/test_llm_tool_schema.py`。AstrBot v4.5+ 需在
WebUI 工具面板手动启用（默认不注入 LLM）。
**LLM 请求前钩子（v0.28.1）**：`_on_llm_request_guard`（`@filter.on_llm_request`）
向每个 LLM 请求的 system_prompt 末尾追加压缩版「跑团助手·工具守则」
（`_llm_request_guard` 静态方法生成，带可检索标记、防重复追加、异常静默容错）：
全部 9 个工具按场景一句一条（掷骰/检定→roll_dice、规则数据→
query_dnd_knowledge、先攻→manage_initiative、战利品/消耗→manage_inventory、
角色卡→manage_character、车卡→guide_chargen 禁扮演编卡、买卖→manage_shop、
构筑建议→advise_build、私设转录/写入/点评→manage_homebrew（v0.37.0，含
「点评前必须用 query_dnd_knowledge 查同类型条目对照」强制句）），
并明确清空/删除/移除/取消等破坏性操作不主动执行。目的：防模型在开放式任务
上「扮演/编造」而不调工具（工具描述只在调用时可见，守则注入 system_prompt
在生成回复前即生效）。
`query_dnd_knowledge` 共 40 参数（v0.26.0 加 `feat_type`/`feat_keywords`，filter 打开
对 kind=专长的支持：`feat_keywords` 逗号分隔多标签 AND、别名归一，返回条目带一句话概要，
供 LLM 结合角色卡/团情给出专长选取建议；v0.27.0 加 `spell_keywords`，同法术标签反查，
返回条目带一句话概要；v0.29.0 加 `feature`，class_features 本职特性细化：
`"*"`=全部本职特性全文、具体名=单个特性跨版本全文；v0.33.0 加
`class_role`/`class_keywords`/`subclass_keywords`，filter 打开对 kind=职业/子职的支持：
定位与能力标签逗号分隔 AND、别名归一，返回条目带一句话概要，供 LLM 给出职业/子职
选取建议；v0.34.0 加 `race_keywords`/`background_keywords`，filter 打开对
kind=种族/背景的支持：能力标签逗号分隔 AND、别名归一，返回条目带一句话概要，
供 LLM 给出种族/背景选取建议；v0.35.0 加 `spell_class`（职业法术表反查，
中英文职业名均可）与 `class_level`（class_features 等级过滤，如「野蛮人 7 级
获得什么」→ action=class_features + class_level=7，38→40 参数）。
`guide_chargen` 参数（v0.35.0）：action=start 新增 `race`/`class_name`/
`background` 可选预填（值必须来自 advise_build 档案；chargen.py start 逐项
复用知识库校验，合法跳步、非法回退正常询问并在 check 注明；2014 全预填→
属性步，2024 物种写入 species）。
`_llm_request_guard` 守则（v0.35.0 更新）：追加「构筑/升级建议→advise_build
（推荐条目必须来自工具返回，禁止凭记忆补充条目名）」。

---

## 8. 版本与兼容策略

- **知识库**：schema 变更 bump `KB_SCHEMA_VERSION` + `scripts/build_kb.py` 全量重建；
  运行期版本检查不匹配自动回退内置库（`kb_update.db` 更新通道已预留：GitHub Release +
  sha256 + `os.replace`）。**私设（v0.36.0）独立于 schema**：overlay 在 `data_dir/trpg_homebrew/`，
  官方库升级不触碰；schema 升级不影响私设文件（加载器按当前官方库键集判定覆盖）。
  **私设写入（v0.37.0）配置 `homebrew_write_enabled` 默认关**：升级用户不受
  影响（仅 convert/review 可用），既有私设文件不被插件改写。
- **KV**：无迁移框架，靠「新字段缺失即空」向前兼容；key 结构变更=数据不可读（历史教训：
  v0.10.0 改名后 AstrBot 按插件 ID 隔离 KV，旧数据不迁移）。
- **打包**：zip 顶层为插件文件夹，含 `scripts/`/`tests/`/`docs/`（`docs/ARCHITECTURE.md`
  随包分发），排除 `.git`/`__pycache__`/`.pytest_cache`/`.cache`；产物放 workspace 根 `dist/`。

---

## 9. 新功能检查清单

1. **一功能一模块**：dataclass + `to_dict/from_dict`（容错）+ Manager（单锁）+ `format_*` 静态方法。
2. **KV key 前缀登记**：新 key 用功能前缀 + `origin`(+`sender`)，登记到 §3 表，避免撞车。
3. **三入口复用**：命令/前缀路由/LLM 工具共享同一核心 handler；工具 docstring `Args:` 同步。
4. **锁与权限**：读-改-写单锁；群聊破坏性操作走 `_check_destructive_permission`。
5. **领域语言**：新概念先看 `CONTEXT.md` 术语，冲突以它为准。
6. **测试**：`tests/` 用 AstrBot 替身（假 Star/假事件）全链路覆盖；知识库用例走
   `tests/fixtures/kb_sample/` 迷你库。运行：
   `python -m pytest tests/ -q -p no:cacheprovider`（插件目录下）。
7. **交付**：版本 bump → CHANGELOG/README/metadata → 全量测试 → 重建 kb（如涉及）→
   zip 到根 `dist/` → 部署说明 → git commit → 记忆日志。

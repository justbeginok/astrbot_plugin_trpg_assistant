# astrbot_plugin_trpg_assistant

AstrBot 的跑团助手插件：骰池投掷、投掷历史、战斗先攻与背包管理。

## Language

### 通用

**会话来源（unified_msg_origin）**:
AstrBot 提供的会话隔离标识，群聊与私聊各自独立。所有按会话存储的数据都以它为 key 的一部分。

**玩家（Player）**:
会话中的一名参与者，以发送者 ID（sender_id）标识。同一玩家在不同会话中是不同实体。

**纯文本输出（Plain-Text Output，v0.42.0）**:
跨模块的展示约定（ADR-0017）：所有用户可见输出以纯文本为准，去掉
QQ/onebot 不解析的 Markdown 控制符号（成对 `**`/`*`/`~~`、行首 `` ` ``/`>`/`#`），
保留 `- ` 列表、`1. ` 编号、`——…——` 分隔装饰、emoji、`×`/`→` 等纯文本
排版与骰式表达式内的语义符号（`3d6>3`、`d20+5#标签` 的 `>`/`#` 按字面
渲染，属表达式语法不误删）。守卫测试 `test_no_markdown_output.py` 防回潮。
_Avoid_: 让 LLM 输出 Markdown（同一约定适用于 LLM 工具返回值）

### 骰子

**多重投掷（Repeat Roll，v0.47.0）**:
骰式 `N#expr`（如 `3#d20+d6`）表示把整条表达式重复投掷 N 次（Roll20 语义），
逐行输出每次明细并汇总合计/平均。`#` 仅在左侧整体为纯数字且位于表达式开头时
才视为重复前缀，其余 `#` 仍是标签分隔符（`d20+5#攻击`）。重复次数上限 20
（配置项 `max_repeat_count`）；多重投掷不支持 DC 与大成功/大失败判定。
_avoid_: 把「重复」理解为只重复第一个骰组

**骰池表达式树（Dice AST，v0.47.0）**:
四则运算（+ - * /）与括号的递归下降结构化表示，骰组为叶子、常数为叶子、
运算与括号为节点。解析后若为纯 +/- 和则回退为扁平 `groups + flat_modifier`
（保持历史输出不变），否则保留 AST 走独立求值路径。
_avoid_: 仅靠正则拼凑骰式运算

**骰数算式 / 骰面算式（Count/Sides Expression，v0.47.0）**:
骰数或骰面位置允许括号算式，如 `(2+3)d6`、`3d(2*4)`；括号内可含骰子
（`(1d6+1)d8`）。求值结果必须为正整数且受 `max_dice_count`/`max_dice_sides` 限制。
除法向下取整（Python `//` 语义），除零报错。

**计数组限根（Success-Count Root-Only，v0.47.0）**:
带 `>N`/`<N`/`fN` 计数修饰的骰组只能作为整条表达式单独使用，不得参与四则运算
或出现在括号中（含骰数/骰面位置的括号算式），否则报错。如 `3d6>3+2` 报错，
`3d6>3` 合法。

### 先攻

**先攻（Initiative）**:
一次战斗中各单位行动顺序的列表，按先攻值降序、同值先入列者先行动。

**单位（Entry）**:
先攻列表中的一行，可以是玩家角色或 DM 录入的怪物。
_Avoid_: 角色、成员

### 背包

**背包（Inventory）**:
一个物品条目的有序集合，按归属分为个人背包与队伍背包两类。

**个人背包（Personal Inventory）**:
归属单个玩家（会话来源 + 发送者 ID）的背包，只有本人能修改，他人可只读查看。

**队伍背包（Party Inventory）**:
归属整个会话的公共背包，会话内全员可存取，清空需管理员/白名单权限；私聊中不存在。
_Avoid_: 公共背包、团队仓库

**物品条目（Item Entry）**:
背包中的一行：名称 + 数量 + 可选的单件重量、单件价值、备注。同名条目在同一背包中至多一行。

**数量（qty）**:
条目必填字段，正整数；归零时条目自动删除，不允许存在 0。

**单件重量 / 单件价值（weight / value）**:
条目的可选字段。只统计不限制——背包显示总重量/总价值，是否超载由 DM 裁定，插件不判定。

**货币（Money）**:
不是独立概念。金币等货币就是普通物品条目，不做钱包字段、不做余额校验。
v0.20.0 起商店结算把「金币/银币/铜币」三条目（面值 100/10/1 铜币）作为
唯一货币口径：折铜扣款、自动找零；背包价值字段统一以铜币为单位。
_Avoid_: 钱包、账户、余额

**币制（Currency Form）**:
v0.20.0 起背包/商店的价值显示口径：价值字段一律为铜币整数（1金币=10银币
=100铜币），显示时经 format_cp 换算成「X金Y银Z铜」；货币条目的 value
按面值 100/10/1 自动维护，背包总价值=折铜后的财富总额。
_Avoid_: 无单位价值、金币浮点

**折铜结算（Copper Settlement）**:
v0.20.0 商店买卖的货币处理：把玩家三币种折成铜币总额校验与扣减，差额
自动找零（greedy 大币优先，整币优先、最多破开一枚最小可破大币，尽量保留
玩家原有币种组合）。算法在 money.py 纯函数层（settle_payment）。

**商店（Shop）**:
v0.20.0 起的会话级功能：DM 配置的商品列表 + 玩家自助买卖结算。一个会话
一家店（KV `shop:{origin}`，含商品列表与回购系数），命令 `/商店`，
llm_tool `manage_shop`（v0.39.0 起含 list/buy/sell/add/remove/clear，管理
动作在工具内鉴权，支持批量 items 参数）。买卖跨商店+背包两把锁，
固定锁序「先 Shop 锁，后 Inventory 锁」，背包侧由 settle_purchase /
settle_sale 单次调用原子完成。
_Avoid_: 市场、拍卖行、多店

**商品条目（Shop Entry）**:
商店列表中的一行：名称 + 可选售价覆盖（None=用库价）+ 可选库存
（None=无限，0=售罄）+ 知识库带出的单件重量（购买时写入背包条目）。

**库价（List Price）**:
商品价格的默认来源：知识库 items 表 value_cp（5etools PHB 定价，铜币）。
无售价覆盖且无库价的商品不可交易（提示 DM 设价）。

**售价覆盖（Price Override）**:
DM 为单个商品设置的专属价格（`/商店 设价 <名称> <金额|自动>`），优先于
库价；「自动」清除覆盖恢复库价。购买入包的价值字段=成交单价（覆盖价优先）。

**回购系数（Buyback Rate）**:
卖出价 = 商店当前售价 × 系数（默认 1.0 全价，`/商店 回购率` 设置，
clamp 0–2）。回购只收在架商品：计数库存 +qty，无限库存不变，
不上架新条目。

**批量交易（Batch Trade）**:
v0.39.0 起，一条命令/一次工具调用处理多件商品的形态：买/卖用「名称
[数量]」交替、数量可省略（默认 1，数字 token 归属前一名称）；上架用
「名称 [价=X] [库存=Y]」逐项属性归属。**逐件原子**：每件复用单件事务，
失败项单独列示原因、其余继续，不整批回滚。

**清空（Shop Clear）**:
v0.39.0 起，移除商店全部商品条目、保留回购系数的管理操作
（`/商店 清空`，权限同其他管理操作：群聊白名单/管理员、私聊放行）。
_Avoid_: 重置、重建（初始化是另一操作：从知识库重建并覆盖商品列表）

**物品流转（Transfer）**:
物品在背包间的移动：put（个人→队伍）、take（队伍→个人）、give（个人→个人，仅 @目标）。流转是原子的：源数量不足时两侧均不变。

**同名合并（Merge）**:
向背包放入已有同名物品时数量累加；本次调用提供了重量/价值/备注则覆盖旧属性，未提供则保留。

**编辑（Edit）**:
事后修改物品条目属性的操作（`edit`），只改重量/价值/备注三字段，不涉及数量与名称。

**清除标记（`-`）**:
`edit` 语法中把某属性改回「未设置」的写法：`w=-`、`v=-`、`note=-`。省略该键则保持原值。

**发放 / 收回（Grant / Revoke，v0.42.0）**:
DM 向队伍背包批量发放战利品（`/发放`，全员可用）与批量撤回（`/收回`，管理员/
白名单权限，私聊一律拒绝）的短命令。发放支持 `重=X`（单件重量）、`价=X`
（价值，`parse_money` 铜币，支持「2金5银」）、`备注=X` 逐项属性归属；
批量逐件原子、失败列明、成功 N 件/失败 M 件汇总。LLM 工具
`manage_inventory` 的 `remove`+`to_party=true` 与命令侧同口径鉴权
（ADR-0016），防止工具绕过命令侧收回权限。
_Avoid_: 战利品系统（只是队伍背包的便捷入口，不引入独立战利品模型）

**批量背包操作（Batch Inventory，v0.42.0）**:
`/发放`、`/bag add/rm/put/take` 一条指令多件物品的形态：非属性、非纯数字
token = 下一物品名（数量默认 1），纯数字 token = 前一物品的数量（连续
数字报「重复」）。单件回落原解析器保零回归（如 `/bag add 长剑` 仍报数量
必填）。与商店批量（v0.39.0）共用「逐件原子」语义。

### 知识库

**知识库（Knowledge Base）**:
插件内置的 DND 5e 中文资料库（只读 SQLite，随包打包）。数据源自 5etools 中文站的结构化 JSON，覆盖法术、怪物、物品、专长、背景、职业/子职六类。查询经 `KnowledgeBaseManager`，不走 KV。
_注（v0.43.0）_: 法术条目的中文详述/元数据改以 5e_chm Markdown（人工校对）为主源，5etools JSON 仅承担其余类别与 spell_classes 职业法术表（ADR-0018）。
_注（v0.45.0）_: 怪物筛选新增六维（伤害细分/状态免疫/速度类型/感官/阵营/特性名，ADR-0020）；第三方书怪物经 LLM 提取管道入库（试点：《火炬光下的克苏鲁》第七章 23 条，source=火炬光下的克苏鲁、edition=2024）。
_注（v0.49.0）_: 职业/子职特性数据改以 5e_chm 人工校对中文为主源（ADR-0024）：职业 30 条（含血族/铳士/拳斗士/血猎手/邪狱使等第三方职业）、子职 285 个、本职特性 828 行/子职特性 2586 行；规则引擎侧表（生命骰/豁免/施法进度/起始装备/富化概要）沿用 5etools-cn 结构化 JSON。提取管道 `scripts/class_extract/`（inventory → parser → emit → finalize → llm_fallback）。
_注（v0.50.0）_: 新增第七类「选项」（optionalfeature，ADR-0025）：魔能祈唤/战技/超魔法/战斗风格 153 条，5e_chm/md 规则解析入库（scripts/optional_extract/）；`/查祈唤|战技|修法|风格` + `/筛选项`（类型/先决反查）。

**可定制职业选项（Optional Feature，v0.50.0）**:
让职业高度可定制的选项：魔能祈唤（魔契师，featureType=EI）、战技（战斗大师/
战技专家，MV）、超魔法（术士，MM）、战斗风格（战士，FS）。条目含先决条件
（`prerequisite`，超魔法为术法点消耗）与正文；entry_tags 以 `feature_type`/
`prerequisite` 双 facet 反查。同名多版本（2014/2024）并列展示。

**5e_chm Markdown（chm 数据源）**:
本地人工校对中文资料 `5e_chm/md/`（htm→md 转换产物，7040 个 .md）。法术数据源的两类文件：**速查表**（`速查/法术速查/5E万法大全.md` 官方 936 条 + 合作方万法大全.md 第三方 284 条，HTML 表格 23 列结构化元数据）与**详述页**（按环阶打包或散落单文件，`#### 中文名｜English` + 元数据块 + 人工校对正文）。解析器 `scripts/chm_parser.py`，对账 `scripts/audit_chm.py`。

**来源码（Source Code）**:
条目的出版来源标识。官方用 5etools 码（XPHB=2024 PHB、PHB=2014 PHB、XGE/TCE/FTD…扩展书）；chm 表格来源码（PHB24/PHB14/夸力许/冰风谷…）构建期映射为 5etools 码；第三方（合作方万法大全 284 条）用自定义码（黯潮→ACT、胧忆岛→LOO、歪月→WYM…）。同英文名双版本法术中文名归一（2024 优先，旧名入别名）。

**富化字段（Enrichment）**:
spells.summary（一句话概要）与 entry_tags.spell_keyword（语义大类标签）合称富化。官方/第三方全 1220 条覆盖（v0.52.0，ADR-0027：全量重跑为 13 个效果域大类 + ≤30 字概要，`kb_patches/spell_enrich.json`）。

**条目（Entry）**:
知识库中的一条数据 = 名称 + 英文名 + 来源 + 版本 + 清洗后正文 + 机翻标记。同名不同版各为一行（`UNIQUE(kind, name, source)`）。
_Avoid_: 词条、卡片

**版本（edition / source）**:
同一条目在不同规则版本（2014/2024）中的来源标注，如 `PHB·2014`、`XPHB·2024`。同名多版本查询必须全部返回，不合并。

**机翻标记（Machine Flag）**:
与 5etools-cn 渲染规则一致：`translator` 缺失或非人工译者白名单（仅「不全书」）的条目视为机翻，返回时标注 ⚠️机翻，由 DM 甄别。
_Avoid_: 校对、翻译质量

**别名（Alias）**:
条目的可检索名称（中文名、英文名，全小写化）组成的索引，用于「别名精确 → 名称 LIKE → 逐字缩短」三级模糊搜索的首级命中。

**特性总表（Feature Table）**:
职业查询的输出形态（v0.48.0 起，ADR-0023）：按层级段（第1层 1-4级 /
第2层 5-10级 / 第3层 11-16级 / 第4层 17-20级）分组的概要层，每行
「N级 名称：一句话概要」（summary 字段），默认版本 = 群级开卡规则 /
最新版；玩家按需钻取单个特性（特性 名）或等级段全文。
_Avoid_: 一次性输出全部特性全文（改为按层级段分条发送）

**层级段（Tier Segment，v0.48.0）**:
职业特性的四档等级分段（1-4/5-10/11-16/17-20），L1 概要总表、L2 层级/
等级段钻取、L3 全文分条共用。段标题形如「第N层 X-Y级」（概要）或
「【职业·第N层 X-Y级】」（全文消息）。

**职业版本策略（Class Edition Policy，v0.48.0）**:
`/查职业` 默认展示的规则版本：群级开卡规则（`chargen_rule:{origin}`
的 edition 字段）→ 未设规则/私聊取该职业最新版；显式第二参数
（2014/2024）可覆盖。单特性细化（特性 名）保持跨版本。

**内置库（Builtin DB）**:
随插件 zip 分发的 `kb_data/dnd_kb.db`，永不被在线更新覆盖，是知识库的最终兜底；在线更新上线后 `data_dir/kb_update.db` 优先。

**结构化筛选（Filter）**:
按过滤字段精确查询：怪物（类型 + 挑战等级）、法术（环级 + 学派）、物品（稀有度）。中文枚举（龙类/3环/珍稀）经 kb_enums 映射为内部英文值。

**特性反查（Feature Lookup）**:
v0.13.0 起「按特性/属性筛条目」的查询形态（`/筛怪` `/筛法术` `/筛物品` 与 LLM `filter` 的标签参数），与「特性（trait）」严格区分——后者指怪物/职业的能力条目。特性反查的数据基础是构建期提取的 `entry_tags` 表，不依赖正文全文检索。
_Avoid_: 特性查询（与 trait 混淆）、属性筛选（与六属性混淆）

**特性标签（Tag / entry_tags）**:
构建期从源数据提取的「(facet, value)」对：facet 编码维度+关系（`dmg_dealt` 造成伤害 / `dmg_resist` 抗性 / `dmg_immune` 免疫 / `dmg_vuln` 易伤 / `condition_inflict` 施加状态 / `condition_immune` 状态免疫 / `environment` 环境 / `weapon_property` 武器属性 / `spell_component` 成分 / `spell_shape` 范围形状 / `spell_target` 目标），value 为归一化后的 canonical 中文（暗蚀、海岸、灵巧…）。怪物伤害类型来自动作区中文文本正则提取（`{@damage}` 标签只含骰子）；`{@condition}` 标签在清洗前提取。

**伤害类型词表（Damage Type Canon）**:
13 种标准伤害类型的 canonical 中文（强酸/钝击/寒冷/火焰/力场/闪电/暗蚀/穿刺/毒素/心灵/光耀/挥砍/雷鸣）与变体别名（黯蚀→暗蚀、冷冻→寒冷、精神→心灵、酸性→强酸…）。归一化在构建期完成，查询期只做别名→canonical 映射（kb_enums）。

**跨库广搜（Broad Search）**:
`/查询` 指令：一次搜索全部条目类别（法术/怪物/物品/专长/背景/职业）的名称，结果按类别分组展示；`-全文` 参数额外启用正文 LIKE 搜索。名称无命中时兜底按法术学派列出（`/查询 惑控`）。

**基础物品（Base Item）**:
v0.14.0 起：魔法物品的 `baseItem` 字段（如「长剑|PHB」）在构建期提取为 `entry_tags` 的 `base_item` facet（值=中文名）。「以长剑为基础的魔法武器」= 按 base_item=长剑 反查，`/查物品 长剑` 自动附此类列表，`/筛物品 长剑` 显式列出。

**物品大类（Item Type）**:
物品 `type` 单字母码（M/R/S/HA/MA/LA/A/SCF/INS/RD/WD/RG/P/SC…）经合并码表（基础与魔法物品共用，实测同码语义一致，如 S=盾牌）映射为 canonical 中文（武器/盾牌/重甲/中甲/轻甲/弹药/法器/乐器/权杖/魔杖/戒指/药水/卷轴…），存 `item_type` facet。法杖（staff）在源数据中无独立编码，不单列。

**物品稀有度（Rarity）**:
v0.15.0 起完整支持：items 表 `rarity` 列存英文原值（none/common/uncommon/rare/very rare/legendary/artifact/varies/unknown/unknown (magic)），中文↔英文双向映射在 kb_enums（RARITY_CN / RARITY_CN_REV / format_rarity）。反查：六档标准值精确匹配；「非魔法物品」= rarity='none'（基础物品）；「魔法物品」= filter 层哨兵 'magic' 展开为 `rarity IS NOT NULL AND rarity != 'none'`（1240 件）。显示：详情与列表统一中文，非魔法物品直接显示「非魔法物品」（不带「稀有度：」前缀）。

**状态（Condition）**:
v0.16.0 起：conditionsdiseases.json 的 `condition[]`（30 条）+ `status[]`（5 条，2024 浴血/专注）合并为 kind=condition（disease 不收），2014/2024 双版本并存（reprintedAs 不跳转——与物品不同，状态版本是规则并存）。纯文本 body（同 background），无侧表、无筛选维度。

**种族（Race）**:
v0.16.0 起：races.json 全收 160 条（同名多版本并存，reprintedAs 不跳转），schema v3 新增 races 侧表（speed_walk/climb/swim/fly/burrow + darkvision 数值列）。**速度只走结构化字段**：speed int=步行，dict 值 int 或 bool true（=等同步行速度）；正文里的临时飞行（阿斯莫/龙裔变身）不收录。生物类型：有 creatureTypes 按字段（源词归一化：妖精→精类/构装→构造体/亡灵→不死生物/怪兽→怪物，CREATURE_TYPE_CN_NORM），无字段默认类人生物（118 条）。反查六维度：速度（类型词+裸N尺=≥N/「N尺以下」=≤N）、体型（size 数组逐条）、生物类型、天生抗性（2024 结构化 resist/immune/vulnerable 含 choose 展开 + 2014 正文「对X伤害具有抗性/免疫/易伤」正则双通道，facet 复用 dmg_resist/dmg_immune/dmg_vuln）、黑暗视觉（裸词=有无/带数值=≥N）、天生施法（2024 additionalSpells innate+known + 2014 正文 {@spell} 标签双通道 → innate_spell facet，命令层未知 token 兜底查法术库）。

**职业定位（Class Role）**:
v0.33.0 起：13 个基础职业 + TCE 协力者按四大定位归类（武者：战士/野蛮人/武僧；奥法：法师/术士/魔契师；神职：牧师/圣武士/德鲁伊；专家：游侠/游荡者/吟游诗人/奇械师），存 `entry_tags` 的 `class_role` facet（值=武者/奥法/神职/专家），`/筛职业 武者` 裸词定位反查，`/查职业` 头部展示「定位：X」。UA 秘术师无定位。

**能力关键字（Keyword Tag / class_keyword / subclass_keyword）**:
v0.33.0 起：职业（29 条）与子职（186 条，排除 UA/Plane Shift 跨界）由 AI 生成一句话概要（`classes` 侧表 summary）与能力关键字（`entry_tags` 的 `class_keyword`/`subclass_keyword` facet）。职业词表 `CLASS_KEYWORD_TAGS` 六类：战斗方式/施法/辅助/技能倾向/属性依赖/特殊机制（canonical 与专长/法术词表对齐）；子职词表 `SUBCLASS_KEYWORD_TAGS` 三类：定位倾向/主题风味/特色机制（含八大魔法学派）。`/筛职业` `/筛子职` 裸词自动消歧（值集优先）、前缀词「定位/标签/关键字」显式指定、别名归一（潜行→隐匿、重武器→双手、塑能学派→塑能）。词表外自由词构建期告警。

### 私设

**私设 / 房规（Homebrew）**:
DM 自写的非官方条目（v0.36.0 起），放入 `{data_dir}/trpg_homebrew/*.json` 即被运行期增量加载；source 独立 = 纯新增，与官方 `(kind,name,source)` 相同 = 覆盖官方（房规修正），查询结果置顶并标注 🏠房规。
_Avoid_: 补丁（构建期 kb_patches 通道，与私设无关）

**私设助手（manage_homebrew，v0.37.0）**:
第 9 个 LLM 工具，私设相关 LLM 能力的总称，含转录/写入/点评三个 action。

**转录（convert）**:
把 DM 口述的纯文本私设转成合法私设 JSON。采用双程校验：LLM 生成 JSON 后回传工具，经权威解析回执「通过/逐条告警」后才算定稿。

**写入（write）**:
把转录产物直接落盘到私设目录并热重载生效。需插件配置 `homebrew_write_enabled` 开启且调用者为白名单/管理员（私聊不放行——私设是全局数据）；目标文件已存在时默认拒绝，DM 确认后可整文件替换（overwrite）或按唯一键合并（merge）。

**点评（review）**:
对私设草稿做平衡性点评。工具只解析草稿返回锚点与同名命中，对照材料由 LLM 自主查库获取，且必须先查库再点评（禁止仅凭记忆）。

**锚点（Anchor）**:
点评时从草稿解析出的定位信息（kind/name/source + 环级/稀有度/CR 等结构化字段），用于指引对照查询的方向。

### 构筑咨询

**构筑咨询（Build Advice / advise_build，v0.35.0）**:
第 8 个 LLM 工具：把玩家目标/角色卡现状确定性组装成候选档案（职业/种族/背景/子职/专长/法术/升级特性时间线），LLM 只负责基于档案组织话术。两个 action：`new_build`（从零构筑，含「根据背景推荐」——LLM 提炼主题词传入 goal）与 `level_up`（插件直读活跃卡，LLM 零传参）。幻觉控制只在输入侧：条目名全部来自知识库反查，docstring/守则明文禁止凭记忆补充。
_Avoid_: 自动出卡（构筑只是建议，落库仍走 guide_chargen）

**目标词消歧（Goal Disambiguation）**:
goal 自由文本 → 标签的三阶段：① 别名归一（normalize_term：各词表 resolver 顺序取首个命中，前排→坦克、回血→治疗）；② 查库确认（kb.value_facets：一词可命中多家族，如「坦克」同属职业/子职/种族词表）；③ 整词未命中做 CJK 复合词抽取（_extract_tag_terms：按别名长度从长到短子串匹配，「前排打手」→坦克）。库内不存在的词直接丢弃。
_Avoid_: 让 LLM 预映射标签（会产生无效标签）

**候选档案（BuildDossier）**:
advise_build 的返回结构（JSON 文本）：new_build = {edition, level, goal_tags, races≤5, classes≤5, subclasses(按职业≤3), backgrounds≤5, feats≤8, spells≤10, hint}；level_up = {card, class_features_timeline(等级+名称+一句话概要), feat_candidates(带 prereq_check 标注), spells(主职职业法术表), hint}。每维度带版本标注（source·edition），控制 token 体积。

**前置标注（Prereq Mark，标注不过滤）**:
level_up 对专长候选逐条标注前置条件满足情况：`✅力量13`（卡面属性达标）/`❌缺敏捷13`（差一点够到，可顺势建议属性规划）/`⚠️需特性「战斗风格」`（卡面未记录特性，人工核对）/`⚠️小型种族`（卡未记录体型）。只标注、不过滤，由 LLM 结合标注给建议。判定基于 `entry_tags` 的 prereq_ability/prereq_race/prereq_feat/prereq_feature facet（feat_prereq_facets 一次取全）。

**职业法术表（Spell Class List / spell_classes，v0.35.0）**:
schema v10 新侧表：法术→主职业（英文 5e.tools 生成的法术源查找表 `gendata-spell-source-lookup.json`，2024 数据模型起法术条目不再内嵌 classes 字段，改由站点生成文件提供）。构建：`scripts/fetch_en_spells.py` 下载 + build_kb `--en-spell-lookup`，按 (法术名小写, source) 匹配中文条目的 ENG_name；未命中写 `spell_classes_unmatched.json` 报告不阻塞（多数是无主职业表的附赠法术，合法排除）。只存主职业表，子职/领域附赠不做。三入口暴露：`/筛法术 职业 <职业名>`（前缀词，中英文职业名均可）、query_dnd_knowledge `spell_class` 参数、advise_build 内部（法术环上限按施法进度类型确定性计算：full 表/1/2 与 artificer=⌊(级+3)/4⌋/1/3=⌊(级+5)/6⌋/pact=min(⌈级/2⌉,5)）。

**车卡预填（Chargen Prefill，v0.35.0）**:
guide_chargen action=start 新增 race/class_name/background 可选参数（值必须来自 advise_build 档案，禁止凭记忆填）：逐项复用知识库校验（与推进步一致），合法写入草稿并跳过对应步骤（2014 全预填→属性步；2024 物种写入 species），非法项忽略回退正常询问并在 check 注明。链式预填不跳空——中间非法则停在对应步。

### 角色卡

**属性值（Ability Scores）**:
v0.17.0 起：角色的六维（力量/敏捷/体质/智力/感知/魅力），合法范围 1-30，修正值 = floor((值-10)/2)。六维一律称「属性值」。
_Avoid_: 属性筛选（已被知识库特性反查占用）

**角色卡（Character Sheet）**:
一个玩家在一个会话中的一张命名卡，含属性值、职业（兼职列表）、种族/物种、背景、阵营、熟练、战斗字段、装备槽位、生平。按 KV 一卡一 key 存储，同玩家可有多张，一张为活跃卡。

**语言（Languages）**:
v0.28.0 起的角色卡字段：一个角色可拥有多门语言（如「通用语、精灵语、地底通用语」），
自由文本、无词表校验（不做 DnD 语言合法性判定），仅清洗去重限条数。
命令 `/卡 设 语言` 整体覆盖，文本导入支持「语言：…」独立行，卡面摘要显示
「语言：…」行，`/卡 详情 语言` 查看全文。
_Avoid_: 语言熟练（语言只有会不会，没有熟练档位）

**活跃卡（Active Character）**:
玩家每会话一张的默认卡：`/r` 属性/技能/豁免联动与 `manage_character` 工具 name 缺省时的目标。新建首张卡自动活跃，删活跃卡回退到索引第一张。

**卡名索引（Character Index）**:
`character:index:` KV。因 AstrBot KV 无枚举能力，卡名列表必须显式维护，所有增删改在同一锁内同步，否则卡会成孤儿。

**开卡规则（Chargen Rule）**:
群级车卡约束（`chargen_rule:{origin}`，DM/管理员设置）：规则版本（2014|2024）、属性生成方式、子职时机（auto/on/off）、起始等级。全团统一遵守，校验**硬拒绝**——不合规拒绝保存，特许路径 = DM 改群规则。

**车卡草稿（Chargen Draft）**:
引导状态机的 KV 中间态（`character:draft:{origin}:{sender}`），记录进行到哪一步与已确认字段，跨重启可续传；落库后删除，重开覆盖。

**购点法（Point Buy）**:
参数化属性生成方式之一：点数池（默认 27）+ 属性区间（默认 8-15）+ 固定成本表（8=0 … 15=9）。别名 27buy/32buy 指向池 27/32。

**标准数组（Standard Array）**:
属性生成方式之一：预置 6 个值（15/14/13/12/10/8）各用一次自由分配。

**掷骰开卡（Rolled Scores）**:
属性生成方式之一：指定骰式（如 4d6kh3）掷 N 次（默认 6）得池子，由**插件代骰**（明细入草稿+投掷历史），玩家只能原样分配池子、禁止自报数字。别名 dnd5 = 4d6kh3×6。

**中文优势/劣势（CN Adv/Dis）**:
`d20优势` / `d20劣势` 紧贴后缀写法（等价引擎 adv/dis：掷 2 枚骰取大/取小；任意骰面适用，如 `1d4优势`=2d4kh1），v0.38.0 起在 main.py `_do_roll` 命令层映射（`_map_zh_adv_dis`），dice_parser 上游只读不加中文语法。**必须紧贴**：带空格（`d20 优势`）、前置（`优势d20`）、单独使用（`优势`）均报语法错误；标签内「优势」二字（如 `d20 战斗优势`）不受影响；玩家登记同名命名掷骰时按既有优先级（角色卡联动优先）。详见 ADR-0014。

**战斗字段双层（Layered Stat）**:
战斗字段（HP/AC/法术位/攻击加值）的 base+bonus 两层模型：base 由规则引擎自动算（v0.17 恒 0），bonus 是手动房规调整，显示=base+bonus。引擎重算 base 时 bonus 保留，房规特许永不与自动重算冲突。

**工具返回驱动（Tool-Reply-Driven）**:
车卡引导的机制：每次 llm_tool 调用返回【进度】【校验】【下一问】三段式文本，LLM 只做话术包装、一次只问一个问题、禁止替玩家作答；状态真相在插件草稿 KV，LLM 上下文丢失可续。

**规则引擎（chargen_engine，v0.18）**:
纯函数模块：输入 CharacterSheet（classes/种族/背景/装备槽）→ 重算四个战斗字段的 base 层。HP=首职首级满骰+其余期望值(⌊faces/2⌋+1)+体修×等级；法术位按 casterProgression 折算（full=级、1/2=⌊级/2⌋、1/3=⌊级/3⌋、artificer=⌈级/2⌉、pact 独立表）+ 兼职施法者合并查 full 表；AC=护甲（LA+敏/MA+min(敏,2)/HA 平值）+盾+无甲防御（野蛮人 10+敏+体、武僧 10+敏+感、龙族血脉 13+敏）取高；攻击=装备槽武器（灵巧取高/远程敏/其余力）+熟练、各施法职业「法术攻击」=施法属性修+熟练。只动 base 不动 bonus；攻击条目「生成集外整体保留」兜底手动条目。

**加值选择（Ability Bonus Step，v0.18）**:
引导状态机在 ABILITY_ASSIGN 后的新步（S_ABILITY_BONUS）：2014 种族/2024 背景含 `choose` 加值时进入（半精灵式 count：选 N 个各 +1；2024 背景 weighted：+2/+1 或 +1/+1/+1 二选一），插件列出方案、玩家答「力+2 敏+1」、硬校验（多重集恰匹配一个方案）后落库时自动叠加；纯固定加值（2014 种族平铺）无 choose 自动跳过。替代 v0.17 的 LLM 引导+DM 复核。

**知识库 schema v4 侧表（v0.18）**:
build_kb.py 新增 6 张规则引擎侧表：class_combat（生命骰/豁免/施法进度/施法属性）、subclass_caster（子职 1/3 施法进度，重复空行已过滤）、class_starting_equipment、background_ability/race_ability（ability payload，choose 结构原样 JSON）、item_combat（护甲/武器战斗字段，type/property 的 `|source` 后缀已切分）。法术位表按 casterProgression 硬编码（镜像 classTable 无法术位列）。XPHB 圣武士/游侠 caster=artificer（1 级即有施法，向上取整），2014 为 1/2。

**加值分层校验（Layered Validation）**:
v0.17 的校验口径：插件硬校验只管「加值前」的属性分配（购点花费/骰池多重集/标准数组多重集）；2014 种族/2024 背景的属性加值由 LLM 查知识库引导计算，确认步展示「分配+加值=最终」，最终值由 DM 复核。**v0.18 起升级为全自动**：加值选择步 + 落库自动叠加（clamp 1-30），本词条仅保留历史语义。

**起始金币（Starting Gold）**:
v0.20.0 起车卡落库时按群规则发放，单位=金币，三态：`自动`=按职业
goldAlternative（class_starting_equipment payload，如「{@dice 5d4 × 10|…|起始金币}」
→ 骰式 5d4 × 乘数 10 金币，仅 2014 职业有）插件代骰；纯数字=全团固定金额
（不代骰）；骰式=DM 自定义随机财富（复用 _split_gold_expr 拆乘数）。写入个人
背包「金币」条目（value 按面值 100 铜）。2024 职业无 goldAlternative——起始
财富内嵌在装备方案 defaultData 的 `{"value": N}`（铜币，A/B/C 可选），需玩家
选择，由 LLM 引导、不自动发放。查询/代骰失败静默跳过不阻断落库。
_Avoid_: 初始资金、开卡奖励

# ADR-0025：可定制职业选项入库（魔能祈唤/战技/超魔法/战斗风格）

- **状态**：已采纳
- **日期**：2026-08-14
- **版本**：v0.50.0

## 背景

v0.49.0 职业数据库重建后，用户提出：魔契师的魔能祈唤、战斗大师的可选战技等
「让职业高度可定制化的选项」无法查询。排查确认：

1. **5etools-cn 镜像缺 optionalfeatures.json**——这些选项在 5e.tools 数据模型
   里是独立类别（optionalfeature，featureType 枚举 EI/MV/MM/FS），但 5etools-cn
   仓库未包含该文件，标准通道无数据源；
2. **5e_chm 有素材但被跳过**——v0.49.0 职业提取管道（class_extract）把
   「魔能祈唤/战技选项/超魔法选项」列入 OPTION_LIST_STEMS 跳过（非职业/子职）；
3. **build_kb/kb.py/命令层无此 kind**——知识库六类（法术/怪物/物品/专长/
   背景/职业）之外没有第七类；
4. 附带发现：2014 战斗大师的战技嵌在 `#### 战技Maneuvers` 特性正文里
   （`*指挥官奇袭Commander's Strike。*` 斜体段），/查职业 只显示「战技（3级）」
   标题；2024 战斗风格专长（FS 类）在 5etools-cn feats.json 中为 0 条。

## 决策

1. **新增 optionalfeature 类别**（entries.kind='optionalfeature'，中文「选项」），
   数据源 = 5e_chm/md 人工校对中文，featureType 码沿用 5etools 官方枚举：
   EI（魔能祈唤）/ MV（战技）/ MM（超魔法）/ FS（战斗风格）。
2. **提取管道 `scripts/optional_extract/`**（规则解析，零 LLM）：
   - 源文件显式清单（10 个：祈唤 PHB2014/XGE/TCE/XPHB、战技 PHB2014 正文/
     TCE/XPHB、超魔法 PHB2014 ####/XPHB、战斗风格 XPHB）；
   - 三格式标题：裸标题（中文+空格+英文）、无空格（中文English，中文部分
     排除 `*` 防正文斜体引用误判）、`####` 标题；
   - 先决/消耗行：`*先决：…*`/`*先决条件：…*`/`*消耗：N术法点*`/
     `*战斗风格专长（先决：…）*`（2024 版无尾星号）→ prerequisite 字段；
   - 特例：2014 战斗大师从 `#### 战技Maneuvers` 正文剥离 `*中文English。正文`
     斜体战技；2014 术士取 `超魔法Metamagic` 特性后的 `####` 段；TCE 祈唤
     `*Legacy*` 尾注 → legacy 标记；
   - 输出 5etools 兼容 optionalfeatures.json（name/ENG_name/featureType/source/
     edition/prerequisite/entries/class/legacy），edition 由 source 显式指定。
3. **build_kb**：KIND_SOURCES 加 `optionalfeature: optionalfeatures.json`；
   `_kind_body` 渲染器加 `_optionalfeature_body`（先决行——「消耗」开头直接
   显示、其余「先决：」前缀——+ 正文）；entry_tags 双 facet：
   `feature_type`（类型中文，153 全覆盖）+ `prerequisite`（先决原文，消耗型
   不记，72 条）；`_filter_order`/`_fetch_filtered` 支持新 kind。
4. **查询层**：
   - 四个独立命令：`/查祈唤`（EI）/`/查战技`（MV）/`/查修法`（MM）/
     `/查风格`（FS），detail 按类型过滤，候选回退 search；卡片式展示
     （标题「名｜Eng」+ 类型 + 正文[含先决行] + 版本行，对齐法术卡片）；
   - `/筛选项`：类型反查（feature_type，支持简称祈唤→魔能祈唤）+ 先决反查
     （prerequisite LIKE 子串，filter tags 值含 % 时自动 LIKE）；
   - LLM 工具 query_dnd_knowledge：kind=选项 + opt_type/opt_prereq 条件参数
     （40→42 参数，schema 测试同步）。
5. **版本策略**：选项条目按 source 分版本（PHB/XGE/TCE→2014、XPHB→2024），
   detail 一次返回全部版本（与法术一致，同名多版本并列标注来源·版本）。

## 结果

- 153 条选项入库（EI 82 / MV 43 / MM 18 / FS 10）：2014 战技 16 条全集
  （正文剥离）、2024 战技 20 条、超魔法 2014 8 + 2024 10、祈唤全版本、
  2024 战斗风格 10 条（补 5etools-cn 缺失）。
- `/查祈唤 苦痛魔爆`：双版本卡片（先决行 + 正文 + 版本行）。
- `/筛选项 先决 第5级`：9 条命中；`/筛选项 类型 祈唤`：82 条。
- 全量 pytest 1265 passed（新增命令层 7 测试 + fixture 5 条样例）。

## 后果

- 知识库新增第七类（kind=optionalfeature），`/查询` 广搜覆盖（_KIND_ORDER 追加）。
- `kb_enums.KIND_CN`/`OPTIONAL_FEATURE_TYPE_CN`、`build_kb.OPTIONAL_FEATURE_TYPE_CN`
  需同步维护（新增 featureType 时双端更新）。
- optional_extract 产物进包前排除（pack_release EXCLUDE_PATHS）。
- 2014 战斗大师正文战技已剥离入库，但原「战技」特性标题仍保留在子职特性中
  （不冲突：一个是特性标题，一个是战技选项本体）。

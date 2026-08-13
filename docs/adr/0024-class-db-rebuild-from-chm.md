# ADR-0024：职业数据库从 5e_chm 全量重建（/查职业 数据源换源）

- **状态**：已采纳
- **日期**：2026-08-13
- **版本**：v0.49.0

## 背景

用户报障：`/查职业 战士 魔射手` 返回「没有这个子职」，而 `战士 勇士` 正常。
排查根因（v0.48.0 版本策略引入后）：

1. **魔射手仅 XGE（2014 版）**。群规则感知把默认版本解析成 2024 后，
   `class_features(subclass='魔射手', edition='2024')` 把 XGE 行全过滤 → 报未找到；
   而「勇士」2014/2024 都有所以正常。
2. **候选列表查询不带版本过滤、子职查询带**——两者不一致导致「候选可见但查不到」。
3. 更深的隐患：现职业数据源是 5etools-cn 的 class-*.json，仅 186 个子职，
   缺少 5e_chm 里大量人工校对中文的官方扩展书子职与第三方职业/子职
   （血族/铳士/拳斗士/血猎手/邪狱使等），且 5etools-cn 数据含
   2024 伪子职瑕疵（subclass 名=职业名，如「战士/战士」）。

## 决策

1. **职业/子职特性数据换源 5e_chm 人工校对中文**（对齐怪物 ADR-0021 与
   法术 ADR-0018 的既有路径）：`/查职业` 的 class_features 数据从
   5e_chm/md 全量重建；**规则引擎侧表不动**（class_combat 生命骰/豁免/
   施法进度、subclass_caster、起始装备、classes 富化概要沿用 5etools-cn
   结构化 JSON——车卡硬数据 5e_chm 是叙述文本，提取易错）。
2. **提取方式 = 规则解析为主 + LLM 子代理兜底**（grill 决策）：
   - parser 处理三格式族：2024/第三方显式等级标题（`1级：特性名`）、
     2014 `#### 特性名`（等级从正文首句推断）+ 裸标题
     （`回气Second Wind`/`心灵之刃 Psychic Blades`，需特性表确认或正文
     等级线索）、HTML 特性表（圣武士/牧师用 `<table>`）；
   - 特性表锚点 → 特性名→等级映射（多等级展开：属性值提升 4/6/8/12…），
     去括号别名（动作如潮（一次）→动作如潮）；
   - 噪音过滤：职业表/生命值/熟练项/装备/快速建卡/战斗风格选项等非特性块；
   - 少数格式混乱的第三方文件（胧忆岛落英结社/侵蚀行者，无标题段落+怪物
     统计块混排）走 LLM 子代理（flash/lite），契约 llm_class_schema.md。
3. **按名合并**（沿用怪物 finalize 惯例）：5e_chm 覆盖同
   (className, subclass, source, level, name) 行；5etools-cn 独有 source
   （EGW/FRHoF/FTD/UA/PSA 等）原样保留；第三方书全量新增。
4. **伪子职过滤**：5etools-cn 2024 数据的 `subclass.name == className`
   声明（如「战士/战士」，其 subclassFeature 实为 2024 本职特性）在
   finalize 时删除，避免污染子职列表。
5. **版本回退修复**（本 ADR 起因）：子职查询在显式/默认版本过滤后为空时，
   自动回退其他版本并标注 `subclass_edition_fallback`（魔射手 2024→2014），
   输出头部提示「（仅 2014 版，已自动切换）」；候选与查询一致性保持。
6. **提取管道可复现**：`scripts/class_extract/`（inventory → parser → emit →
   finalize → llm_fallback），与怪物管道同级，构建命令见 run_extract.py 帮助。

## 结果

- 库规模：职业 30 条（+5 第三方职业）、子职 285 个（原 186 → +99）、
  本职特性 828 行、子职特性 2586 行（2024 本职特性归位）。
- `战士` 子职候选 25 → 19（伪子职「战士」清除），魔射手[XGE] 候选可见且
  可查询（2024 默认自动回退 2014 并标注）。
- 2024 版 12 职业本职特性全部入库（此前 5etools-cn 的 XPHB 数据以
  classFeature 形态存在但被 build_kb 归并到子职，体验异常）。
- 全量 pytest 1258 passed 无回归。

## 后果

- `kb_data/dnd_kb.db` 需随发版重建（SCHEMA_VERSION 不变，数据量增大 ~30%）。
- 5etools-cn data/class 目录已被 merged 产物覆盖（已备份
  `backup_class_cn_20260813/`）；下次重建直接跑 run_extract.py 全流程。
- 职业查询的正文为 5e_chm 人工校对中文；第三方职业 source 为自定义码
  （VTM/SHO/BF/BH/DF/LOO 等），`/筛职业` 标签反查不受影响（富化概要沿用 cn）。
- 后续若 5e_chm 更新（git pull），重跑 run_extract + build_kb 即可。

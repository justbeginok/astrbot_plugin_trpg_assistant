# 构筑咨询：advise_build 工具 + 职业法术表（schema v10）

v0.35.0 让「跑团助手引导车卡」从「LLM 复述车卡流程」升级为「构筑咨询」：
玩家说「帮我车一个 15 级前排打手」「以上是我的背景，帮我想想适合什么构筑」
「我要升到 7 级了，看看我的卡推荐点什么」，助手通过知识库反向检索给出方案，
并尽可能减少 LLM 幻觉。本 ADR 记录 grill-with-docs 流程确认的 12 项决策
与实现取舍。

## 决策总览（与用户确认）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | 实现形态 | **新工具+补参数混合**：新建第 8 个 LLM 工具 `advise_build`（代码确定性组装候选档案），同时给 query_dnd_knowledge 补参数 | 只改守则引导=幻觉最高；只建大工具=场景受限 |
| 2 | 场景范围 | 三个场景一次全做，但 action 合并为 `new_build`/`level_up` 两个（「根据背景推荐」走 new_build，LLM 提炼主题词传入 goal） | 底层数据缺口共享，一次交付；from_backstory 代码做不了 NLP，属伪分层 |
| 3 | 幻觉控制 | **只做输入侧约束**：候选全部代码组装+AI 概要，docstring/守则明文「推荐必须来自工具返回，禁止凭记忆补条目名」 | 输出侧拦截依赖框架能力且不可靠 |
| 4 | level_up 读卡 | 工具内部用事件 origin+sender **直读活跃角色卡**（LLM 零传参） | 杜绝传参幻觉 |
| 5 | 专长前置 | **标注不过滤**：逐条 ✅/❌缺力量13/⚠️未校验 | 硬过滤会藏掉「差一点够到」的目标专长 |
| 6 | 特性时间线 | 等级+特性名+一句话概要（class_features.summary），全文由 LLM 二次调 query_dnd_knowledge | token 可控 |
| 7 | 目标解析 | goal 自由文本+代码消歧（别名归一+查库 facet 归属+CJK 复合词抽取），keywords 显式参数补充 | 把映射交给 LLM 会产生无效标签 |
| 8 | 版本过滤 | 读群规则 ChargenRule.edition；非空只取该版本 | 无双版并存干扰 |
| 9 | 车卡联动 | **深联动**：guide_chargen start 加 race/class_name/background 可选预填，走状态机既有校验（合法跳步/非法回退） | 构筑→车卡体验闭环；不做方案存 KV（新状态+过期问题） |
| 10 | query 补参 | 只加 class_features 等级过滤（`class_level`）+ `spell_class`，prereq_* 不暴露 | 前置校验在 advise_build 内部代码做，LLM 无需直查 |
| 11 | 职业法术表 | 本期补数据：英文 5e.tools 法术源查找表按 ENG_name+source 匹配，新侧表 `spell_classes`，主职业表 only（子职/领域附赠不做） | 法术推荐必须限定职业法术表才不胡说 |
| 12 | 法术表暴露 | 三入口全暴露：/筛法术「职业」前缀词 + query_dnd_knowledge `spell_class` 参数 + advise_build 内部 | 项目惯例：每 facet 都有命令+工具入口 |

## 关键设计取舍

### 数据源：5e.tools 已无 spell.classes
2024 数据模型起 5e.tools 不再在 spell 条目内嵌 `classes` 字段（站点/镜像实测
0 命中），职业法术表改由站点生成文件
`data/generated/gendata-spell-source-lookup.json` 提供（17 个数据源、936 条
法术名，含 XPHB）。故构建脚本改为接收**单文件查找表**（`--en-spell-lookup`），
并新增 `scripts/fetch_en_spells.py` 下载；按 (法术名小写, source) 匹配中文
条目的 ENG_name。未命中写入 `spell_classes_unmatched.json` 报告不阻塞构建
（实测 115 条未命中绝大多数是无主职业表的附赠法术，属合法排除）。

### 目标词消歧三阶段（resolve_goal_tags）
1. 别名归一（normalize_term：按各词表 resolver 顺序取首个命中，如 前排→坦克、
   回血→治疗）；
2. 查库确认（kb.value_facets：该词在哪些 facet 下真实存在，一词可属多家族）；
3. 整词未命中时 CJK 复合词抽取（_extract_tag_terms：按别名长度从长到短做
   子串匹配，「前排打手」→坦克）；库内不存在的词直接丢弃。

### 子职归属收敛
子职标签反查结果跨职业（同一标签命中多职业子职），按各职业的
`class_features.subclass_candidates`（权威子职名清单）收敛，只保留属于该职业
的子职（实测修复前「灵能武士」会出现在圣武士候选下）。

### 专长候选的三个确定性来源（level_up）
1. 卡面职业的 class_keyword 标签 → feat_keyword 反查（两词表 canonical 高度
   重叠）；
2. 卡面已满足前置的属性专长（如力量≥13 → prereq_ability=「力量 13」）；
3. 19 级后的传奇恩惠（feat_type=传奇恩惠；未达 19 级在池中排除）。
逐条带 prereq_check 标注（✅X/❌缺X/⚠️未校验），不过滤。

### 法术环上限
`_max_spell_level(caster, level)` 确定性硬表：full=表、1/2 与 artificer=
floor((level+3)/4)、1/3=floor((level+5)/6)、pact=min(ceil(level/2),5)。
避免按错误公式给半施法者推荐 4 环法术。

### 既存缺陷顺手修复
filter() 对 LEFT JOIN 类 kind（专长/背景/职业/子职）无标签时返回**全库条目**
（`+1 契约掌控者权杖` 这类物品混入背景筛选），本次兜底查询场景暴露，补
`e.kind = ?` 守卫修复；KbEntry 带出 entry_id 供前置标注用。

## Consequences

- **schema v10 全量重建**内置库（kb_data/dnd_kb.db），随包替换；旧 v9 库
  运行期自动回退内置库。
- **第 8 个 LLM 工具**：AstrBot v4.5+ 需在 WebUI 工具面板手动启用
  advise_build；参数 schema 由 docstring Args: 段生成，改参数必须同步
  docstring 与 tests/test_llm_tool_schema.py。
- **守则注入升级**：_llm_request_guard 追加构筑场景一句（条目名必须来自
  工具返回）。
- 数据构建需要英文源查找表（fetch_en_spells.py 下载到
  .cache/5etools-en/data/generated/），缺失时构建照常（跳过 spell_classes）。
- 全量测试 987 通过（新增 test_build_advisor.py 22 例、
  test_advise_build_tool.py 12 例等）。

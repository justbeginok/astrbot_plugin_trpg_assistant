# 私设 LLM 写入：插件自管文件 + 单工具三动作 + 私聊不放行

v0.37.0 在 v0.36.0 运行期私设 overlay 之上，让 DM 可以把纯文本私设交给助手：
助手转录为合法私设 JSON（convert）、经配置与权限双闸后直接落盘
`trpg_homebrew/`（write）、并对照已有条目点评平衡性（review）。本 ADR 记录
grill-with-docs 流程确认的 9 项决策与实现取舍。

## 决策总览（与用户确认）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| 1 | 工具形态 | **单工具 `manage_homebrew` + action 枚举**（convert/write/review），无命令入口 | 与 manage_inventory/manage_shop 同构；纯文本私设很长，天然走 LLM 对话，命令入口体验差 |
| 2 | 配置形态 | `homebrew_write_enabled`（bool，**默认关**） | `_conf_schema.json` 无 enum 先例；默认关保证升级用户行为不变 |
| 3 | convert 校验 | **双程校验**：LLM 生成 JSON → 回传 convert → 插件临时目录 HomebrewManager 试加载 → 回执修订 | 权威解析与 /kb reload 同一条链，零漂移；DM 拿到的 JSON 即可直接用 |
| 4 | write 冲突 | **默认拒绝 + 列出现有条目清单** → DM 确认后 `overwrite=true` 整换 / `merge=true` 按 (kind,name,source) 合并（新盖旧） | 「加一条私设」若静默整文件覆盖会丢旧条目；静默 merge 会让幻觉条目混入 |
| 5 | write 权限 | **群聊+私聊都需白名单/管理员**，不复用 `_check_destructive_permission` | 私设是插件级全局数据（data_dir），私聊写入同样影响所有群；既有破坏性权限的「私聊放行」语义只适用于会话级数据 |
| 6 | review 取料 | **LLM 自主调 query_dnd_knowledge 查库**，插件不预取对照列表 | 用户明确选择；对照面开放（同 facet/跨版本/风味参照），预取反而是限制 |
| 7 | review 防呆 | 工具返回**结构化锚点**（kind/name/source/环级/稀有度/CR + 同名命中）+ docstring/守则/返回尾句三重「先查库再点评」 | 假锚点比没锚点更危险，解析失败直接退回纯文本模式+强制查库句 |
| 8 | 生成格式 | **简化格式为主**（kind/name/source/body）+ 尽量填关键结构化字段 | 模板最简单、LLM 出错面最小；结构化字段保住 /筛X 与锚点能力 |
| 9 | 长输出 | convert 全文贴回 + 字符数统计；>1200 字符提示开写入或分条转换 | 群聊平台可能截断；不做文件发送（跨平台能力不一致） |

## 关键设计取舍

### 为什么插件自写文件，而不用 AstrBot 内置文件工具

AstrBot v4.23.0 起确有内置文件读写工具（`astrbot_file_read_tool` /
`astrbot_file_write_tool` / `astrbot_file_edit_tool` / `astrbot_grep_tool`，
`core/tools/computer_tools/fs.py`，v4.27.2 现状不变），但三条硬约束使其
**不能作为本插件的存储通道**：

1. `provider_settings.computer_use_runtime` 默认 `"none"`——内置文件工具
   **默认不挂载**，依赖它等于要求用户额外开启 computer-use；
2. 非 admin 会话的写白名单只含会话 workspace（`data/workspaces/{umo}`），
   `data/plugin_data/{plugin}/`（即 `StarTools.get_data_dir()`）**不在其内**，
   写 `trpg_homebrew/` 会被 PermissionError 拒绝，行为随用户角色变化；
3. 插件 `@filter.llm_tool` 处理器注入的首参是 `AstrMessageEvent`，拿不到
   内置工具 `call()` 所需的 `ContextWrapper[AstrAgentContext]`，手工构造
   属未文档化的脆弱路径。

插件与 AstrBot 同进程，`pathlib` 直写 `StarTools.get_data_dir()/trpg_homebrew/`
不受任何工具层白名单约束，确定性最强。落盘沿用项目先例（scripts/build_kb.py）
「同目录临时文件 + `os.replace`」原子写。

### 双解析路径的漂移控制

`homebrew_writer.py` 内有两条解析路径：`validate_homebrew_text`
（HomebrewManager 权威，含正文渲染/侧表提取）与 `flatten_raw_entries`
（仅键定位，供 merge/锚点/文件名派生）。约定：**「是否合法」只信前者**；
后者永不做合法性判断。merge 输出统一为「显式 kind/name/source 的简化数组」，
5etools 旧文件的 entries/trait 字段原样保留（简化格式 body 优先于 entries，
可再解析）——由 `test_write_merge_5etools_existing` 回归锁死。

### 并发与一致性

写动作的「检查冲突 → 写盘 → reload」整段在插件级 `_homebrew_write_lock`
（asyncio.Lock）内完成，防两个会话同时写同一文件产生交错；写后
`reload_homebrew()` 同步整体替换 overlay（GIL 保证查询读旧池或新池，
无半写状态）。HomebrewManager.load 是同步 CPU 操作且私设文件 KB 级，
不需要 asyncio.to_thread（与 /kb reload 命令 handler 一致）。

### source 撞官方的三层防护

私设与官方 `(kind,name,source)` 相同即覆盖官方——这是 v0.36.0 的特性，
但也是 LLM 误操作的最大风险面：① docstring 明文禁止冒用官方来源码；
② convert/write 校验时经 `official_key_set()` 撞键检测，返回中醒目标注
「将作为房规覆盖官方条目」；③ write 后 reload 结果含 overrides 计数回显。

## 边界

- 私设职业/子职仍不参与规则引擎联动（v0.36.0 既有边界），docstring 不承诺
  车卡联动。
- review 的点评质量取决于 LLM 是否遵守「先查库」守则；插件侧只做锚点与
  强制句，不做输出侧拦截（同 advise_build 的输入侧约束哲学）。
- AstrBot v4.5→v4.27.2 对 `@filter.llm_tool` + docstring Args 体系无破坏性
  变更；v4.26.0 起 WebUI 逐工具权限管理（插件工具默认 member 可用），
  新工具同样需在工具面板启用后才注入 LLM。

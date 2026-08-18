# ADR-0029：跑团日志用独立 SQLite 库（可变数据第二存储）

- **状态**：已采纳
- **日期**：2026-08-18
- **版本**：v0.54.0（规划）

## 背景

跑团记录（团/场次模型，见 CONTEXT.md「跑团记录」）需要逐条追加、按团/场次
查询、摘要前取「最近 N 条」的长期日志数据，与现有 KV 数据（先攻/背包/角色卡
等有界小列表）性质不同：

1. **追加高频**：每条消息（玩家 + 机器人回复）追加一次。KV 的
   read-modify-write 需整块反序列化 → 追加 → 回写，O(n)/条、O(n²)/整场；
2. **无枚举**：AstrBot KV 无枚举能力，团名列表需像角色卡那样手动维护索引；
3. **上限丢数据**：KV 列表设上限会丢团开头剧情，不设上限单 key 值失控；
4. **摘要需取最近 N 条**：只有 SQL 能 `LIMIT`，KV/文件都得整读再切。

## 决策

1. 跑团日志存 **`data_dir/trpg_log.db`**（`StarTools.get_data_dir()` 下，与
   `trpg_homebrew/` 并排），**不进插件包**（插件包整包替换会丢数据）。
2. 用 stdlib `sqlite3`（插件已在知识库用到，零新依赖），WAL 模式 + 单连接
   管理器锁。
3. 两表：`log_entries`（逐条消息，含 origin/campaign/session_seq/role/
   sender/text/is_roll 预标）+ `summaries`（场次小结）。
4. 打破「可变数据全走 KV」约定——本插件第二处可变存储（私设文件已先例）。

## 后果

- 部署备份流程须新增对 `trpg_log.db` 的备份（`VACUUM INTO`，与 data_v4.db
  同法）；升级插件不丢日志（在 data_dir 不在插件包）。
- 需在 tests/ 新增 SQLite 临时库夹具（现有 KV 替身不覆盖该路径）。
- 团名枚举、场次边界、导出能力由 SQL 白送，无需手动索引。
- ARCHITECTURE.md §3「KV 持久化全景」需补一条例外说明，指向本 ADR。

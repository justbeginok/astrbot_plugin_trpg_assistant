# 知识库作为只读 SQLite 文件，是「持久化只走 AstrBot KV」惯例的首次例外

DND 知识库（法术/怪物/物品/专长/背景/职业）以**只读 SQLite 文件**随插件打包
（`kb_data/dnd_kb.db`），由离线构建脚本 `scripts/build_kb.py` 从 5etools 中文站
JSON 编译而来；查询全部走 `sqlite3` 标准库，不写入 AstrBot KV、
不占用任何 `xxx:{origin}` 前缀。

## Considered Options

- **把条目存进 AstrBot KV（`kb:{origin}:...`）**：与既有惯例一致，但知识库是
  ~8000 条、只读、跨会话共享的数据——每会话复制一份既浪费又无法统一更新；
  且 KV 面向「读-改-写」小对象，没有结构化过滤能力（按 CR/类型/学派筛选），
  否决。
- **纯 JSON 文件 + 内存加载**：实现最简单，但每次查询要全量遍历 + 手写过滤，
  构建产物也可直接用 JSON；SQLite 换来的是索引（aliases 精确命中）、
  关联侧表（spells/monsters/items 过滤字段）与 `UNIQUE(kind,name,source)`
  约束，成本几乎为零（标准库），选用。
- **SQLite FTS5 全文索引**：模糊搜索更强，但 FTS5 分词对中文支持依赖编译选项
  （trigram），部署机 SQLite 版本不确定；且「火球木→火球」这类错别字
  trigram 同样无能为力。数据量万级，`LIKE` 全表扫 <100ms，用「别名精确 →
  LIKE → 逐字缩短」三级策略替代，否决 FTS5。

## Consequences

- 知识库是唯一不落 KV 的持久数据；为保持「一功能一模块 + Manager」惯例，
  仍采用 `KnowledgeBaseManager` + `format_*` 静态方法，dataclass 仅作
  内存结果模型（KbEntry/SearchHit/ClassFeatureRow）。
- 只读查询无并发写，不需要 `asyncio.Lock`；未来 `/kb update` 换库时
  Manager 以「换路径 + 重开连接」实现原子切换（`os.replace` + `.bak` 回滚）。
- DB 路径解析预留两档：`data_dir/kb_update.db`（在线更新产物）优先，
  内置库兜底——在线更新上线后无需改动查询层。
- 构建是发版流程的一部分：改数据 = 跑 `scripts/build_kb.py` + 发新版 zip。

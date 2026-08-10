# 知识库 schema v5：items 表加价值/重量列

v0.20.0 商店系统需要物品的**价值与重量**做定价/入包同步。此前 items 表
只有 rarity/attunement 两列，物品正文 body 也未固化价格/重量。

## Considered Options

- **正文 body 固化「价值/重量」行（仿稀有度先例）**：构建期把价格写进
  body，查询直接显示。否决——价格/重量是商店的**结构化输入**（要精确取整、
  做运算、按价格排序去重），埋在正文里只能靠正则抠；且改显示需重建库。
  最终选择：**结构化列 + 查询期格式化**（`format_cp` 换算币制显示），
  body 不动。
- **独立侧表 item_stats**：价值/重量放新侧表。否决——与 rarity 同属
  items 主表的「固有属性」，直接加列免 JOIN、与既有 filter/查询模式一致。
- **只存金币/只存磅整数**：价值单位存金币浮点、重量存整数。
  否决——源数据 value 是铜币整数（蜡烛 1cp），转金币浮点引入精度问题
  且丢失铜币级物价；重量可为小数（1/10 磅）。
  最终选择：`value_cp INTEGER`（铜币）+ `weight_lb REAL`（磅），
  源数据缺失存 NULL（商店层「无价不可初始化上架」）。

## 提取与版本规则

- **提取函数 `_item_value_weight`**：value 容忍字符串数字、非负；
  weight 容忍小数、非负；非法/缺失 → None。
- **`_copy` 浅合并天然继承基条目价值**（如 _copy 魔法物品继承基础物品
  的 value 会错误定价？——不会：继承发生在源数据层，魔法物品 rarity≠none
  不进入初始商店候选；商店只对在架条目取价）。
- **reprintedAs 跳转语义不变**：PHB 旧版被跳转到 XPHB 新版，天然保证
  「同名取 2024 价」；list_init_shop_items 的 XPHB 优先排序只是兜底
  双保险。
- **schema 版本机制照旧**：SCHEMA_VERSION="5"、kb.py KB_SCHEMA_VERSION=5；
  resolve_db_path 对旧库自动回退内置库，仅商店初始化/库价查询提示需
  v5 库（item_price/item_stats_lines/list_init_shop_items 对旧库缺列
  降级为 None/空，不崩溃）。

## Consequences

- **需要重跑 build_kb.py 全量重建内置库**并随包替换 dnd_kb.db（部署
  说明注明）；旧 v4 库不崩，仅商店功能受限。
- 实际数据：PHB/XPHB 非魔法有价物品 246 种（去重后）进入初始商店候选；
  PHB 无价物品基本为 0（全有价），无价多出现在魔法/杂物类。
- `/查物品` 详情现在额外显示「价值：X金Y银Z铜｜重量：N 磅」行
  （查询期格式化，不冻结进 body，改显示无需重建库）。

# 商店：批量交易 + 整店清空 + LLM 工具开放管理动作

v0.39.0 商店扩展：一次处理多件商品的批量买/卖/上架/下架、管理员
`/商店 清空`，以及 `manage_shop` 工具开放管理动作。本 ADR 记录三处
有真实取舍的决策，并修订 ADR-0010 的「管理操作不暴露给 LLM」一行。

## Considered Options

- **批量买/卖语法**：
  - 严格成对（`买 长剑 1 匕首 2`）：零歧义，但买单件也要写数量。
  - **数量可省略的贪心解析**（采纳）：非数字 token = 新物品名（数量 1），
    数字 token = 前一物品的数量。`买 长剑`=x1、`买 长剑 2`=x2、
    `买 长剑 匕首 2`=x1+x2，完全向后兼容既有单件写法。代价：纯数字
    token 不能作为物品名（批量语法下报「数量前缺少物品名称」，旧
    `_parse_name_qty` 单件仍可买）——D&D 物品名无纯数字，接受。
- **批量上架属性归属**：非「价=/库存=」前缀 token = 下一个物品名，
  属性 token 归当前物品（`上架 长剑 价=2金 匕首 库存=3`）。与单件
  `_parse_shop_add` 属性语法一致，规则统一；属性先于任何名称报错。
- **批量失败语义**：
  - 全有或全无（整批回滚）：需跨多件大事务，buy/sell 内部已是
    Shop→Inventory 双锁事务，回滚要反向补偿（扣回的货/货币），复杂度高。
  - **逐件原子**（采纳）：每件复用 `manager.buy/sell/add_entry/remove_entry`
    单件事务（锁序 Shop→Inventory 不变），失败件列明原因、其余继续，
    结果逐行明细 + 「成功 N 件 / 失败 M 件」汇总。
- **LLM 工具开放管理动作**：ADR-0010 原约定「manage_shop 只开放
  list/buy/sell，管理操作不暴露给 LLM」。v0.39.0 需求明确要求上架/下架
  也要走 LLM 工具端 → **开放 add/remove/clear**，但每个管理 action 在
  工具内先做 `_check_destructive_permission`（群聊白名单/管理员、私聊
  放行），非管理员拒绝并引导找 DM。设价/设库存/初始化/回购率仍仅命令端。
- **LLM 批量参数形态**：
  - 逗号分隔紧凑串（`items="长剑x2,匕首x3"`，沿用知识库 keywords 惯例）：
    LLM 拼串易错，且上架批量要带价/库存时表达力不足。
  - **items(array)**（采纳）：元素 `{"item": 名称, "qty": 数量}`，
    add 可含 `price`（"2金" 或铜币整数）与 `stock`（数字或 "无限"）；
    array 类型在 AstrBot schema 白名单内。旧 `item`/`qty` 参数保留，
    items 缺省时回退单件（向后兼容）；防御部分模型把数组序列化成
    JSON 字符串（`json.loads` 兜底）。
- **清空语义**：只清商品条目、**保留回购系数**（与 `init_from_kb`
  一致：清商品不清配置）；整体重置回购率由 `/商店 回购率 1.0` 单独做。
  空店返回「本来就是空的」；权限同其他管理操作。

## Consequences

- **批量 = N 个独立小事务**：每件独立锁往返（N 件 N 次），不包跨件大锁；
  锁序 Shop→Inventory 不变（ADR-0009/0010）。部分失败不产生回滚需求，
  玩家按结果明细补齐即可。
- **命令层新增解析器**：`_parse_batch_name_qty`（买/卖共用，数量可省略
  贪心）+ `_parse_batch_shop_add`（上架逐项属性）；`_parse_name_qty` 仍
  供 /bag rm/put/take/give 共用，未改动。单件路径保留原详细文案（含
  display_prefix 引导），仅 `len>1` 才走批量汇总——既有单件消息零回归。
- **LLM 工具 schema 变更**：`manage_shop` 新增 `items(array)` 参数
  （docstring 与签名同步，`test_llm_tool_schema.py` 强制校验）；action
  枚举扩为 list/buy/sell/add/remove/clear。旧调用（action+item+qty）不受
  影响。工具内管理动作的权限判定与命令层共用同一函数，口径一致。
- **边界（已知接受）**：纯数字物品名在批量语法下不可买（首 token 数字
  报错）；`价=`/`库存=` 前缀不能作物品名（与单件上架同限制）；LLM 传
  畸形 items（字符串/非对象元素/缺 item 字段）由 `_normalize_tool_items`
  逐项校验并返回明确错误；批量中同名物品出现两次（如上架）第二件报
  「已在架」，买同物两行分别扣减。
- **文档同步**：CONTEXT.md 新增「批量交易」「清空」词条并修订「商店」
  词条；ADR-0010 权限行已标注由本 ADR 修订。

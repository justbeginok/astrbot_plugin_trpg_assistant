# 背包批量发放/收回 + manage_inventory items 批量参数

v0.42.0 背包扩展：DM 便捷发放/撤回团队战利品。新增 `/发放`（grant）与
`/收回`（revoke）短命令直接操作队伍背包、`/bag add/rm/put/take` 全量批量，
以及 `manage_inventory` 工具新增 `items(array)` 批量参数。本 ADR 记录三处
有真实取舍的决策，并将 ADR-0015 的「逐件原子批量 + items(array)」模式
扩展到背包域。

## Considered Options

- **命令形态**：
  - 只增强 `/bag party add/rm`：改动最小，但「发放战利品」是 DM 高频
    场景，`/bag party add` 词法绕且不直观。
  - **新增短命令**（采纳）：`/发放`（主名 `grant`）/`/收回`（主名
    `revoke`），整个 arg 即物品列表（无子命令），直接写队伍背包；
    `/bag party` 保留不废弃。`give` 顶层命令名刻意避开（保留给现有
    个人间转移语义）。
- **鉴权对称性**：命令侧「发放全员放行、收回需权限」。
  - 工具侧维持无鉴权（状态）：LLM 可绕过收回鉴权直接从队伍背包删物品，
    与命令侧 revoke 不对称，形成安全漏洞。
  - **工具侧 `remove` + `to_party=True` 引入 `_check_destructive_permission`**
    （采纳）：与命令侧 revoke 同口径（私聊放行、群聊白名单/管理员）。
    put/take/add(to_party) 保持开放。这是对 ADR-0015「工具内鉴权」模式
    的背包域延伸，也是 v0.42.0 唯一的安全语义收紧。
- **批量属性语法**：对齐 `/商店 上架` 的逐项属性归属模式。
  - `重=X`（非负 float）、`价=X`（`parse_money`，铜币，支持「2金5银」）、
    `备注=X`（str）归属当前物品；同时兼容 `w=/v=/note=` 英文短键
    （与 `/bag add` 单件一致）。纯数字 token = 前一物品数量（缺省 1）；
    同一物品连续两个数字报「重复」。
- **LLM 批量参数形态**：沿用 ADR-0015 的 `items(array)`（不引入新 action）。
  - `_normalize_tool_items` 拆出公共 base（None/str→json.loads/list 校验
    item|name、qty 1~99999），shop 版继续走原函数（零回归），新增
    `_normalize_tool_inventory_items` 在 base 上追加 weight/value/note。
  - action=add/remove/put/take 全量支持 items 批量（与命令侧对齐）；
    put/take 元素仅用 item 与 qty（流转携带源条目属性）。

## Consequences

- **`/发放 治疗药水 3 价=5银 火球术卷轴 1`** 一条命令完成战利品发放；
  `/收回 火球术卷轴 1` 撤回发错（需管理员/白名单，私聊一律拒绝——队伍
  背包按群隔离）。
- **`/bag add/rm/put/take` 批量**：单件回落原解析器（`_parse_add_tokens`/
  `_parse_name_qty`）保零回归（如 `/bag add 长剑` 仍报数量必填）；批量
  走「逐件原子、失败列明、成功 N 件/失败 M 件」汇总，同 ADR-0015。
- **LLM 工具 schema 变更**：`manage_inventory` 新增 `items(array)` 参数
  （docstring 与签名同步，`test_llm_tool_schema.py` 强制校验 8 参数）；
  旧调用（action+item+qty）不受影响。
- **边界（已知接受）**：纯数字 token 不能作物品名（批量语法下报「数量前
  缺少物品名称」）；`价=` 前缀不能作物品名；revoke 不校验物品存在与否
  （不存在时逐件报「队伍背包里没有」）；工具侧 remove(to_party=True)
  非管理员被拒，LLM 需引导用户找 DM。
- **文档同步**：CONTEXT.md 新增「发放/收回」「批量」相关词条；
  ARCHITECTURE.md §7 命令表补 grant/revoke 与批量说明；ADR-0015 的
  批量模式在本 ADR 中扩展到背包域。

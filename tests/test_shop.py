"""商店（/商店）命令级 + 管理器级集成测试。

覆盖点：
  - /商店 初始化：从知识库生成 PHB/XPHB 非魔法物品（同名去重取 2024/XPHB 价）。
  - 购买：成交入包（带库重/价值）、自动找零、钱不够原子回滚、库存扣减/售罄。
  - 卖回：只收在架商品、回购系数计价、计数库存增加、无限库存不变。
  - DM 权限：非管理员配置子命令被拒。
  - 设价覆盖 / 回购率 clamp / manage_shop llm_tool 三动作。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
# 测试构建时隔离真实补丁目录（kb_patches/），避免真实补丁污染 fixture 计数。
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


class FakeEvent:
    """假消息事件（对齐 test_kb_commands 的替身形态，含 admin/private）。"""

    def __init__(
        self,
        message_str: str,
        origin: str = "group:1",
        sender_id: str = "u1",
        sender_name: str = "Alice",
        private: bool = False,
        admin: bool = False,
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._private = private
        self._admin = admin
        self.stopped = False

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_private_chat(self) -> bool:
        return self._private

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    """子类化插件：真实 __init__ + 内存 KV + 注入 fixture 知识库。"""

    def __init__(self, db_path: Path, config: dict | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}
        self._kb_manager = KnowledgeBaseManager(db_path)

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def ev(
    message_str: str,
    origin: str = "group:1",
    sender_id: str = "u1",
    private: bool = False,
    admin: bool = False,
) -> FakeEvent:
    return FakeEvent(
        message_str, origin, sender_id=sender_id, private=private, admin=admin
    )


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def make_plugin(tmp_path: Path) -> _MemoryPlugin:
    db = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, db, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return _MemoryPlugin(db)


def shop(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.shop_cmd(event)))


def bag(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.bag_cmd(event)))


def add_coins(plugin: _MemoryPlugin, coin: str, qty: int) -> None:
    bag(plugin, ev(f"/bag add {coin} {qty}"))


def init_shop(plugin: _MemoryPlugin) -> list[str]:
    return shop(plugin, ev("/商店 初始化", admin=True))


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------


def test_init_from_kb_dedup_and_xphb_price(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = init_shop(p)
    # fixture 有价非魔法 PHB/XPHB 基础物品 = 长剑(XPHB)/手弩/炽火胶/皮甲/盾牌 = 5
    # （长剑 PHB 被 reprintedAs 跳转到 XPHB，不重复上架）
    assert "上架 5 种" in out[0]

    out = shop(p, ev("/商店"))
    text = out[0]
    assert "共 5 种商品" in text
    assert "长剑" in text and "手弩" in text
    assert "盾牌" in text
    # 库价直接显示具体金额（15金=1500 铜、25金=2500 铜），不再显示「库价」占位
    assert "长剑 — 15金" in text
    assert "手弩 — 25金" in text
    assert "皮甲 — 10金" in text


def test_init_requires_admin(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(p, ev("/商店 初始化"))
    assert "你没有权限配置商店" in out[0]
    out = shop(p, ev("/商店"))
    assert "还没有商品" in out[0]  # 未初始化


def test_init_keeps_buyback_rate(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    shop(p, ev("/商店 回购率 0.5", admin=True))
    init_shop(p)
    out = shop(p, ev("/商店"))
    # 初始化后回购系数保留
    from astrbot_plugin_trpg_assistant.shop import ShopManager

    s = run(p.shop_manager.get("group:1"))
    assert s.buyback_rate == 0.5


# ---------------------------------------------------------------------------
# 购买
# ---------------------------------------------------------------------------


def test_buy_success_fills_weight_value(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 20)  # 2000 铜
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "🛒 已购买 长剑 ×1" in out[0]
    assert "花费 15金" in out[0]

    out = bag(p, ev("/bag"))
    assert "金币 ×5" in out[0]  # 2000 - 1500 = 500 铜 = 5 金
    # 入包带库重/库价（价值字段=成交单价 1500 铜 → 15金/件）
    assert "长剑 ×1" in out[0]
    assert "💰15金/件" in out[0]
    assert "⚖️3/件" in out[0]


def test_buy_money_shortfall_atomic(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 10)  # 1000 铜 < 1500
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "钱不够" in out[0]
    assert "还差 5金" in out[0]
    # 原子回滚：背包无长剑、金币未动
    text = bag(p, ev("/bag"))[0]
    assert "金币 ×10" in text
    assert "长剑" not in text


def test_buy_stock_decrement_and_sold_out(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    shop(p, ev("/商店 设库存 长剑 1", admin=True))
    add_coins(p, "金币", 30)
    out = shop(p, ev("/商店 买 长剑 2"))
    assert "库存不足" in out[0]
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "已购买" in out[0]
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "库存不足" in out[0]


def test_buy_auto_change_making(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    shop(p, ev("/商店 设价 皮甲 50", admin=True))  # 50 铜
    add_coins(p, "金币", 1)  # 100 铜
    out = shop(p, ev("/商店 买 皮甲 1"))
    assert "花费 5银" in out[0]
    text = bag(p, ev("/bag"))[0]
    assert "银币 ×5" in text  # 1 金被破开，找回 5 银
    assert "金币" not in text  # 金币条目已耗尽删除
    assert "皮甲 ×1" in text
    assert "💰5银/件" in text


def test_buy_price_override(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    shop(p, ev("/商店 设价 长剑 2金", admin=True))
    add_coins(p, "金币", 3)
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "花费 2金" in out[0]
    # 设价显示为具体币制
    out = shop(p, ev("/商店"))
    assert "长剑 — 2金" in out[0]


def test_buy_no_price_item(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    # 上架一个知识库无价的物品（DM 不设价）
    shop(p, ev("/商店 上架 火球法杖", admin=True))
    add_coins(p, "金币", 5)
    out = shop(p, ev("/商店 买 火球法杖 1"))
    assert "没有定价" in out[0]
    # 列表中该商品标「未定价」
    out = shop(p, ev("/商店"))
    assert "火球法杖 — 未定价" in out[0]


def test_buy_not_found(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    out = shop(p, ev("/商店 买 不存在的东西 1"))
    assert "商店里没有" in out[0]


# ---------------------------------------------------------------------------
# 卖回
# ---------------------------------------------------------------------------


def test_sell_buyback_rate(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    shop(p, ev("/商店 回购率 0.5", admin=True))
    bag(p, ev("/bag add 皮甲 2 w=10 v=1000"))
    out = shop(p, ev("/商店 卖 皮甲 1"))
    assert "已卖出 皮甲 ×1" in out[0]
    assert "获得 5金" in out[0]  # 1000 × 0.5 = 500 铜
    text = bag(p, ev("/bag"))[0]
    assert "皮甲 ×1" in text  # 还剩 1 件
    assert "金币 ×5" in text


def test_sell_only_on_shelf(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    bag(p, ev("/bag add 魔法扫帚 1"))
    out = shop(p, ev("/商店 卖 魔法扫帚 1"))
    assert "商店不收" in out[0]
    # 物品未扣
    assert "魔法扫帚 ×1" in bag(p, ev("/bag"))[0]


def test_sell_insufficient_items(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    bag(p, ev("/bag add 皮甲 1"))
    out = shop(p, ev("/商店 卖 皮甲 5"))
    assert "背包里没有足够" in out[0]


def test_sell_restocks_counted_stock(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    shop(p, ev("/商店 设库存 长剑 1", admin=True))
    add_coins(p, "金币", 30)
    shop(p, ev("/商店 买 长剑 1"))
    # 商店库存 0；卖回 1 件 → 库存恢复 1
    out = shop(p, ev("/商店 卖 长剑 1"))
    assert "商店余 1 件" in out[0]


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_add_remove_entry(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(p, ev("/商店 上架 神秘符咒 价=3金 库存=2", admin=True))
    assert "已上架 神秘符咒" in out[0]
    out = shop(p, ev("/商店"))
    assert "神秘符咒 — 3金（余 2）" in out[0]
    out = shop(p, ev("/商店 下架 神秘符咒", admin=True))
    assert "已下架 神秘符咒" in out[0]
    assert "神秘符咒" not in shop(p, ev("/商店"))[0]


def test_rate_clamp_and_reject(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(p, ev("/商店 回购率 3", admin=True))
    assert "需在 0 到 2 之间" in out[0]
    out = shop(p, ev("/商店 回购率 0.5", admin=True))
    assert "回购系数已设为 0.5" in out[0]
    # 管理器层直接 clamp
    clamped = run(p.shop_manager.set_rate("group:1", 9.9))
    assert clamped == 2.0


def test_stock_unlimited_unchanged(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 40)
    shop(p, ev("/商店 买 长剑 2"))
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "余" not in out[0]  # 无限库存不显示余量


# ---------------------------------------------------------------------------
# 分页翻页
# ---------------------------------------------------------------------------


def _make_big_shop(p: _MemoryPlugin, count: int = 35) -> None:
    """循环上架 count 种商品（直接走管理器，跳过权限）。"""
    for i in range(count):
        run(p.shop_manager.add_entry("group:1", f"商品{i:03d}", price_cp=100 + i))


def test_shop_pagination_commands(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    _make_big_shop(p, 35)
    # 第 1 页：前 30 条 + 翻页提示
    out = shop(p, ev("/商店"))
    assert "共 35 种商品，第 1/2 页" in out[0]
    assert "1. 商品000" in out[0]
    assert "30. 商品029" in out[0]
    assert "商品030" not in out[0]  # 第 31 条在下一页
    assert "/商店 <页码> 翻页" in out[0]
    # 纯数字翻页
    out = shop(p, ev("/商店 2"))
    assert "第 2/2 页" in out[0]
    assert "31. 商品030" in out[0]
    assert "35. 商品034" in out[0]
    # 页 子命令
    out = shop(p, ev("/商店 页 2"))
    assert "第 2/2 页" in out[0]
    # 越界夹取：超大页码 → 最后页；0 → 第 1 页
    out = shop(p, ev("/商店 99"))
    assert "第 2/2 页" in out[0]
    out = shop(p, ev("/商店 0"))
    assert "第 1/2 页" in out[0]
    # 页 缺页码 → 用法提示
    out = shop(p, ev("/商店 页"))
    assert "用法" in out[0]


def test_shop_pagination_format_unit() -> None:
    from astrbot_plugin_trpg_assistant.shop import Shop, ShopEntry, ShopManager

    big = Shop(entries=[ShopEntry(name=f"物品{i:03d}", price_cp=i) for i in range(35)])
    page1 = ShopManager.format_shop(big, page=1)
    assert "第 1/2 页" in page1
    assert "1. 物品000" in page1
    page2 = ShopManager.format_shop(big, page=2)
    assert "31. 物品030" in page2
    assert "物品029" not in page2
    # 越界夹取
    assert "第 2/2 页" in ShopManager.format_shop(big, page=999)
    # 不足一页不显示翻页提示
    small = Shop(entries=[ShopEntry(name="x", price_cp=1)])
    assert "翻页" not in ShopManager.format_shop(small)
    assert "第 1/1 页" in ShopManager.format_shop(small)


def test_shop_list_price_resolution() -> None:
    """列表价格：覆盖价优先；库价经 resolver 显示具体金额；无价标「未定价」。"""
    from astrbot_plugin_trpg_assistant.shop import Shop, ShopEntry, ShopManager

    s = Shop(
        entries=[
            ShopEntry(name="覆盖品", price_cp=50),  # 覆盖价 5银
            ShopEntry(name="库价品", price_cp=None),  # resolver 返回 1500 → 15金
            ShopEntry(name="无价品", price_cp=None),  # resolver 返回 None → 未定价
        ]
    )
    resolver = {"库价品": 1500, "无价品": None}.get
    text = ShopManager.format_shop(s, price_resolver=resolver)
    assert "覆盖品 — 5银" in text
    assert "库价品 — 15金" in text
    assert "无价品 — 未定价" in text
    # 无 resolver（纯格式化场景）→ 保留「库价」占位
    text2 = ShopManager.format_shop(s)
    assert "库价品 — 库价" in text2


# ---------------------------------------------------------------------------
# manage_shop llm_tool
# ---------------------------------------------------------------------------


def test_tool_list_buy_sell(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    out = run(p.manage_shop_tool(ev(""), action="list"))
    assert "共 5 种商品" in out
    add_coins(p, "金币", 20)
    out = run(p.manage_shop_tool(ev(""), action="buy", item="长剑", qty=1))
    assert "已购买 长剑 ×1" in out
    assert "花费 15金" in out
    out = run(p.manage_shop_tool(ev(""), action="sell", item="长剑", qty=1))
    assert "已卖出 长剑 ×1" in out
    assert "获得 15金" in out


def test_tool_list_pagination(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    _make_big_shop(p, 35)
    # 默认第 1 页 + 多页时附 LLM 导向的翻页提示
    out = run(p.manage_shop_tool(ev(""), action="list"))
    assert "第 1/2 页" in out
    assert "1. 商品000" in out
    assert "page 参数" in out
    # page 参数翻页
    out = run(p.manage_shop_tool(ev(""), action="list", page=2))
    assert "第 2/2 页" in out
    assert "31. 商品030" in out
    # 越界夹取
    out = run(p.manage_shop_tool(ev(""), action="list", page=99))
    assert "第 2/2 页" in out


def test_tool_unknown_action(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = run(p.manage_shop_tool(ev(""), action="init"))
    assert "未知的 action" in out


# ---------------------------------------------------------------------------
# 清空商店（/商店 清空）
# ---------------------------------------------------------------------------


def test_shop_clear_removes_all_keeps_rate(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    shop(p, ev("/商店 回购率 0.5", admin=True))
    out = shop(p, ev("/商店 清空", admin=True))
    assert "已清空商店，共移除 5 种商品" in out[0]
    # 列表空 + 回购系数保留
    out = shop(p, ev("/商店"))
    assert "还没有商品" in out[0]
    from astrbot_plugin_trpg_assistant.shop import ShopManager

    s = run(p.shop_manager.get("group:1"))
    assert s.buyback_rate == 0.5
    # 清空后买不到任何东西
    add_coins(p, "金币", 10)
    out = shop(p, ev("/商店 买 长剑 1"))
    assert "商店里没有" in out[0]


def test_shop_clear_empty_shop(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(p, ev("/商店 清空", admin=True))
    assert "商店本来就是空的" in out[0]


def test_shop_clear_requires_admin(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    out = shop(p, ev("/商店 清空"))  # 群聊非管理员
    assert "你没有权限配置商店" in out[0]
    # 商品仍在
    assert "共 5 种商品" in shop(p, ev("/商店"))[0]
    # 私聊放行
    out = shop(p, ev("/商店 清空", private=True))
    assert "已清空商店" in out[0]


# ---------------------------------------------------------------------------
# 批量购买/卖回（数量可省略的贪心解析）
# ---------------------------------------------------------------------------


def test_shop_batch_buy_success(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 40)
    out = shop(p, ev("/商店 买 长剑 皮甲 2"))
    assert "批量购买：成功 2 件" in out[0]
    assert "✅ 已购买 长剑 ×1，花费 15金" in out[0]
    assert "✅ 已购买 皮甲 ×2，花费 20金" in out[0]


def test_shop_batch_buy_partial_failure(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 20)  # 长剑 15金 + 皮甲×2 20金 不够
    out = shop(p, ev("/商店 买 长剑 皮甲 2"))
    assert "批量购买：成功 1 件，失败 1 件" in out[0]
    assert "✅ 已购买 长剑 ×1" in out[0]
    assert "❌ 钱不够" in out[0]
    assert "还差 15金" in out[0]


def test_shop_batch_buy_qty_omittable_backward_compat(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 50)
    # 单件不带数量 → ×1
    out = shop(p, ev("/商店 买 长剑"))
    assert "已购买 长剑 ×1" in out[0]
    # 单件带数量 → ×2（历史行为保持）
    out = shop(p, ev("/商店 买 皮甲 2"))
    assert "已购买 皮甲 ×2" in out[0]
    # 数字出现在名称位 → 报错
    out = shop(p, ev("/商店 买 2 长剑"))
    assert "数量「2」前缺少物品名称" in out[0]


def test_shop_batch_sell_success_and_partial(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    bag(p, ev("/bag add 皮甲 2"))
    bag(p, ev("/bag add 盾牌 1"))
    out = shop(p, ev("/商店 卖 皮甲 盾牌"))
    assert "批量卖出：成功 2 件" in out[0]
    assert "💰 已卖出 皮甲 ×1" in out[0]
    assert "💰 已卖出 盾牌 ×1" in out[0]
    # 背包不足 / 商店不收 → 部分失败
    out = shop(p, ev("/商店 卖 皮甲 魔法扫帚"))
    assert "批量卖出：成功 1 件，失败 1 件" in out[0]
    assert "❌ 商店不收「魔法扫帚」" in out[0]


# ---------------------------------------------------------------------------
# 批量上架/下架
# ---------------------------------------------------------------------------


def test_shop_batch_add_per_item_attrs(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(
        p, ev("/商店 上架 神秘符咒 价=3金 库存=2 火球法杖 库存=3", admin=True)
    )
    assert "批量上架：成功 2 件" in out[0]
    assert "已上架 神秘符咒（3金，库存 2）" in out[0]
    assert "已上架 火球法杖（库价，库存 3）" in out[0]
    out = shop(p, ev("/商店"))
    assert "神秘符咒 — 3金（余 2）" in out[0]
    assert "火球法杖 — 未定价（余 3）" in out[0]


def test_shop_batch_add_attr_before_name(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(p, ev("/商店 上架 价=3金 长剑", admin=True))
    assert "属性「价=3金」前缺少商品名称" in out[0]


def test_shop_batch_add_duplicate_partial(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    out = shop(p, ev("/商店 上架 神秘符咒 神秘符咒", admin=True))
    assert "批量上架：成功 1 件，失败 1 件" in out[0]
    assert "❌ 「神秘符咒」已在架" in out[0]


def test_shop_batch_remove(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    out = shop(p, ev("/商店 下架 长剑 皮甲 不存在物", admin=True))
    assert "已下架：长剑、皮甲（共 2 件）。" in out[0]
    assert "❌ 商店里没有「不存在物」" in out[0]
    out = shop(p, ev("/商店"))
    assert "长剑" not in out[0] and "皮甲" not in out[0]


# ---------------------------------------------------------------------------
# manage_shop llm_tool：批量 items 与管理动作
# ---------------------------------------------------------------------------


def test_tool_batch_buy_items_array(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 40)
    out = run(
        p.manage_shop_tool(
            ev(""),
            action="buy",
            items=[{"item": "长剑", "qty": 1}, {"item": "皮甲", "qty": 2}],
        )
    )
    assert "批量购买：成功 2 件" in out
    assert "已购买 长剑 ×1" in out
    assert "已购买 皮甲 ×2" in out


def test_tool_batch_items_as_json_string(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 40)
    out = run(
        p.manage_shop_tool(
            ev(""), action="buy", items='[{"item": "长剑", "qty": 1}]'
        )
    )
    assert "批量购买：成功 1 件" in out
    assert "已购买 长剑 ×1" in out


def test_tool_items_overrides_single_params(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    add_coins(p, "金币", 40)
    # items 与 item/qty 并存时 items 优先
    out = run(
        p.manage_shop_tool(
            ev(""), action="buy", item="长剑", qty=1, items=[{"item": "皮甲", "qty": 1}]
        )
    )
    assert "已购买 皮甲 ×1" in out
    assert "长剑" not in out


def test_tool_admin_actions_permission(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    # 非管理员被拒
    out = run(p.manage_shop_tool(ev(""), action="add", item="神秘符咒"))
    assert "你没有权限上架商品" in out
    out = run(p.manage_shop_tool(ev(""), action="remove", item="长剑"))
    assert "你没有权限下架商品" in out
    out = run(p.manage_shop_tool(ev(""), action="clear"))
    assert "你没有权限清空商店" in out
    # 商品未动
    assert "共 5 种商品" in shop(p, ev("/商店"))[0]


def test_tool_admin_actions_allowed(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    init_shop(p)
    # 管理员批量上架（items 带 price/stock）
    out = run(
        p.manage_shop_tool(
            ev("", admin=True),
            action="add",
            items=[{"item": "神秘符咒", "price": "3金", "stock": 2}],
        )
    )
    assert "批量上架：成功 1 件" in out
    assert "已上架 神秘符咒（3金，库存 2）" in out
    # 管理员清空
    out = run(p.manage_shop_tool(ev("", admin=True), action="clear"))
    assert "已清空商店，共移除 6 种商品" in out

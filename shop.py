"""shop.py — 商店（Shop）管理模块。

商店是「DM 配置的商品列表 + 玩家自助买卖结算」的会话级功能（v0.20.0）：
- 一个会话一家店，KV `shop:{origin}`（origin = unified_msg_origin）；
- 商品条目 = 名称 + 可选售价覆盖（默认用知识库库价）+ 可选库存（None = 无限）
  + 知识库带出的单件重量（供买入时写入背包条目）；
- 货币结算遵循「货币即物品条目」约定（money.py）：买入折铜扣款 + 自动找零，
  卖出按「商店售价 × 回购系数」付款；
- 回购只收在架商品：计数库存 +qty，无限库存不变，不上架新条目。

锁边界（务必遵守，防死锁）：
  买卖事务跨 ShopManager 与 InventoryManager 两把锁，固定顺序为
  **先 Shop 锁，后 Inventory 锁**（事务入口集中在 ShopManager.buy/sell）。
  InventoryManager 永不反向持有 Shop 锁，锁序无环即无死锁。
  背包侧写入（扣货币/入货/扣货/入货币）各自封装成 InventoryManager 的
  settle_purchase / settle_sale 单次调用，在同一把 Inventory 锁内原子完成。

模块结构遵循项目惯例：dataclass + to_dict/from_dict（容错脏数据）
+ Manager + format_* 静态方法。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot.api import logger

from .money import (
    COIN_VALUE,
    format_cp,
    inventory_copper,
    make_change,
    settle_payment,
)

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Star

    from .kb import KnowledgeBaseManager

# KV 存储 key 前缀（与 inventory: 等并列）。
_KV_PREFIX_SHOP = "shop:"

# 商品名称最大长度（与背包条目一致，防伪造多行输出）。
_NAME_MAX = 30

# 库存/数量上限（防天文数字）。
_QTY_MAX = 99999

# 回购系数合法区间。
_RATE_MIN = 0.0
_RATE_MAX = 2.0

# 单次展示的商品条数上限（初始化可能上千件，列表分页）。
_LIST_LIMIT = 30


def _sanitize_name(text: str) -> str:
    """剔除名称中的控制字符并截断至 _NAME_MAX 字符。"""
    cleaned = "".join(
        ch for ch in (text or "") if ch not in "\r\n\t\x00\x0b\x0c\x0e\x1f"
    ).strip()
    if len(cleaned) > _NAME_MAX:
        cleaned = cleaned[:_NAME_MAX] + "…"
    return cleaned


def _to_int(value: object) -> int | None:
    """容错转非负 int（None/非法/负数 → None）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _to_float(value: object) -> float | None:
    """容错转非负 float（None/非法/负数 → None）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ShopEntry:
    """商店中的单个商品条目。"""

    name: str  # 商品名称（已清洗）
    price_cp: int | None = None  # 售价覆盖（铜币）；None = 用知识库库价
    stock: int | None = None  # 库存；None = 无限
    weight_lb: float | None = None  # 单件重量（知识库带出，购买时写入背包）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "price_cp": self.price_cp,
            "stock": self.stock,
            "weight_lb": self.weight_lb,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ShopEntry:
        return cls(
            name=_sanitize_name(str(data.get("name", ""))),
            price_cp=_to_int(data.get("price_cp")),
            stock=_to_int(data.get("stock")),
            weight_lb=_to_float(data.get("weight_lb")),
        )


@dataclass
class Shop:
    """一家商店：商品列表 + 回购系数。"""

    entries: list[ShopEntry] = field(default_factory=list)
    buyback_rate: float = 1.0  # 回购系数：卖出价 = 售价 × 系数（1.0 = 全价）

    def find(self, name: str) -> ShopEntry | None:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "buyback_rate": self.buyback_rate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Shop:
        raw_entries = data.get("entries", [])
        entries: list[ShopEntry] = []
        if isinstance(raw_entries, list):
            for d in raw_entries:
                if not isinstance(d, dict):
                    continue
                entry = ShopEntry.from_dict(d)
                if entry.name:
                    entries.append(entry)
        try:
            rate = float(data.get("buyback_rate", 1.0))
        except (TypeError, ValueError):
            rate = 1.0
        return cls(entries=entries, buyback_rate=max(_RATE_MIN, min(rate, _RATE_MAX)))


# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


@dataclass
class BuyResult:
    """购买结果。"""

    ok: bool
    item_name: str
    qty: int
    reason: str = ""  # "" / "not_found" / "sold_out" / "no_price" / "insufficient_money"
    price_cp: int = 0  # 成交单价（总价 = price_cp × qty）
    total_cp: int = 0
    shortfall_cp: int = 0  # 钱不够时差多少
    stock_left: int | None = None  # 扣减后库存（无限为 None）


@dataclass
class SellResult:
    """回购结果。"""

    ok: bool
    item_name: str
    qty: int
    reason: str = ""  # "" / "not_found" / "no_price" / "insufficient"（背包数量不足）
    pay_cp: int = 0  # 应付总额（售价 × 系数 × qty）
    price_cp: int = 0  # 单价基准
    stock_left: int | None = None


# ---------------------------------------------------------------------------
# 商店管理器
# ---------------------------------------------------------------------------


class ShopManager:
    """基于 AstrBot KV 的商店管理器（单店/会话）。

    本模块不做权限判断：上架/下架/设价/设库存/初始化/回购率等管理操作
    的鉴权由命令层（main.py）控制；买卖对全员开放。
    """

    def __init__(
        self,
        star: Star,
        kb_manager: "KnowledgeBaseManager",
        inventory_manager,
    ) -> None:
        self._star = star
        self._kb = kb_manager
        self._inventory = inventory_manager
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 内部 KV 读写
    # ------------------------------------------------------------------

    @staticmethod
    def _key(origin: str) -> str:
        return f"{_KV_PREFIX_SHOP}{origin}"

    async def _load(self, origin: str) -> Shop:
        try:
            raw = await self._star.get_kv_data(self._key(origin), None)
            if isinstance(raw, dict):
                return Shop.from_dict(raw)
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取商店失败: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 读取商店时发生未预期异常: {e}")
        return Shop()

    async def _save(self, origin: str, shop: Shop) -> None:
        try:
            await self._star.put_kv_data(self._key(origin), shop.to_dict())
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入商店失败: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 写入商店时发生未预期异常: {e}")

    # ------------------------------------------------------------------
    # 只读接口（不加锁，与 InventoryManager.get_personal 一致）
    # ------------------------------------------------------------------

    async def get(self, origin: str) -> Shop:
        return await self._load(origin)

    def entry_price_cp(self, entry: ShopEntry) -> int | None:
        """商品实际单价（铜币）：售价覆盖优先，否则知识库库价。"""
        if entry.price_cp is not None:
            return entry.price_cp
        return self.resolve_price(entry.name)

    def resolve_price(self, name: str) -> int | None:
        """按名称查知识库库价（铜币）；库不可用/无价返回 None（列表显示用）。"""
        if self._kb is not None and self._kb.available:
            try:
                stats = self._kb.item_price(name)
            except Exception as e:  # noqa: BLE001 — 库不可用/旧库无列时降级无价
                logger.warning(f"[trpg_assistant] 查询库价失败: {e}")
                return None
            if stats is not None:
                return stats[0]
        return None

    # ------------------------------------------------------------------
    # 管理写接口（命令层鉴权后调用，全在 self._lock 内）
    # ------------------------------------------------------------------

    async def init_from_kb(
        self, origin: str, seeds: list[tuple[str, str, str, int | None, float | None]]
    ) -> int:
        """以知识库初始商品候选重建商店（覆盖旧配置，回购系数保留）。

        seeds：kb.list_init_shop_items() 返回的
        (名称, 来源, 版本, 价值cp, 重量lb) 列表；只收有库价的候选。
        """
        async with self._lock:
            shop = await self._load(origin)
            old_rate = shop.buyback_rate
            entries: list[ShopEntry] = []
            for name, _src, _edition, value_cp, weight_lb in seeds:
                v = _to_int(value_cp)
                if v is None:
                    continue
                entries.append(
                    ShopEntry(
                        name=_sanitize_name(name),
                        price_cp=None,
                        stock=None,
                        weight_lb=_to_float(weight_lb),
                    )
                )
            shop = Shop(entries=entries, buyback_rate=old_rate)
            await self._save(origin, shop)
        return len(entries)

    async def add_entry(
        self,
        origin: str,
        name: str,
        price_cp: int | None = None,
        stock: int | None = None,
        weight_lb: float | None = None,
    ) -> tuple[bool, str]:
        """上架商品（同名覆盖）。返回 (是否成功, 原因/提示)。"""
        clean = _sanitize_name(name)
        if not clean:
            return False, "名称不能为空"
        async with self._lock:
            shop = await self._load(origin)
            if shop.find(clean) is not None:
                return False, f"「{clean}」已在架，可用 /商店 设价 / 设库存 调整。"
            shop.entries.append(
                ShopEntry(
                    name=clean,
                    price_cp=_to_int(price_cp),
                    stock=_to_int(stock),
                    weight_lb=_to_float(weight_lb),
                )
            )
            await self._save(origin, shop)
        return True, ""

    async def remove_entry(self, origin: str, name: str) -> bool:
        async with self._lock:
            shop = await self._load(origin)
            entry = shop.find(name)
            if entry is None:
                return False
            shop.entries.remove(entry)
            await self._save(origin, shop)
        return True

    async def set_price(self, origin: str, name: str, price_cp: int | None) -> bool:
        """设价（覆盖库价）；price_cp=None 恢复用库价。"""
        async with self._lock:
            shop = await self._load(origin)
            entry = shop.find(name)
            if entry is None:
                return False
            entry.price_cp = _to_int(price_cp)
            await self._save(origin, shop)
        return True

    async def set_stock(self, origin: str, name: str, stock: int | None) -> bool:
        """设库存（None = 无限；0 = 售罄）。"""
        async with self._lock:
            shop = await self._load(origin)
            entry = shop.find(name)
            if entry is None:
                return False
            entry.stock = _to_int(stock)
            await self._save(origin, shop)
        return True

    async def set_rate(self, origin: str, rate: float) -> float:
        """设回购系数，返回实际生效值（clamp 到 [0, 2]）。"""
        clamped = max(_RATE_MIN, min(float(rate), _RATE_MAX))
        async with self._lock:
            shop = await self._load(origin)
            shop.buyback_rate = clamped
            await self._save(origin, shop)
        return clamped

    async def clear(self, origin: str) -> int:
        """清空商店全部商品条目（回购系数保留，与 init_from_kb 语义一致）。

        空店不写 KV；返回移除的商品条目数。
        """
        async with self._lock:
            shop = await self._load(origin)
            count = len(shop.entries)
            if not count:
                return 0
            shop.entries = []
            await self._save(origin, shop)
            return count

    # ------------------------------------------------------------------
    # 买卖事务（跨商店+背包两把锁，固定顺序：Shop → Inventory）
    # ------------------------------------------------------------------

    async def buy(
        self,
        event: "AstrMessageEvent",
        origin: str,
        name: str,
        qty: int = 1,
    ) -> BuyResult:
        """购买：校验（在架 + 有价 + 库存够）→ 背包侧「扣款 + 入货」→ 扣库存。

        全部校验通过后才写背包，库存扣减在 Shop 锁内完成；任一校验失败
        均不产生写入。返回 BuyResult 供命令层拼文案。
        """
        qty = max(1, min(int(qty), _QTY_MAX))
        clean = _sanitize_name(name)
        async with self._lock:
            shop = await self._load(origin)
            entry = shop.find(clean)
            if entry is None:
                return BuyResult(ok=False, item_name=clean, qty=qty, reason="not_found")
            if entry.stock is not None:
                if entry.stock <= 0:
                    return BuyResult(
                        ok=False, item_name=clean, qty=qty, reason="sold_out"
                    )
                if entry.stock < qty:
                    return BuyResult(
                        ok=False, item_name=clean, qty=qty, reason="sold_out",
                        stock_left=entry.stock,
                    )
            price_cp = self.entry_price_cp(entry)
            if price_cp is None:
                return BuyResult(ok=False, item_name=clean, qty=qty, reason="no_price")
            total_cp = price_cp * qty

            # 背包侧：扣款（折铜+找零）+ 入货，同一把 Inventory 锁内原子完成。
            ok, reason = await self._inventory.settle_purchase(
                event,
                total_cp=total_cp,
                item_name=clean,
                qty=qty,
                weight=entry.weight_lb,
                value=float(price_cp),
                note="",
            )
            if not ok:
                shortfall = 0
                try:
                    inv = await self._inventory.get_personal(event)
                    coins = {c: 0 for c in COIN_VALUE}
                    for c in COIN_VALUE:
                        e = inv.find(c)
                        if e is not None:
                            coins[c] = e.qty
                    shortfall = total_cp - inventory_copper(coins)
                except Exception:  # noqa: BLE001
                    shortfall = 0
                return BuyResult(
                    ok=False, item_name=clean, qty=qty, reason="insufficient_money",
                    total_cp=total_cp, price_cp=price_cp, shortfall_cp=max(shortfall, 0),
                )

            if entry.stock is not None:
                entry.stock -= qty
                await self._save(origin, shop)
            return BuyResult(
                ok=True, item_name=clean, qty=qty, price_cp=price_cp,
                total_cp=total_cp,
                stock_left=None if entry.stock is None else entry.stock,
            )

    async def sell(
        self,
        event: "AstrMessageEvent",
        origin: str,
        name: str,
        qty: int = 1,
    ) -> SellResult:
        """回购：只收在架商品 → 背包侧「扣货 + 入货币」→ 有计数库存 +qty。

        卖出价 = 商店当前售价 × 回购系数 × qty（取整到铜币）。
        """
        qty = max(1, min(int(qty), _QTY_MAX))
        clean = _sanitize_name(name)
        async with self._lock:
            shop = await self._load(origin)
            entry = shop.find(clean)
            if entry is None:
                return SellResult(ok=False, item_name=clean, qty=qty, reason="not_found")
            price_cp = self.entry_price_cp(entry)
            if price_cp is None:
                return SellResult(ok=False, item_name=clean, qty=qty, reason="no_price")
            pay_cp = int(price_cp * qty * shop.buyback_rate)

            ok, reason = await self._inventory.settle_sale(
                event, item_name=clean, qty=qty, pay_cp=pay_cp
            )
            if not ok:
                return SellResult(
                    ok=False, item_name=clean, qty=qty, reason=reason,
                    price_cp=price_cp, pay_cp=pay_cp,
                )
            if entry.stock is not None:
                entry.stock = min(entry.stock + qty, _QTY_MAX)
                await self._save(origin, shop)
            return SellResult(
                ok=True, item_name=clean, qty=qty, price_cp=price_cp, pay_cp=pay_cp,
                stock_left=None if entry.stock is None else entry.stock,
            )

    # ------------------------------------------------------------------
    # 格式化辅助方法（静态，供命令与 LLM 工具复用）
    # ------------------------------------------------------------------

    @staticmethod
    def list_limit() -> int:
        """商品列表每页条数（分页翻页用，/商店 <页码>）。"""
        return _LIST_LIMIT

    @staticmethod
    def format_shop(
        shop: Shop,
        title: str = "🏪 本店商品",
        page: int = 1,
        price_resolver=None,
    ) -> str:
        """渲染商品列表（分页展示前 _LIST_LIMIT 条；计数库存显示余量）。

        - 页码越界自动夹取到合法范围（1..总页数）；
        - 价格显示：售价覆盖优先；未覆盖时若提供 price_resolver（如
          ShopManager.resolve_price）则解析知识库库价显示具体金额，
          解析不到标「未定价」；无 resolver 时标「库价」。
        """
        total = len(shop.entries)
        total_pages = max(1, (total + _LIST_LIMIT - 1) // _LIST_LIMIT)
        page = max(1, min(page, total_pages))
        lines = [f"{title}（共 {total} 种商品，第 {page}/{total_pages} 页）："]
        start = (page - 1) * _LIST_LIMIT
        shown = shop.entries[start : start + _LIST_LIMIT]
        for idx, e in enumerate(shown, start + 1):
            price_note = (
                format_cp(e.price_cp)
                if e.price_cp is not None
                else _resolve_list_price(e, price_resolver)
            )
            stock_note = "无限" if e.stock is None else (
                "售罄" if e.stock <= 0 else f"余 {e.stock}"
            )
            lines.append(f"{idx}. **{e.name}** — {price_note}（{stock_note}）")
        if total > _LIST_LIMIT:
            lines.append(
                f"输入 /商店 <页码> 翻页（每页 {_LIST_LIMIT} 条，共 {total_pages} 页）。"
            )
        lines.append("购买：/商店 买 <名称> <数量>；卖出：/商店 卖 <名称> <数量>。")
        return "\n".join(lines)

    @staticmethod
    def format_status(shop: Shop) -> str:
        """商店配置概览（回购系数 + 条目数）。"""
        return (
            f"🏪 商店配置：共 {len(shop.entries)} 种商品，"
            f"回购系数 {shop.buyback_rate:g}（卖出价 = 售价 × 系数）。"
        )


def _resolve_list_price(entry: ShopEntry, price_resolver=None) -> str:
    """商品列表的价格展示：resolver 解析库价 → 具体金额；无价 → 「未定价」；
    无 resolver（纯格式化场景）→ 「库价」占位。"""
    if price_resolver is None:
        return "库价"
    try:
        cp = price_resolver(entry.name)
    except Exception as e:  # noqa: BLE001 — 列表显示容错，不阻断
        logger.warning(f"[trpg_assistant] 列表解析库价失败: {e}")
        return "未定价"
    return format_cp(cp) if cp is not None else "未定价"

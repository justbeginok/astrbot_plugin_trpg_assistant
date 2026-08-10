"""
inventory.py — 背包（Inventory）管理模块。

提供「个人背包 + 队伍背包」双模型，通过 AstrBot 的 KV 存储持久化。

归属模型：
  - 个人背包：inventory:{unified_msg_origin}:{sender_id}，归属单个玩家。
  - 队伍背包：inventory:party:{unified_msg_origin}，归属整个会话（群），
    会话内全员可存取（清空权限由命令层控制，本模块不鉴权）。

物品模型：
  - 五字段：名称（必填）、数量（必填，>=1）、单件重量、单件价值、备注。
  - 货币是普通物品（如「金币」条目），不做独立钱包。
  - v0.20.0 起：价值字段统一以铜币为单位（1金币=10银币=100铜币），
    货币条目（金币/银币/铜币）的 value 按面值 100/10/1 自动维护，
    显示层把铜币换算成「X金Y银Z铜」；旧版手工录入的单位不再适用。
  - 同名物品合并数量；合并时本次提供的 w/v/note 覆盖旧属性。
  - remove 归零自动删除条目。

物品流转（在同一把管理器锁内完成，源扣除+目标合并原子写入）：
  - put：个人 → 队伍；take：队伍 → 个人；give：个人 → 个人。
  - settle_purchase / settle_sale：商店买卖的背包侧结算
    （扣货币+入货 / 扣货+入货币，同一把锁内原子，供 ShopManager 调用）。

锁序约束：ShopManager 买卖事务固定「先 Shop 锁，后 Inventory 锁」；
本模块的写方法仅持自身 _lock，永不反向持有 Shop 锁（无死锁环）。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot.api import logger

from .money import (
    COIN_VALUE,
    format_cp,
    make_change,
    settle_payment,
)

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Star

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# KV 存储 key 前缀（PluginKVStoreMixin 已按 plugin_id 隔离命名空间，
# 此前缀仅用于本插件内部区分功能，与 initiative:、history: 等区分）。
_KV_PREFIX_PERSONAL = "inventory:"  # inventory:{origin}:{sender_id}
_KV_PREFIX_PARTY = "inventory:party:"  # inventory:party:{origin}

# 物品名称 / 备注最大长度，超出截断并追加省略号。
_NAME_MAX = 30
_NOTE_MAX = 60

# 单次操作与单条目数量上限（防止天文数字撑爆输出与存储）。
_QTY_MAX = 99999

# 需要从名称/备注中剔除的控制字符正则（与 initiative.py 一致）。
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_name(text: str) -> str:
    """剔除名称中的控制字符并截断至 _NAME_MAX 字符，防止伪造多行输出。"""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text).strip()
    if len(cleaned) > _NAME_MAX:
        cleaned = cleaned[:_NAME_MAX] + "…"
    return cleaned


def _sanitize_note(text: str) -> str:
    """剔除备注中的控制字符并截断至 _NOTE_MAX 字符。"""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text).strip()
    if len(cleaned) > _NOTE_MAX:
        cleaned = cleaned[:_NOTE_MAX] + "…"
    return cleaned


def _to_non_neg_float(value: object) -> float | None:
    """将任意值安全转换为非负 float；None/转换失败/负数返回 None。"""
    if value is None:
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if result != result or result < 0:  # NaN 或负数
        return None
    return result


def _fmt_num(value: float) -> str:
    """格式化重量/价值数值：去掉多余的尾零（0.5 而非 0.50）。"""
    return f"{value:g}"


# 编辑属性的「未提供」哨兵：区分「本次调用不修改该字段」与「清除该字段」
# （None = 清除；_UNSET = 保持原值）。
_UNSET = object()


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ItemEntry:
    """背包中的单个物品条目。"""

    name: str  # 物品名称（已清洗）
    qty: int  # 数量，>= 1（归零即删除条目，不允许 0 存在）
    weight: float | None = None  # 单件重量，None = 未设置
    value: float | None = None  # 单件价值，None = 未设置
    note: str = ""  # 备注，空串 = 无

    def line_total_weight(self) -> float | None:
        """该行总重量；未设置单件重量时返回 None。"""
        return None if self.weight is None else self.weight * self.qty

    def line_total_value(self) -> float | None:
        """该行总价值；未设置单件价值时返回 None。"""
        return None if self.value is None else self.value * self.qty

    # ------------------------------------------------------------------
    # 序列化辅助方法
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转换为可写入 KV 存储的字典。"""
        return {
            "name": self.name,
            "qty": self.qty,
            "weight": self.weight,
            "value": self.value,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ItemEntry:
        """从 KV 存储读取的字典中还原条目，容忍缺失字段与脏数据类型。"""
        try:
            qty = int(data.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        return cls(
            name=str(data.get("name", "")),
            qty=qty,
            weight=_to_non_neg_float(data.get("weight")),
            value=_to_non_neg_float(data.get("value")),
            note=str(data.get("note", "") or ""),
        )


@dataclass
class Inventory:
    """一个背包：物品条目的有序集合（按入包先后排序）。"""

    items: list[ItemEntry] = field(default_factory=list)

    def find(self, name: str) -> ItemEntry | None:
        """按名称精确查找条目（不大小写折叠，与先攻的名称匹配一致）。"""
        for item in self.items:
            if item.name == name:
                return item
        return None

    def total_weight(self) -> tuple[float, bool]:
        """返回 (总重量, 是否存在未设重量的条目)。未设重量的条目按 0 计。"""
        total = 0.0
        has_unset = False
        for item in self.items:
            line = item.line_total_weight()
            if line is None:
                has_unset = True
            else:
                total += line
        return total, has_unset

    def total_value(self) -> tuple[float, bool]:
        """返回 (总价值, 是否存在未设价值的条目)。未设价值的条目按 0 计。

        v0.20.0 起单位统一为铜币；货币条目（金币/银币/铜币）按面值
        （100/10/1）计入，不看其存储的 value 字段。
        """
        total = 0.0
        has_unset = False
        for item in self.items:
            unit = COIN_VALUE.get(item.name)
            if unit is not None:
                total += unit * item.qty
                continue
            line = item.line_total_value()
            if line is None:
                has_unset = True
            else:
                total += line
        return total, has_unset

    # ------------------------------------------------------------------
    # 序列化辅助方法
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转换为可写入 KV 存储的字典。"""
        return {"items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, data: dict) -> Inventory:
        """从 KV 存储读取的字典中还原背包，容忍缺失字段与脏数据。

        非 dict 条目直接跳过；qty < 1 或名称为空的脏条目丢弃。
        """
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            return cls()
        items: list[ItemEntry] = []
        for d in raw_items:
            if not isinstance(d, dict):
                continue
            entry = ItemEntry.from_dict(d)
            if entry.qty < 1 or not entry.name:
                continue
            items.append(entry)
        return cls(items=items)


@dataclass
class TransferResult:
    """物品流转（put/take/give）的结果，供调用方拼装提示文案。"""

    ok: bool  # 是否完成流转（源扣除 + 目标写入均已生效）
    item_name: str  # 目标物品名称（已清洗）
    qty: int  # 请求流转的数量
    reason: str = ""  # 失败原因："" / "not_found" / "insufficient"
    available: int = 0  # 源背包中现有数量（insufficient 时供文案使用）


@dataclass
class RemoveResult:
    """移除物品的结果。"""

    removed_qty: int  # 实际移除的数量（失败时为 0）
    remaining: int  # 操作后剩余数量（条目已删除时为 0）
    deleted: bool  # 条目是否因归零被删除
    found: bool  # 是否找到了该物品


# ---------------------------------------------------------------------------
# 背包管理器
# ---------------------------------------------------------------------------


class InventoryManager:
    """基于 AstrBot KV 存储的背包管理器（个人背包 + 队伍背包）。

    本模块不做权限判断：队伍背包的清空权限、私聊中禁用队伍背包等
    规则均由命令层（main.py）控制。
    """

    def __init__(self, star: Star) -> None:
        self._star = star
        # 单把管理器级锁，保证所有读-改-写互斥（与 InitiativeManager 一致）。
        # put/take/give 需同时读写两个背包，同一把锁天然保证原子性；
        # 不存在多把锁故无死锁问题。
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 内部 KV 读写
    # ------------------------------------------------------------------

    async def _load(self, key: str) -> Inventory:
        try:
            raw = await self._star.get_kv_data(key, None)
            if isinstance(raw, dict):
                return Inventory.from_dict(raw)
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取背包失败: {e}")
        except Exception as e:
            logger.warning(f"[trpg_assistant] 读取背包时发生未预期异常: {e}")
        return Inventory()

    async def _save(self, key: str, inv: Inventory) -> None:
        try:
            await self._star.put_kv_data(key, inv.to_dict())
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入背包失败: {e}")
        except Exception as e:
            logger.warning(f"[trpg_assistant] 写入背包时发生未预期异常: {e}")

    @staticmethod
    def _personal_key(event: AstrMessageEvent, sender_id: str | None = None) -> str:
        sid = sender_id if sender_id is not None else str(event.get_sender_id())
        return f"{_KV_PREFIX_PERSONAL}{event.unified_msg_origin}:{sid}"

    @staticmethod
    def _party_key(event: AstrMessageEvent) -> str:
        return f"{_KV_PREFIX_PARTY}{event.unified_msg_origin}"

    # ------------------------------------------------------------------
    # 只读接口（不加锁，同 InitiativeManager.get_state）
    # ------------------------------------------------------------------

    async def get_personal(
        self, event: AstrMessageEvent, sender_id: str | None = None
    ) -> Inventory:
        """返回指定玩家（默认发送者本人）的个人背包（只读）。"""
        return await self._load(self._personal_key(event, sender_id))

    async def get_party(self, event: AstrMessageEvent) -> Inventory:
        """返回当前会话的队伍背包（只读）。"""
        return await self._load(self._party_key(event))

    # ------------------------------------------------------------------
    # 写接口（全部在 self._lock 内读-改-写）
    # ------------------------------------------------------------------

    async def add_item(
        self,
        event: AstrMessageEvent,
        name: str,
        qty: int,
        weight: float | None = None,
        value: float | None = None,
        note: str | None = None,
        to_party: bool = False,
    ) -> tuple[ItemEntry, bool]:
        """向背包放入物品，返回 (合并后的条目, 是否发生了同名合并)。

        同名合并：数量累加；本次调用中 weight/value/note 非 None 的字段
        覆盖旧值，为 None 则保留旧值。
        """
        key = self._party_key(event) if to_party else self._personal_key(event)
        clean_name = _sanitize_name(name)
        if not clean_name:
            raise ValueError("物品名称不能为空")
        qty = max(1, min(int(qty), _QTY_MAX))
        weight = _to_non_neg_float(weight)
        value = _to_non_neg_float(value)
        clean_note = _sanitize_note(note) if note is not None else None

        async with self._lock:
            inv = await self._load(key)
            existing = inv.find(clean_name)
            if existing is not None:
                existing.qty = min(existing.qty + qty, _QTY_MAX)
                if weight is not None:
                    existing.weight = weight
                if value is not None:
                    existing.value = value
                if clean_note is not None:
                    existing.note = clean_note
                entry = existing
                merged = True
            else:
                entry = ItemEntry(
                    name=clean_name,
                    qty=qty,
                    weight=weight,
                    value=value,
                    note=clean_note or "",
                )
                inv.items.append(entry)
                merged = False
            await self._save(key, inv)
        return entry, merged

    async def edit_item(
        self,
        event: AstrMessageEvent,
        name: str,
        weight: object = _UNSET,
        value: object = _UNSET,
        note: object = _UNSET,
        in_party: bool = False,
    ) -> ItemEntry | None:
        """编辑物品属性，返回更新后的条目；物品不存在返回 None。

        每个字段的三态语义（weight/value/note）：
          - 传 _UNSET（默认）：保持原值不变；
          - 传 None：清除该字段（weight/value 置 None，note 置空串）；
          - 传其他值：覆盖为新值（weight/value 经 _to_non_neg_float 容错，
            非法值视为清除并记录告警）。
        不涉及数量与名称的修改。
        """
        key = self._party_key(event) if in_party else self._personal_key(event)
        clean_name = _sanitize_name(name)
        if not clean_name:
            return None

        # 调用方已校验参数，这里做二次容错（保持 from_dict 的容错风格）。
        w = _to_non_neg_float(weight) if weight is not _UNSET else _UNSET
        v = _to_non_neg_float(value) if value is not _UNSET else _UNSET
        n = (
            _sanitize_note(note) if note is not None else None
        ) if note is not _UNSET else _UNSET

        async with self._lock:
            inv = await self._load(key)
            entry = inv.find(clean_name)
            if entry is None:
                return None
            if w is not _UNSET:
                entry.weight = w
            if v is not _UNSET:
                entry.value = v
            if n is not _UNSET:
                entry.note = n or ""
            await self._save(key, inv)
        return entry

    async def remove_item(
        self,
        event: AstrMessageEvent,
        name: str,
        qty: int = 1,
        from_party: bool = False,
    ) -> RemoveResult:
        """从背包取出物品。数量不足或物品不存在时不做任何写入。"""
        key = self._party_key(event) if from_party else self._personal_key(event)
        clean_name = _sanitize_name(name)
        qty = max(1, int(qty))

        async with self._lock:
            inv = await self._load(key)
            entry = inv.find(clean_name)
            if entry is None:
                return RemoveResult(removed_qty=0, remaining=0, deleted=False, found=False)
            if entry.qty < qty:
                return RemoveResult(
                    removed_qty=0, remaining=entry.qty, deleted=False, found=True
                )
            entry.qty -= qty
            deleted = entry.qty == 0
            if deleted:
                inv.items.remove(entry)
            await self._save(key, inv)
            return RemoveResult(
                removed_qty=qty, remaining=entry.qty, deleted=deleted, found=True
            )

    async def clear_personal(self, event: AstrMessageEvent) -> int:
        """清空发送者的个人背包，返回被清除的物品种数。"""
        return await self._clear_key(self._personal_key(event))

    async def clear_party(self, event: AstrMessageEvent) -> int:
        """清空当前会话的队伍背包，返回被清除的物品种数。"""
        return await self._clear_key(self._party_key(event))

    async def _clear_key(self, key: str) -> int:
        async with self._lock:
            inv = await self._load(key)
            count = len(inv.items)
            await self._star.delete_kv_data(key)
            return count

    async def put_to_party(
        self, event: AstrMessageEvent, name: str, qty: int = 1
    ) -> TransferResult:
        """个人 → 队伍。"""
        return await self._transfer(
            self._personal_key(event), self._party_key(event), name, qty
        )

    async def take_from_party(
        self, event: AstrMessageEvent, name: str, qty: int = 1
    ) -> TransferResult:
        """队伍 → 个人。"""
        return await self._transfer(
            self._party_key(event), self._personal_key(event), name, qty
        )

    async def give(
        self, event: AstrMessageEvent, target_id: str, name: str, qty: int = 1
    ) -> TransferResult:
        """个人 → 个人（目标为同一会话内的其他玩家）。"""
        return await self._transfer(
            self._personal_key(event),
            self._personal_key(event, target_id),
            name,
            qty,
        )

    async def _transfer(
        self, src_key: str, dst_key: str, name: str, qty: int
    ) -> TransferResult:
        """在同一把锁内完成「源扣除 + 目标合并写入」。

        源数量不足或物品不存在时整体不写入（原子性）。
        流转携带源条目的 w/v/note 属性；目标已有同名条目时按合并语义，
        以转出方属性中的非 None 字段覆盖目标属性。
        """
        clean_name = _sanitize_name(name)
        qty = max(1, int(qty))

        async with self._lock:
            src = await self._load(src_key)
            entry = src.find(clean_name)
            if entry is None:
                return TransferResult(
                    ok=False, item_name=clean_name, qty=qty, reason="not_found"
                )
            if entry.qty < qty:
                return TransferResult(
                    ok=False,
                    item_name=clean_name,
                    qty=qty,
                    reason="insufficient",
                    available=entry.qty,
                )

            entry.qty -= qty
            if entry.qty == 0:
                src.items.remove(entry)

            dst = await self._load(dst_key)
            dst_entry = dst.find(clean_name)
            if dst_entry is not None:
                dst_entry.qty = min(dst_entry.qty + qty, _QTY_MAX)
                if entry.weight is not None:
                    dst_entry.weight = entry.weight
                if entry.value is not None:
                    dst_entry.value = entry.value
                if entry.note:
                    dst_entry.note = entry.note
            else:
                dst.items.append(
                    ItemEntry(
                        name=clean_name,
                        qty=qty,
                        weight=entry.weight,
                        value=entry.value,
                        note=entry.note,
                    )
                )

            await self._save(src_key, src)
            await self._save(dst_key, dst)
            return TransferResult(ok=True, item_name=clean_name, qty=qty)

    # ------------------------------------------------------------------
    # 商店结算（v0.20.0）：供 ShopManager 在 Shop 锁内调用，本文件方法
    # 只持自身 _lock，与 Shop 锁构成固定「Shop → Inventory」顺序。
    # ------------------------------------------------------------------

    async def settle_purchase(
        self,
        event: AstrMessageEvent,
        *,
        total_cp: int,
        item_name: str,
        qty: int,
        weight: float | None = None,
        value: float | None = None,
        note: str | None = None,
    ) -> tuple[bool, str]:
        """商店购买的背包侧结算：同一把锁内「扣货币（折铜+自动找零）+ 入货」。

        - total_cp > 0：先扣款（settle_payment 整币优先、最多破开一枚大币），
          货币合计不足 → 整体不写入，返回 (False, "insufficient_money")；
        - item_name 非空：入货（同名合并，weight/value/note 非 None 覆盖）；
          货币条目写回时 value 恒按面值 100/10/1；
        - 返回 (是否成功, 失败原因)。写入失败不抛错（KV 容错惯例）。
        """
        key = self._personal_key(event)
        clean_name = _sanitize_name(item_name)
        qty = max(1, min(int(qty), _QTY_MAX))
        async with self._lock:
            inv = await self._load(key)
            if total_cp > 0:
                coins = {c: 0 for c in COIN_VALUE}
                for c in COIN_VALUE:
                    e = inv.find(c)
                    if e is not None:
                        coins[c] = e.qty
                ok, new_coins = settle_payment(coins, total_cp)
                if not ok:
                    return False, "insufficient_money"
                self._apply_coins(inv, new_coins)
            if clean_name:
                entry = inv.find(clean_name)
                w = _to_non_neg_float(weight)
                v = _to_non_neg_float(value)
                n = _sanitize_note(note) if note is not None else None
                if entry is not None:
                    entry.qty = min(entry.qty + qty, _QTY_MAX)
                    if w is not None:
                        entry.weight = w
                    if v is not None:
                        entry.value = v
                    if n is not None:
                        entry.note = n
                else:
                    inv.items.append(
                        ItemEntry(
                            name=clean_name,
                            qty=qty,
                            weight=w,
                            value=v,
                            note=n or "",
                        )
                    )
            await self._save(key, inv)
        return True, ""

    async def settle_sale(
        self,
        event: AstrMessageEvent,
        *,
        item_name: str,
        qty: int,
        pay_cp: int,
    ) -> tuple[bool, str]:
        """商店回购的背包侧结算：同一把锁内「扣物品 + 入货币（找零入包）」。

        物品不存在或数量不足 → 整体不写入，返回 (False, "not_found"/"insufficient")。
        """
        key = self._personal_key(event)
        clean_name = _sanitize_name(item_name)
        qty = max(1, min(int(qty), _QTY_MAX))
        async with self._lock:
            inv = await self._load(key)
            entry = inv.find(clean_name)
            if entry is None:
                return False, "not_found"
            if entry.qty < qty:
                return False, "insufficient"
            entry.qty -= qty
            if entry.qty == 0:
                inv.items.remove(entry)
            if pay_cp > 0:
                coins = {c: 0 for c in COIN_VALUE}
                for coin, n in make_change(pay_cp):
                    coins[coin] = n
                self._apply_coins(inv, coins, add=True)
            await self._save(key, inv)
        return True, ""

    @staticmethod
    def _apply_coins(inv: Inventory, coins: dict[str, int], add: bool = False) -> None:
        """把币种快照写回背包：add=False 表示覆盖（扣款后剩余，归零的条目删除），
        add=True 表示累加（找零入包）。货币条目 value 恒按面值。"""
        for coin, n in coins.items():
            entry = inv.find(coin)
            if add:
                if n <= 0:
                    continue
                if entry is not None:
                    entry.qty = min(entry.qty + n, _QTY_MAX)
                    entry.value = float(COIN_VALUE[coin])
                else:
                    inv.items.append(
                        ItemEntry(name=coin, qty=n, value=float(COIN_VALUE[coin]))
                    )
            elif n > 0:
                if entry is not None:
                    entry.qty = n
                    entry.value = float(COIN_VALUE[coin])
                else:
                    inv.items.append(
                        ItemEntry(name=coin, qty=n, value=float(COIN_VALUE[coin]))
                    )
            elif entry is not None:
                inv.items.remove(entry)

    # ------------------------------------------------------------------
    # 格式化辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def format_item_line(entry: ItemEntry) -> str:
        """将单个条目渲染为一行文本（供确认/流转文案复用）。

        价值字段 v0.20.0 起为铜币口径：货币条目按面值显示（金币→1金），
        其余按铜币换算成「X金Y银Z铜」。
        """
        parts = [f"**{entry.name}** ×{entry.qty}"]
        attrs: list[str] = []
        if entry.weight is not None:
            attrs.append(f"⚖️{_fmt_num(entry.weight)}/件")
        coin_unit = COIN_VALUE.get(entry.name)
        if coin_unit is not None:
            attrs.append(f"💰{format_cp(coin_unit)}/件")
        elif entry.value is not None:
            attrs.append(f"💰{format_cp(int(round(entry.value)))}/件")
        if entry.note:
            attrs.append(f"备注：{entry.note}")
        if attrs:
            parts.append(f"（{'，'.join(attrs)}）")
        return "".join(parts)

    @staticmethod
    def format_inventory(inv: Inventory, title: str) -> str:
        """将背包渲染为列表文本，附总重量/总价值统计（币制显示）。

        存在未设重量/价值的条目时，对应总计追加「+」表示「至少」。
        空背包由调用方决定提示文案（本方法不处理）。
        """
        lines: list[str] = [f"{title}（{len(inv.items)} 种物品）："]
        for idx, item in enumerate(inv.items, 1):
            lines.append(f"{idx}. {InventoryManager.format_item_line(item)}")
        total_w, w_unset = inv.total_weight()
        total_v, v_unset = inv.total_value()
        lines.append(
            f"—— 总重量 ⚖️ {_fmt_num(total_w)}{'+' if w_unset else ''}"
            f"　总价值 💰 {format_cp(int(round(total_v)))}{'+' if v_unset else ''} ——"
        )
        return "\n".join(lines)

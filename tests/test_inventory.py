"""inventory.py 单元测试。

覆盖点：
  - ItemEntry/Inventory to_dict/from_dict：缺字段、脏类型、qty<=0 条目丢弃、
    weight=None 往返。
  - add_item：新条目、同名合并数量、合并时 w/v/note 覆盖与保留（None 不覆盖）、
    名称控制字符清洗与截断、空名称拒绝。
  - remove_item：部分扣减、恰好归零删除（deleted=True）、超出数量不写入、
    物品不存在。
  - edit_item：仅覆盖提供的字段、None 清除、负值清除（LLM 约定）、
    物品不存在返回 None、队伍背包隔离、数量与名称不受影响。
  - put/take/give：流转后两侧数量正确、属性随物品迁移、目标已有同名时合并、
    源不足时两侧均不变（原子性）、源不存在。
  - clear_personal/clear_party：返回条数、KV key 被删除。
  - 会话/玩家隔离：不同 origin、不同 sender_id 的背包互不干扰（KV key 断言）。
  - format_inventory / format_item_line 输出内容。
"""

from __future__ import annotations

import asyncio

from astrbot_plugin_trpg_assistant.inventory import (
    Inventory,
    InventoryManager,
    ItemEntry,
)


class FakeEvent:
    """最小化的假 AstrMessageEvent，仅提供 InventoryManager 依赖的成员。"""

    def __init__(
        self, origin: str, sender_id: str = "u1", sender_name: str = "Alice"
    ) -> None:
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name


class FakeStar:
    """内存字典实现的假 Star，模拟 KV 读写（均为 async）。"""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._data[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._data.pop(key, None)


def run(coro):
    return asyncio.run(coro)


def make_manager() -> tuple[InventoryManager, FakeStar]:
    star = FakeStar()
    return InventoryManager(star=star), star


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_entry_round_trip_with_all_fields(self) -> None:
        entry = ItemEntry(name="治疗药水", qty=3, weight=0.5, value=50.0, note="群体治疗")
        restored = ItemEntry.from_dict(entry.to_dict())
        assert restored == entry

    def test_entry_round_trip_with_none_weight(self) -> None:
        entry = ItemEntry(name="金币", qty=120)
        restored = ItemEntry.from_dict(entry.to_dict())
        assert restored.weight is None
        assert restored.value is None
        assert restored.note == ""

    def test_from_dict_missing_fields_use_defaults(self) -> None:
        entry = ItemEntry.from_dict({"name": "绳索", "qty": 2})
        assert entry.weight is None
        assert entry.value is None
        assert entry.note == ""

    def test_from_dict_dirty_types_fall_back(self) -> None:
        entry = ItemEntry.from_dict(
            {"name": "绳索", "qty": "abc", "weight": "xyz", "value": -5, "note": None}
        )
        assert entry.qty == 0  # 转换失败回退 0，由 Inventory.from_dict 丢弃
        assert entry.weight is None
        assert entry.value is None  # 负数非法 → None
        assert entry.note == ""

    def test_inventory_from_dict_drops_dirty_entries(self) -> None:
        inv = Inventory.from_dict(
            {
                "items": [
                    {"name": "有效", "qty": 1},
                    {"name": "零数量", "qty": 0},
                    {"name": "负数量", "qty": -3},
                    {"name": "", "qty": 5},
                    "not-a-dict",
                    42,
                ]
            }
        )
        assert [i.name for i in inv.items] == ["有效"]

    def test_inventory_from_dict_non_list_items(self) -> None:
        assert Inventory.from_dict({"items": "oops"}).items == []
        assert Inventory.from_dict({}).items == []


# ---------------------------------------------------------------------------
# add_item
# ---------------------------------------------------------------------------


class TestAddItem:
    def test_add_new_entry(self) -> None:
        mgr, _ = make_manager()
        entry, merged = run(
            mgr.add_item(FakeEvent("group:1"), "治疗药水", 3, weight=0.5, value=50)
        )
        assert merged is False
        assert (entry.name, entry.qty, entry.weight, entry.value) == (
            "治疗药水",
            3,
            0.5,
            50.0,
        )

    def test_add_same_name_merges_qty(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3))
        entry, merged = run(mgr.add_item(ev, "治疗药水", 2))
        assert merged is True
        assert entry.qty == 5
        inv = run(mgr.get_personal(ev))
        assert len(inv.items) == 1

    def test_merge_overrides_provided_attrs_keeps_others(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3, weight=0.5, value=50, note="旧备注"))
        entry, _ = run(mgr.add_item(ev, "治疗药水", 1, value=60))
        assert entry.weight == 0.5  # 未提供 → 保留
        assert entry.value == 60.0  # 提供 → 覆盖
        assert entry.note == "旧备注"  # 未提供 → 保留

    def test_add_sanitizes_control_chars(self) -> None:
        mgr, _ = make_manager()
        entry, _ = run(mgr.add_item(FakeEvent("group:1"), "药水\n伪造行", 1))
        assert "\n" not in entry.name

    def test_add_truncates_long_name(self) -> None:
        mgr, _ = make_manager()
        entry, _ = run(mgr.add_item(FakeEvent("group:1"), "很长" * 30, 1))
        assert len(entry.name) <= 31  # 30 + 省略号

    def test_add_empty_name_raises(self) -> None:
        mgr, _ = make_manager()
        try:
            run(mgr.add_item(FakeEvent("group:1"), "  ", 1))
        except ValueError:
            return
        raise AssertionError("空名称应抛出 ValueError")

    def test_add_qty_clamped_to_max(self) -> None:
        mgr, _ = make_manager()
        entry, _ = run(mgr.add_item(FakeEvent("group:1"), "金币", 10**9))
        assert entry.qty == 99999

    def test_add_to_party_uses_party_key(self) -> None:
        mgr, star = make_manager()
        ev = FakeEvent("group:1", sender_id="u1")
        run(mgr.add_item(ev, "金币", 50, to_party=True))
        assert "inventory:party:group:1" in star._data
        assert "inventory:group:1:u1" not in star._data


# ---------------------------------------------------------------------------
# edit_item
# ---------------------------------------------------------------------------


class TestEditItem:
    def test_edit_overrides_provided_fields_only(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3, weight=0.5, value=50, note="旧备注"))
        updated = run(mgr.edit_item(ev, "治疗药水", weight=0.8))
        assert updated is not None
        assert (updated.weight, updated.value, updated.note) == (0.8, 50.0, "旧备注")
        assert updated.qty == 3  # 数量不受影响

    def test_edit_multiple_fields(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3, weight=0.5, value=50))
        updated = run(mgr.edit_item(ev, "治疗药水", value=60, note="已开封"))
        assert (updated.weight, updated.value, updated.note) == (0.5, 60.0, "已开封")

    def test_edit_none_clears_fields(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3, weight=0.5, value=50, note="旧备注"))
        updated = run(mgr.edit_item(ev, "治疗药水", weight=None, note=None))
        assert updated.weight is None
        assert updated.value == 50.0  # 未提供的字段保持
        assert updated.note == ""

    def test_edit_negative_weight_clears(self) -> None:
        """LLM 路径约定：-1 表示清除（_to_non_neg_float 兜底为 None）。"""
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3, weight=0.5))
        updated = run(mgr.edit_item(ev, "治疗药水", weight=-1))
        assert updated.weight is None

    def test_edit_not_found_returns_none(self) -> None:
        mgr, _ = make_manager()
        assert run(mgr.edit_item(FakeEvent("group:1"), "不存在", weight=1.0)) is None

    def test_edit_empty_name_returns_none(self) -> None:
        mgr, _ = make_manager()
        assert run(mgr.edit_item(FakeEvent("group:1"), "  ", weight=1.0)) is None

    def test_edit_party_uses_party_key(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "金币", 50, to_party=True, value=1.0))
        updated = run(mgr.edit_item(ev, "金币", value=2.0, in_party=True))
        assert updated.value == 2.0
        # 个人背包不受影响
        assert run(mgr.get_personal(ev)).items == []

    def test_edit_party_not_found(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "金币", 50, to_party=True))
        assert run(mgr.edit_item(ev, "不存在", note="x", in_party=True)) is None


# ---------------------------------------------------------------------------
# remove_item
# ---------------------------------------------------------------------------


class TestRemoveItem:
    def test_partial_remove(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 5))
        result = run(mgr.remove_item(ev, "治疗药水", 2))
        assert (result.removed_qty, result.remaining, result.deleted, result.found) == (
            2,
            3,
            False,
            True,
        )

    def test_remove_to_zero_deletes_entry(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3))
        result = run(mgr.remove_item(ev, "治疗药水", 3))
        assert result.deleted is True
        assert result.remaining == 0
        assert run(mgr.get_personal(ev)).items == []

    def test_remove_more_than_available_writes_nothing(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3))
        result = run(mgr.remove_item(ev, "治疗药水", 5))
        assert (result.removed_qty, result.remaining, result.deleted) == (0, 3, False)
        assert run(mgr.get_personal(ev)).items[0].qty == 3

    def test_remove_not_found(self) -> None:
        mgr, _ = make_manager()
        result = run(mgr.remove_item(FakeEvent("group:1"), "不存在", 1))
        assert result.found is False


# ---------------------------------------------------------------------------
# 流转：put / take / give
# ---------------------------------------------------------------------------


class TestTransfer:
    def test_put_moves_items_and_attrs(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3, weight=0.5, value=50, note="群体治疗"))
        result = run(mgr.put_to_party(ev, "治疗药水", 2))
        assert result.ok is True
        personal = run(mgr.get_personal(ev))
        party = run(mgr.get_party(ev))
        assert personal.items[0].qty == 1
        assert party.items[0].qty == 2
        assert (party.items[0].weight, party.items[0].value, party.items[0].note) == (
            0.5,
            50.0,
            "群体治疗",
        )

    def test_take_moves_items(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "金币", 50, to_party=True))
        result = run(mgr.take_from_party(ev, "金币", 20))
        assert result.ok is True
        assert run(mgr.get_party(ev)).items[0].qty == 30
        assert run(mgr.get_personal(ev)).items[0].qty == 20

    def test_transfer_merges_into_existing_dst_entry(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "金币", 10, to_party=True))
        run(mgr.add_item(ev, "金币", 5))
        result = run(mgr.put_to_party(ev, "金币", 5))
        assert result.ok is True
        party = run(mgr.get_party(ev))
        assert len(party.items) == 1
        assert party.items[0].qty == 15
        assert run(mgr.get_personal(ev)).items == []  # 源归零删除

    def test_transfer_insufficient_is_atomic(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3))
        result = run(mgr.put_to_party(ev, "治疗药水", 5))
        assert result.ok is False
        assert result.reason == "insufficient"
        assert result.available == 3
        # 两侧均不变
        assert run(mgr.get_personal(ev)).items[0].qty == 3
        assert run(mgr.get_party(ev)).items == []

    def test_transfer_not_found(self) -> None:
        mgr, _ = make_manager()
        result = run(mgr.put_to_party(FakeEvent("group:1"), "不存在", 1))
        assert result.ok is False
        assert result.reason == "not_found"

    def test_give_between_players(self) -> None:
        mgr, _ = make_manager()
        alice = FakeEvent("group:1", sender_id="u1", sender_name="Alice")
        bob = FakeEvent("group:1", sender_id="u2", sender_name="Bob")
        run(mgr.add_item(alice, "治疗药水", 3))
        result = run(mgr.give(alice, "u2", "治疗药水", 1))
        assert result.ok is True
        assert run(mgr.get_personal(alice)).items[0].qty == 2
        bob_inv = run(mgr.get_personal(bob))
        assert bob_inv.items[0].qty == 1
        assert bob_inv.items[0].name == "治疗药水"


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_personal(self) -> None:
        mgr, star = make_manager()
        ev = FakeEvent("group:1", sender_id="u1")
        run(mgr.add_item(ev, "治疗药水", 3))
        run(mgr.add_item(ev, "金币", 50))
        count = run(mgr.clear_personal(ev))
        assert count == 2
        assert "inventory:group:1:u1" not in star._data

    def test_clear_party(self) -> None:
        mgr, star = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "金币", 50, to_party=True))
        count = run(mgr.clear_party(ev))
        assert count == 1
        assert "inventory:party:group:1" not in star._data

    def test_clear_empty_returns_zero(self) -> None:
        mgr, _ = make_manager()
        assert run(mgr.clear_personal(FakeEvent("group:1"))) == 0


# ---------------------------------------------------------------------------
# 隔离性
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_same_user_different_origins_isolated(self) -> None:
        mgr, _ = make_manager()
        ev1 = FakeEvent("group:1", sender_id="u1")
        ev2 = FakeEvent("group:2", sender_id="u1")
        run(mgr.add_item(ev1, "治疗药水", 3))
        assert run(mgr.get_personal(ev2)).items == []

    def test_same_origin_different_users_isolated(self) -> None:
        mgr, _ = make_manager()
        alice = FakeEvent("group:1", sender_id="u1")
        bob = FakeEvent("group:1", sender_id="u2")
        run(mgr.add_item(alice, "治疗药水", 3))
        assert run(mgr.get_personal(bob)).items == []

    def test_personal_and_party_isolated(self) -> None:
        mgr, _ = make_manager()
        ev = FakeEvent("group:1")
        run(mgr.add_item(ev, "治疗药水", 3))
        assert run(mgr.get_party(ev)).items == []


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


class TestFormat:
    def test_format_inventory_with_totals(self) -> None:
        inv = Inventory(
            items=[
                ItemEntry(name="治疗药水", qty=3, weight=0.5, value=50.0, note="群体治疗"),
                ItemEntry(name="金币", qty=120),
            ]
        )
        text = InventoryManager.format_inventory(inv, "🎒 Alice 的背包")
        assert "🎒 Alice 的背包（2 种物品）：" in text
        assert "治疗药水 ×3" in text
        assert "⚖️0.5/件" in text
        assert "💰5银/件" in text  # v0.20.0 价值单位=铜币：50 铜 = 5 银
        assert "备注：群体治疗" in text
        assert "金币 ×120" in text
        assert "💰1金/件" in text  # 货币条目按面值显示
        # 金币按面值（100 铜/枚）计入总价值；无未设置项 → 不带「+」
        assert "总重量 ⚖️ 1.5" in text
        assert "总价值 💰 121金5银" in text

    def test_format_inventory_all_set_no_plus(self) -> None:
        inv = Inventory(items=[ItemEntry(name="金币", qty=10, weight=0.01, value=1.0)])
        text = InventoryManager.format_inventory(inv, "🎒 背包")
        assert "总重量 ⚖️ 0.1　" in text
        assert "+" not in text.split("——")[1]

    def test_format_item_line_minimal(self) -> None:
        line = InventoryManager.format_item_line(ItemEntry(name="绳索", qty=2))
        assert line == "绳索 ×2"

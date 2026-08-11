"""/发放 /收回 命令与背包批量功能测试（v0.42.0）。

覆盖点：
  - /发放：群聊全员可用、单件/批量、属性归属（重=/价=/备注=）、
    数字 token 作数量、空参数报用法、私聊拒绝。
  - /收回：非管理员拒绝、管理员放行、私聊拒绝、批量部分失败。
  - custom_prefix_route：.发放 / .收回 命中且 stop_event。
  - /bag add/rm/put/take 批量（数量省略、部分失败）。
  - manage_inventory items(array) 批量：add/remove/put/take、
    JSON 字符串防御、非法元素报错、remove(to_party=True) 鉴权。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin


class FakeEvent:
    """假消息事件（与 test_inventory_commands 同构）。"""

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
    """子类化插件：真实 __init__ + 内存 KV。"""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def make_plugin(config: dict | None = None) -> _MemoryPlugin:
    return _MemoryPlugin(config=config)


def ev(
    message_str: str,
    origin: str = "group:1",
    sender_id: str = "u1",
    sender_name: str = "Alice",
    private: bool = False,
    admin: bool = False,
) -> FakeEvent:
    return FakeEvent(message_str, origin, sender_id, sender_name, private, admin)


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def grant(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.grant_cmd(event)))


def revoke(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.revoke_cmd(event)))


def bag(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.bag_cmd(event)))


# ---------------------------------------------------------------------------
# /发放
# ---------------------------------------------------------------------------


class TestGrantCommand:
    def test_grant_single_item(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 治疗药水 3"))
        assert "➕ 已发放到队伍背包：治疗药水 ×3（现有 3 个）。" in out[0]
        # 写入的是队伍背包
        inv = run(p._inventory.get_party(ev("")))
        assert inv.find("治疗药水") is not None and inv.find("治疗药水").qty == 3

    def test_grant_single_default_qty(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 绳索"))
        assert "绳索 ×1" in out[0]

    def test_grant_batch_with_qty(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 治疗药水 2 火球术卷轴 1"))
        assert "批量发放：成功 2 件" in out[0]
        assert "✅ 治疗药水 ×2" in out[0]
        assert "✅ 火球术卷轴 ×1" in out[0]

    def test_grant_batch_attrs(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 治疗药水 3 重=0.5 价=1金 备注=战利品"))
        # 单件物品（含属性）走单件文案
        assert "➕ 已发放到队伍背包：治疗药水 ×3" in out[0]
        inv = run(p._inventory.get_party(ev("")))
        entry = inv.find("治疗药水")
        assert entry is not None
        assert entry.qty == 3
        assert entry.weight == 0.5
        assert entry.value == 100.0  # 1金 = 100 铜
        assert entry.note == "战利品"

    def test_grant_batch_attr_applies_to_last_item(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 治疗药水 2 价=5银 火球术卷轴"))
        inv = run(p._inventory.get_party(ev("")))
        assert inv.find("治疗药水").value == 50.0  # 5银 = 50 铜 归属治疗药水
        assert inv.find("火球术卷轴").value is None

    def test_grant_attr_before_name_rejected(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 价=5银 治疗药水"))
        assert "属性「价=5银」前缺少物品名称" in out[0]

    def test_grant_qty_before_name_rejected(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 2 治疗药水"))
        assert "数量「2」前缺少物品名称" in out[0]

    def test_grant_no_args_shows_usage(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放"))
        assert "请提供物品名称" in out[0]

    def test_grant_private_rejected(self) -> None:
        p = make_plugin()
        out = grant(p, ev("/发放 治疗药水 1", private=True))
        assert "私聊没有队伍背包" in out[0]


# ---------------------------------------------------------------------------
# /收回
# ---------------------------------------------------------------------------


class TestRevokeCommand:
    def _seed_party(self, p: _MemoryPlugin) -> None:
        grant(p, ev("/发放 治疗药水 5 火球术卷轴 2"))

    def test_revoke_non_admin_rejected(self) -> None:
        p = make_plugin()
        out = revoke(p, ev("/收回 治疗药水 1"))
        assert "你没有权限收回队伍背包物品" in out[0]

    def test_revoke_admin_single(self) -> None:
        p = make_plugin()
        self._seed_party(p)
        out = revoke(p, ev("/收回 治疗药水 2", admin=True))
        assert "➖ 已从队伍背包收回 治疗药水 ×2（剩余 3 个）。" in out[0]

    def test_revoke_admin_deplete(self) -> None:
        p = make_plugin()
        self._seed_party(p)
        out = revoke(p, ev("/收回 火球术卷轴 2", admin=True))
        assert "队伍背包中已无此物品" in out[0]

    def test_revoke_batch_partial_fail(self) -> None:
        p = make_plugin()
        self._seed_party(p)
        out = revoke(p, ev("/收回 治疗药水 2 不存在之物 1", admin=True))
        assert "批量收回：成功 1 件，失败 1 件。" in out[0]
        assert "✅ 已收回 治疗药水 ×2" in out[0]
        assert "❌ 队伍背包里没有「不存在之物」" in out[0]

    def test_revoke_private_rejected(self) -> None:
        p = make_plugin()
        out = revoke(p, ev("/收回 治疗药水 1", private=True))
        assert "私聊没有队伍背包" in out[0]

    def test_revoke_insufficient(self) -> None:
        p = make_plugin()
        self._seed_party(p)
        out = revoke(p, ev("/收回 治疗药水 99", admin=True))
        assert "队伍背包里只有 5 个「治疗药水」" in out[0]


# ---------------------------------------------------------------------------
# 自定义前缀路由
# ---------------------------------------------------------------------------


class TestGrantRevokePrefixRoute:
    def test_dot_prefix_grant(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".发放 治疗药水 3")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "已发放到队伍背包：治疗药水 ×3" in outputs[0]
        assert event.stopped is True

    def test_dot_prefix_revoke(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        grant(p, ev(".发放 治疗药水 5"))
        event = ev(".收回 治疗药水 2", admin=True)
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "已从队伍背包收回 治疗药水 ×2" in outputs[0]
        assert event.stopped is True

    def test_dot_prefix_revoke_non_admin_rejected(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".收回 治疗药水 1")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert "你没有权限收回队伍背包物品" in outputs[0]
        assert event.stopped is True


# ---------------------------------------------------------------------------
# /bag 批量
# ---------------------------------------------------------------------------


class TestBagBatch:
    def test_bag_add_batch(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag add 治疗药水 2 绳索 1"))
        assert "批量放入：成功 2 件" in out[0]
        assert "✅ 治疗药水 ×2" in out[0]

    def test_bag_add_batch_with_attrs(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag add 治疗药水 2 w=0.5 绳索 1"))
        inv = run(p._inventory.get_personal(ev("")))
        assert inv.find("治疗药水").weight == 0.5
        assert inv.find("绳索") is not None

    def test_bag_add_batch_parse_error(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag add 治疗药水 2 2"))
        assert "数量「2」重复" in out[0]

    def test_bag_rm_batch(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 5 绳索 2"))
        out = bag(p, ev("/bag rm 治疗药水 2 绳索 1"))
        assert "批量取出：成功 2 件" in out[0]
        inv = run(p._inventory.get_personal(ev("")))
        assert inv.find("治疗药水").qty == 3
        assert inv.find("绳索").qty == 1

    def test_bag_rm_batch_partial_fail(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 2"))
        out = bag(p, ev("/bag rm 治疗药水 5 不存在 1"))
        assert "批量取出：成功 0 件，失败 2 件。" in out[0]

    def test_bag_put_batch(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 100 银币 30"))
        out = bag(p, ev("/bag put 金币 50 银币 10"))
        assert "批量存入：成功 2 件" in out[0]
        inv = run(p._inventory.get_party(ev("")))
        assert inv.find("金币").qty == 50
        assert inv.find("银币").qty == 10

    def test_bag_take_batch(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 100"))
        bag(p, ev("/bag put 金币 60"))
        out = bag(p, ev("/bag take 金币 20 银币 5"))
        assert "批量取出：成功 1 件，失败 1 件。" in out[0]
        assert "❌ 队伍背包里没有「银币」" in out[0]
        inv = run(p._inventory.get_party(ev("")))
        assert inv.find("金币").qty == 40

    def test_bag_take_batch_private_rejected(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag put 金币 1 银币 1", private=True))
        assert "私聊没有队伍背包" in out[0]


# ---------------------------------------------------------------------------
# manage_inventory items 批量
# ---------------------------------------------------------------------------


class TestManageInventoryItems:
    def test_items_add_batch(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="add",
                items=[{"item": "治疗药水", "qty": 3}, {"item": "绳索", "qty": 1}],
            )
        )
        assert "批量放入：成功 2 件" in out
        inv = run(p._inventory.get_personal(ev("")))
        assert inv.find("治疗药水").qty == 3

    def test_items_add_to_party(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="add",
                to_party=True,
                items=[{"item": "金币", "qty": 50}],
            )
        )
        assert "批量放入：成功 1 件" in out
        inv = run(p._inventory.get_party(ev("")))
        assert inv.find("金币").qty == 50

    def test_items_add_with_attrs(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="add",
                items=[{"item": "治疗药水", "qty": 2, "weight": 0.5, "value": "1金", "note": "x"}],
            )
        )
        assert "批量放入：成功 1 件" in out
        inv = run(p._inventory.get_personal(ev("")))
        entry = inv.find("治疗药水")
        assert entry.value == 100.0 and entry.weight == 0.5 and entry.note == "x"

    def test_items_json_string(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="add",
                items=json.dumps([{"item": "治疗药水", "qty": 2}]),
            )
        )
        assert "批量放入：成功 1 件" in out

    def test_items_invalid_element(self) -> None:
        p = make_plugin()
        out = run(p.manage_inventory_tool(ev(""), action="add", items=[{"qty": 2}]))
        assert "缺少物品名称" in out

    def test_items_not_list(self) -> None:
        p = make_plugin()
        out = run(p.manage_inventory_tool(ev(""), action="add", items="not json"))
        assert "无法解析" in out

    def test_items_remove_batch(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 5 绳索 2"))
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="remove",
                items=[{"item": "治疗药水", "qty": 2}, {"item": "绳索", "qty": 1}],
            )
        )
        assert "批量取出：成功 2 件" in out
        inv = run(p._inventory.get_personal(ev("")))
        assert inv.find("治疗药水").qty == 3

    def test_items_remove_from_party_non_admin_rejected(self) -> None:
        p = make_plugin()
        grant(p, ev("/发放 治疗药水 5"))
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="remove",
                to_party=True,
                items=[{"item": "治疗药水", "qty": 1}],
            )
        )
        assert "你没有权限从队伍背包取出物品" in out

    def test_items_remove_from_party_admin_ok(self) -> None:
        p = make_plugin()
        grant(p, ev("/发放 治疗药水 5"))
        out = run(
            p.manage_inventory_tool(
                ev("", admin=True),
                action="remove",
                to_party=True,
                items=[{"item": "治疗药水", "qty": 1}],
            )
        )
        assert "批量取出：成功 1 件" in out

    def test_single_remove_from_party_non_admin_rejected(self) -> None:
        p = make_plugin()
        grant(p, ev("/发放 治疗药水 5"))
        out = run(
            p.manage_inventory_tool(
                ev(""), action="remove", item="治疗药水", qty=1, to_party=True
            )
        )
        assert "你没有权限从队伍背包取出物品" in out

    def test_items_put_batch(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 100 银币 30"))
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="put",
                items=[{"item": "金币", "qty": 50}, {"item": "银币", "qty": 10}],
            )
        )
        assert "批量存入：成功 2 件" in out

    def test_items_take_batch(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 100"))
        bag(p, ev("/bag put 金币 60"))
        out = run(
            p.manage_inventory_tool(
                ev(""),
                action="take",
                items=[{"item": "金币", "qty": 20}],
            )
        )
        assert "批量取出：成功 1 件" in out

    def test_items_batch_private_party_rejected(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(
                ev("", private=True),
                action="add",
                to_party=True,
                items=[{"item": "金币", "qty": 1}],
            )
        )
        assert "私聊没有队伍背包" in out

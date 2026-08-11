"""背包（/bag）命令级集成测试：不依赖真实 AstrBot，完整驱动插件指令管线。

覆盖点：
  - /bag add/rm/list 全链路输出（含 emoji 文案）。
  - /bag party / put / take 在私聊中的拒绝文案。
  - /bag party clear：群聊非管理员拒绝、管理员放行、私聊拒绝。
  - /bag give：带 At 桩成功、无 At 报用法、赠送自己拒绝。
  - custom_prefix_route：`.bag ...` 经自定义前缀路由命中且 stop_event。
  - manage_inventory_tool：各 action 输出、qty 缺省、give 不支持提示。
  - 参数解析辅助：_tokenize / _parse_add_tokens / _parse_name_qty。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from astrbot_plugin_trpg_assistant.main import (
    TrpgAssistantPlugin,
    _parse_add_tokens,
    _parse_edit_tokens,
    _parse_name_qty,
    _tokenize,
)


class At:
    """假 At 消息组件（类名必须是 At，与 aiocqhttp 组件一致）。"""

    def __init__(self, qq: str) -> None:
        self.qq = qq


class FakeMessageObj:
    def __init__(self, chain: list) -> None:
        self.message = chain


class FakeEvent:
    """假消息事件：提供背包指令处理所需的全部成员。"""

    def __init__(
        self,
        message_str: str,
        origin: str = "group:1",
        sender_id: str = "u1",
        sender_name: str = "Alice",
        private: bool = False,
        admin: bool = False,
        at_target: str | None = None,
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._private = private
        self._admin = admin
        self.stopped = False
        self.message_obj = (
            FakeMessageObj([At(at_target)]) if at_target is not None else None
        )

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
    at_target: str | None = None,
) -> FakeEvent:
    return FakeEvent(
        message_str, origin, sender_id, sender_name, private, admin, at_target
    )


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def bag(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    """驱动 /bag 指令处理器，返回全部回复文本。"""
    return run(_collect(plugin.bag_cmd(event)))


# ---------------------------------------------------------------------------
# 参数解析辅助
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_plain_tokens(self) -> None:
        assert _tokenize("add 治疗药水 3") == ["add", "治疗药水", "3"]

    def test_quoted_name_with_space(self) -> None:
        assert _tokenize('add "精灵 斗篷" 1') == ["add", "精灵 斗篷", "1"]

    def test_unmatched_quote_returns_none(self) -> None:
        assert _tokenize('add "药水 3') is None

    def test_empty(self) -> None:
        assert _tokenize("") == []


class TestParseAddTokens:
    def test_full_args(self) -> None:
        parsed = _parse_add_tokens(["治疗药水", "3", "w=0.5", "v=50", "note=群体治疗"])
        assert parsed == {
            "name": "治疗药水",
            "qty": 3,
            "weight": 0.5,
            "value": 50.0,
            "note": "群体治疗",
        }

    def test_kv_any_order(self) -> None:
        parsed = _parse_add_tokens(["w=1", "治疗药水", "v=2", "3"])
        assert parsed["name"] == "治疗药水"
        assert parsed["qty"] == 3
        assert parsed["weight"] == 1.0
        assert parsed["value"] == 2.0
        assert parsed["note"] is None

    def test_missing_qty(self) -> None:
        assert isinstance(_parse_add_tokens(["治疗药水"]), str)

    def test_missing_name(self) -> None:
        assert isinstance(_parse_add_tokens([]), str)

    def test_invalid_qty(self) -> None:
        assert "无效的数量" in _parse_add_tokens(["治疗药水", "abc"])
        assert "无效的数量" in _parse_add_tokens(["治疗药水", "0"])

    def test_invalid_weight(self) -> None:
        assert "无效的 w= 值" in _parse_add_tokens(["治疗药水", "1", "w=abc"])
        assert "无效的 w= 值" in _parse_add_tokens(["治疗药水", "1", "w=-1"])

    def test_extra_positional_rejected(self) -> None:
        assert "无法识别的参数" in _parse_add_tokens(["a", "1", "b"])


class TestParseNameQty:
    def test_name_only_default_qty(self) -> None:
        assert _parse_name_qty(["治疗药水"]) == ("治疗药水", 1)

    def test_name_with_qty(self) -> None:
        assert _parse_name_qty(["治疗药水", "2"]) == ("治疗药水", 2)

    def test_empty(self) -> None:
        assert isinstance(_parse_name_qty([]), str)

    def test_invalid_qty(self) -> None:
        assert "无效的数量" in _parse_name_qty(["治疗药水", "x"])


# ---------------------------------------------------------------------------
# /bag add / rm / list 全链路
# ---------------------------------------------------------------------------


class TestBagBasicCommands:
    def test_empty_bag_hint(self) -> None:
        p = make_plugin()
        outputs = bag(p, ev("/bag"))
        assert "背包是空的" in outputs[0]
        assert "/bag add" in outputs[0]

    def test_add_then_list(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag add 治疗药水 3 w=0.5 v=50 note=群体治疗"))
        assert "➕ 已放入 **治疗药水** ×3（现有 3 个）。" in out[0]

        out = bag(p, ev("/bag"))
        assert "🎒 Alice 的背包（1 种物品）：" in out[0]
        assert "**治疗药水** ×3" in out[0]
        assert "总重量 ⚖️ 1.5" in out[0]
        assert "总价值 💰 1金5银" in out[0]  # v0.20.0 价值单位=铜币：150 铜

    def test_add_merges_and_reports_total(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3"))
        out = bag(p, ev("/bag add 治疗药水 2"))
        assert "现有 5 个" in out[0]

    def test_add_quoted_name_with_space(self) -> None:
        p = make_plugin()
        out = bag(p, ev('/bag add "精灵 斗篷" 1'))
        assert "**精灵 斗篷** ×1" in out[0]

    def test_add_missing_qty_shows_usage(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag add 治疗药水"))
        assert "用法：/bag add" in out[0]

    def test_rm_partial_and_depleted(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3"))
        out = bag(p, ev("/bag rm 治疗药水 2"))
        assert "➖ 已取出 **治疗药水** ×2（剩余 1 个）。" in out[0]
        out = bag(p, ev("/bag rm 治疗药水"))
        assert "背包中已无此物品" in out[0]

    def test_rm_insufficient(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3"))
        out = bag(p, ev("/bag rm 治疗药水 5"))
        assert "只有 3 个" in out[0]

    def test_rm_not_found(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag rm 不存在"))
        assert "背包里没有「不存在」" in out[0]

    def test_clear_personal(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3"))
        out = bag(p, ev("/bag clear"))
        assert "已清空背包，共移除 1 种物品" in out[0]
        out = bag(p, ev("/bag clear"))
        assert "本来就是空的" in out[0]

    def test_list_other_player_readonly(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3", sender_id="u1"))
        out = bag(p, ev("/bag list @Bob", sender_id="u2", at_target="u1"))
        assert "玩家 u1 的背包" in out[0]
        assert "**治疗药水** ×3" in out[0]

    def test_list_at_unresolved(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag list @某人"))  # 无 At 组件
        assert "未能识别 @目标" in out[0]

    def test_help_on_unknown_subcommand(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag xyz"))
        assert "背包指令用法" in out[0]


# ---------------------------------------------------------------------------
# 队伍背包与私聊边界
# ---------------------------------------------------------------------------


class TestPartyBag:
    def test_party_put_take_flow(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 100"))
        out = bag(p, ev("/bag put 金币 50"))
        assert "📦 已将 **金币** ×50 存入队伍背包。" in out[0]

        out = bag(p, ev("/bag party"))
        assert "📦 队伍背包（1 种物品）：" in out[0]
        assert "**金币** ×50" in out[0]

        out = bag(p, ev("/bag take 金币 20"))
        assert "📦 已从队伍背包取出 **金币** ×20。" in out[0]

        out = bag(p, ev("/bag take 金币 100"))
        assert "队伍背包里只有 30 个" in out[0]

    def test_party_shared_across_users(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 10", sender_id="u1"))
        bag(p, ev("/bag put 金币 10", sender_id="u1"))
        out = bag(p, ev("/bag take 金币 5", sender_id="u2", sender_name="Bob"))
        assert "已从队伍背包取出 **金币** ×5" in out[0]

    def test_private_chat_rejects_party(self) -> None:
        p = make_plugin()
        for msg in ("/bag party", "/bag put 金币 1", "/bag take 金币 1"):
            out = bag(p, ev(msg, private=True))
            assert "私聊没有队伍背包" in out[0]

    def test_private_chat_rejects_give(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag give 治疗药水", private=True))
        assert "私聊中没有其他玩家可以赠送" in out[0]

    def test_private_chat_rejects_list_at(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag list @某人", private=True))
        assert "私聊中只能查看自己的背包" in out[0]

    def test_party_clear_denied_for_non_admin(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 10"))
        bag(p, ev("/bag put 金币 10"))
        out = bag(p, ev("/bag party clear", admin=False))
        assert "你没有权限清空队伍背包" in out[0]
        # 未被清空
        out = bag(p, ev("/bag party"))
        assert "**金币** ×10" in out[0]

    def test_party_clear_allowed_for_admin(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 10"))
        bag(p, ev("/bag put 金币 10"))
        out = bag(p, ev("/bag party clear", admin=True))
        assert "已清空队伍背包，共移除 1 种物品" in out[0]

    def test_party_clear_private_rejected(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag party clear", private=True))
        assert "私聊没有队伍背包" in out[0]


# ---------------------------------------------------------------------------
# /bag give
# ---------------------------------------------------------------------------


class TestBagGive:
    def test_give_success(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3", sender_id="u1"))
        # 真实平台中 @ 是独立消息组件，message_str 不含 @文本
        out = bag(p, ev("/bag give 治疗药水 1", sender_id="u1", at_target="u2"))
        assert "🎁 已将 **治疗药水** ×1 交给 u2。" in out[0]
        # 双方数量正确
        out = bag(p, ev("/bag", sender_id="u1"))
        assert "**治疗药水** ×2" in out[0]
        out = bag(p, ev("/bag", sender_id="u2", sender_name="Bob"))
        assert "**治疗药水** ×1" in out[0]

    def test_give_without_at_shows_usage(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag give 治疗药水 1"))
        assert "用法：/bag give @某人" in out[0]

    def test_give_to_self_rejected(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3", sender_id="u1"))
        out = bag(p, ev("/bag give 治疗药水 1", sender_id="u1", at_target="u1"))
        assert "不能赠送给自己" in out[0]

    def test_give_with_at_text_tolerated(self) -> None:
        """部分平台 message_str 保留 @文本：应被过滤，不影响解析。"""
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3", sender_id="u1"))
        out = bag(p, ev("/bag give @Bob 治疗药水 1", sender_id="u1", at_target="u2"))
        assert "🎁 已将 **治疗药水** ×1 交给 u2。" in out[0]

    def test_give_insufficient(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 1", sender_id="u1"))
        out = bag(p, ev("/bag give 治疗药水 5", sender_id="u1", at_target="u2"))
        assert "只有 1 个" in out[0]


# ---------------------------------------------------------------------------
# 自定义前缀路由
# ---------------------------------------------------------------------------


class TestCustomPrefixRoute:
    def test_dot_prefix_bag_add(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".bag add 治疗药水 3")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "➕ 已放入 **治疗药水** ×3" in outputs[0]
        assert event.stopped is True

    def test_dot_prefix_bag_zh_alias(self) -> None:
        """中文别名 .背包 经自定义前缀路由命中，与 .bag 等价。"""
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".背包 add 治疗药水 3")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "➕ 已放入 **治疗药水** ×3" in outputs[0]
        assert event.stopped is True

    def test_dot_prefix_bag_view_uses_dot_in_hint(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".bag")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert ".bag add" in outputs[0]
        assert event.stopped is True

    def test_unrelated_message_not_routed(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".baggage 不是指令")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert outputs == []
        assert event.stopped is False


# ---------------------------------------------------------------------------
# manage_inventory LLM 工具
# ---------------------------------------------------------------------------


class TestManageInventoryTool:
    def test_add_and_list(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(
                ev(""), action="add", item="治疗药水", qty=3, weight=0.5, value=50
            )
        )
        assert "➕ 已放入 **治疗药水** ×3" in out
        out = run(p.manage_inventory_tool(ev(""), action="list"))
        assert "**治疗药水** ×3" in out

    def test_add_default_qty(self) -> None:
        p = make_plugin()
        out = run(p.manage_inventory_tool(ev(""), action="add", item="绳索"))
        assert "×1" in out

    def test_add_to_party(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(ev(""), action="add", item="金币", qty=50, to_party=True)
        )
        assert "队伍背包" in out
        out = run(p.manage_inventory_tool(ev(""), action="list", to_party=True))
        assert "📦 队伍背包（1 种物品）" in out

    def test_remove_and_deplete(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="治疗药水", qty=2))
        out = run(p.manage_inventory_tool(ev(""), action="remove", item="治疗药水", qty=2))
        assert "已无此物品" in out

    def test_put_take(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="金币", qty=10))
        out = run(p.manage_inventory_tool(ev(""), action="put", item="金币", qty=4))
        assert "存入队伍背包" in out
        out = run(p.manage_inventory_tool(ev(""), action="take", item="金币", qty=1))
        assert "已从队伍背包取出" in out

    def test_clear_personal_only(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="绳索"))
        out = run(p.manage_inventory_tool(ev(""), action="clear"))
        assert "已清空背包" in out

    def test_private_chat_rejects_party_ops(self) -> None:
        p = make_plugin()
        out = run(
            p.manage_inventory_tool(ev("", private=True), action="add", item="金币", to_party=True)
        )
        assert "私聊没有队伍背包" in out

    def test_unknown_action_mentions_no_give(self) -> None:
        p = make_plugin()
        out = run(p.manage_inventory_tool(ev(""), action="give", item="治疗药水"))
        assert "未知的 action" in out
        assert ".bag give" in out


# ---------------------------------------------------------------------------
# _parse_edit_tokens
# ---------------------------------------------------------------------------


class TestParseEditTokens:
    def test_full_edit(self) -> None:
        parsed = _parse_edit_tokens(["治疗药水", "w=0.8", "v=60", "note=已开封"])
        assert parsed["name"] == "治疗药水"
        assert parsed["weight"] == 0.8
        assert parsed["value"] == 60.0
        assert parsed["note"] == "已开封"

    def test_partial_fields_only(self) -> None:
        parsed = _parse_edit_tokens(["治疗药水", "v=60"])
        assert parsed["name"] == "治疗药水"
        assert "weight" not in parsed  # 未提供 = 不修改
        assert parsed["value"] == 60.0

    def test_dash_means_clear(self) -> None:
        parsed = _parse_edit_tokens(["治疗药水", "w=-", "note=-"])
        assert parsed["weight"] is None
        assert parsed["note"] is None

    def test_no_fields_error(self) -> None:
        assert "请至少提供一个" in _parse_edit_tokens(["治疗药水"])

    def test_no_name_error(self) -> None:
        assert "请提供物品名称" in _parse_edit_tokens(["w=1"])

    def test_extra_positional_error(self) -> None:
        assert "无法识别的参数" in _parse_edit_tokens(["a", "b", "w=1"])

    def test_invalid_weight_error(self) -> None:
        assert "无效的 w= 值" in _parse_edit_tokens(["治疗药水", "w=abc"])
        assert "无效的 w= 值" in _parse_edit_tokens(["治疗药水", "w=-2"])


# ---------------------------------------------------------------------------
# /bag edit 命令
# ---------------------------------------------------------------------------


class TestBagEdit:
    def test_edit_flow(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3 w=0.5 v=50 note=旧备注"))
        out = bag(p, ev("/bag edit 治疗药水 w=0.8 note=已开封"))
        assert "✏️ 已更新 **治疗药水** ×3" in out[0]
        assert "⚖️0.8/件" in out[0]
        assert "备注：已开封" in out[0]
        assert "💰5银/件" in out[0]  # 未修改的价值保持（v0.20.0 单位=铜币：50 铜）
        # 数量不受影响
        out = bag(p, ev("/bag"))
        assert "**治疗药水** ×3" in out[0]

    def test_edit_clear_weight(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 3 w=0.5 v=50"))
        out = bag(p, ev("/bag edit 治疗药水 w=-"))
        assert "已更新" in out[0]
        out = bag(p, ev("/bag"))
        # 重量清除后总计出现「+」（至少）
        assert "总重量 ⚖️ 0+" in out[0]

    def test_edit_not_found(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag edit 不存在 w=1"))
        assert "背包里没有「不存在」" in out[0]

    def test_edit_no_attr_shows_usage(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 治疗药水 1"))
        out = bag(p, ev("/bag edit 治疗药水"))
        assert "请至少提供一个" in out[0]
        assert "用法：/bag edit" in out[0]

    def test_edit_party_flow(self) -> None:
        p = make_plugin()
        bag(p, ev("/bag add 金币 50"))
        bag(p, ev("/bag put 金币 50"))
        out = bag(p, ev("/bag party edit 金币 v=2"))
        assert "✏️ 已更新 **金币** ×50" in out[0]
        # v0.20.0：货币条目价值按面值显示（金币 = 1金），不随 v= 参数变
        assert "💰1金/件" in out[0]
        out = bag(p, ev("/bag party"))
        assert "💰1金/件" in out[0]

    def test_edit_party_private_rejected(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag party edit 金币 v=2", private=True))
        assert "私聊没有队伍背包" in out[0]

    def test_edit_party_not_found(self) -> None:
        p = make_plugin()
        out = bag(p, ev("/bag party edit 不存在 v=2"))
        assert "队伍背包里没有「不存在」" in out[0]


# ---------------------------------------------------------------------------
# manage_inventory action="edit"
# ---------------------------------------------------------------------------


class TestManageInventoryEdit:
    def test_edit_override(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="治疗药水", qty=3, weight=0.5))
        out = run(p.manage_inventory_tool(ev(""), action="edit", item="治疗药水", weight=0.8))
        assert "✏️ 已更新 **治疗药水** ×3" in out
        assert "⚖️0.8/件" in out

    def test_edit_clear_with_negative_one(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="治疗药水", qty=1, weight=0.5))
        out = run(p.manage_inventory_tool(ev(""), action="edit", item="治疗药水", weight=-1))
        assert "已更新" in out
        assert "⚖️" not in out  # 重量已清除，不再显示

    def test_edit_clear_note_with_dash(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="治疗药水", qty=1, note="旧备注"))
        out = run(p.manage_inventory_tool(ev(""), action="edit", item="治疗药水", note="-"))
        assert "已更新" in out
        assert "备注" not in out

    def test_edit_party(self) -> None:
        p = make_plugin()
        run(p.manage_inventory_tool(ev(""), action="add", item="金币", qty=10, to_party=True, value=1))
        out = run(p.manage_inventory_tool(ev(""), action="edit", item="金币", value=2, to_party=True))
        assert "✏️ 已更新 **金币** ×10" in out
        # v0.20.0：货币条目价值按面值显示（金币 = 1金）
        assert "💰1金/件" in out

    def test_edit_unknown_item(self) -> None:
        p = make_plugin()
        out = run(p.manage_inventory_tool(ev(""), action="edit", item="不存在", weight=1))
        assert "背包里没有「不存在」" in out

    def test_edit_missing_item(self) -> None:
        p = make_plugin()
        out = run(p.manage_inventory_tool(ev(""), action="edit", weight=1))
        assert "请提供物品名称" in out

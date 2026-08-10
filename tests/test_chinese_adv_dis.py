"""中文「优势/劣势」命令级测试：覆盖 /r、自定义前缀、roll_dice 工具三入口与边界报错。

v0.38.0 新增：`d20优势` 系列紧贴后缀语法在 _do_roll 命令层映射为引擎
adv/dis 语法糖（dice_parser 上游只读，不直接加中文语法）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator

from astrbot_plugin_trpg_assistant import dice_roller  # noqa: F401  （fixture 注入用）
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin


class FakeEvent:
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


def r_cmd(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.roll_cmd(event)))


def extract_total(out: str) -> int:
    """从格式化输出末尾提取总计，如 'd20adv: [18] = 18' → 18。"""
    m = re.search(r"=\s*(\d+)\s*$", out.strip())
    assert m, f"无法从输出提取总计: {out!r}"
    return int(m.group(1))


class TestZhAdvDisRollCmd:
    def test_adv_takes_max(self, make_rng) -> None:
        p = make_plugin()
        make_rng([18, 5])
        out = r_cmd(p, ev("/r d20优势"))
        assert "d20adv" in out[0]
        assert extract_total(out[0]) == 18

    def test_dis_takes_min(self, make_rng) -> None:
        p = make_plugin()
        make_rng([18, 5])
        out = r_cmd(p, ev("/r d20劣势"))
        assert "d20dis" in out[0]
        assert extract_total(out[0]) == 5

    def test_adv_plus_flat(self, make_rng) -> None:
        p = make_plugin()
        make_rng([12, 6])
        out = r_cmd(p, ev("/r d20优势+2"))
        assert "d20adv" in out[0]
        assert extract_total(out[0]) == 14

    def test_adv_plus_dice_group(self, make_rng) -> None:
        p = make_plugin()
        make_rng([12, 6, 4])
        out = r_cmd(p, ev("/r d20优势+1d4"))
        assert "d20adv" in out[0]
        assert extract_total(out[0]) == 16

    def test_history_records_original_expr(self, make_rng) -> None:
        p = make_plugin()
        make_rng([18, 5])
        r_cmd(p, ev("/r d20优势"))
        hist = p._kv["history:group:1"]
        assert hist[-1]["expr"] == "d20优势"

    def test_adv_in_middle_errors(self, make_rng) -> None:
        p = make_plugin()
        make_rng([12, 6])
        out = r_cmd(p, ev("/r d20+2优势"))
        assert out[0].startswith("解析错误:")

    def test_adv_prefix_errors(self, make_rng) -> None:
        p = make_plugin()
        make_rng([12, 6])
        out = r_cmd(p, ev("/r 优势d20"))
        assert out[0].startswith("解析错误:")

    def test_bare_adv_errors(self, make_rng) -> None:
        p = make_plugin()
        make_rng([12])
        out = r_cmd(p, ev("/r 优势"))
        assert out[0].startswith("解析错误:")

    def test_spaced_adv_errors_not_silent_label(self, make_rng) -> None:
        """`/r d20 优势` 必须报错（必须紧贴），不能静默当普通 d20 带标签。"""
        p = make_plugin()
        make_rng([7])
        out = r_cmd(p, ev("/r d20 优势"))
        assert out[0].startswith("解析错误:")

    def test_label_with_adv_word_not_mangled(self, make_rng) -> None:
        """标签内出现「优势」二字（如 战斗优势）不应被误替换或误报。"""
        p = make_plugin()
        make_rng([7])
        out = r_cmd(p, ev("/r d20 战斗优势"))
        assert not out[0].startswith("解析错误:")
        assert "战斗优势" in out[0]
        assert extract_total(out[0]) == 7

    def test_no_active_card_goes_to_do_roll(self, make_rng) -> None:
        """无活跃卡时 `/r d20优势` 走普通掷骰而非角色卡联动。"""
        p = make_plugin()
        make_rng([18, 5])
        out = r_cmd(p, ev("/r d20优势"))
        assert "d20adv" in out[0]
        assert extract_total(out[0]) == 18


class TestZhAdvDisOtherEntries:
    def test_custom_prefix_route(self, make_rng) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        make_rng([18, 5])
        e = ev(".r d20优势")
        out = run(_collect(p.custom_prefix_route(e)))
        assert "d20adv" in out[0]
        assert extract_total(out[0]) == 18
        assert e.stopped

    def test_llm_tool(self, make_rng) -> None:
        p = make_plugin()
        make_rng([18, 5])
        out = run(p.roll_dice_tool(ev(""), expression="d20优势"))
        assert "d20adv" in out
        assert extract_total(out) == 18

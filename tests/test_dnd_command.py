"""`/dnd N` 命令级测试：DND 5e 属性随机生成（每组 6 次 4d6kh3）。

v0.38.0 新增：N=组数（默认 1，上限 20），每组输出一行 6 个属性值。
"""

from __future__ import annotations

import asyncio
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


def dnd_cmd(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.dnd_cmd(event)))


class TestDndCommand:
    def test_default_one_group(self, make_rng) -> None:
        p = make_plugin()
        # 6 次 4d6kh3 × 4 骰 = 24 次 randint；[6,6,6,1] → 取 6+6+6 = 18
        make_rng([6, 6, 6, 1] * 6)
        out = dnd_cmd(p, ev("/dnd"))
        assert out == ["第1组: 18 18 18 18 18 18"]

    def test_two_groups_and_randint_count(self, make_rng) -> None:
        p = make_plugin()
        rng = make_rng([6, 6, 6, 1] * 12)  # 2 组 × 24 = 48 次 randint
        out = dnd_cmd(p, ev("/dnd 2"))
        lines = out[0].splitlines()
        assert lines[0] == "第1组: 18 18 18 18 18 18"
        assert lines[1] == "第2组: 18 18 18 18 18 18"
        assert "4d6kh3" in lines[2]
        assert rng.randint_calls == 48

    def test_deterministic_scores(self, make_rng) -> None:
        p = make_plugin()
        # 每组 6 次掷骰的 4d6 序列 → 期望合计
        make_rng(
            [6, 5, 4, 1,   # 15
             1, 2, 3, 4,   # 9
             6, 6, 6, 6,   # 18
             3, 3, 3, 3,   # 9
             2, 2, 2, 2,   # 6
             5, 5, 5, 5]   # 15
        )
        out = dnd_cmd(p, ev("/dnd"))
        assert out == ["第1组: 15 9 18 9 6 15"]

    def test_no_card_needed(self, make_rng) -> None:
        p = make_plugin()
        make_rng([6, 6, 6, 1] * 6)
        out = dnd_cmd(p, ev("/dnd 1"))
        assert "第1组:" in out[0]

    def test_history_records_command_form(self, make_rng) -> None:
        p = make_plugin()
        make_rng([6, 6, 6, 1] * 6)
        dnd_cmd(p, ev("/dnd 1"))
        hist = p._kv["history:group:1"]
        assert hist[-1]["expr"] == "dnd 1"

    def test_usage_error(self, make_rng) -> None:
        p = make_plugin()
        make_rng([6, 6, 6, 1] * 6)
        out = dnd_cmd(p, ev("/dnd abc"))
        assert "用法" in out[0]
        out = dnd_cmd(p, ev("/dnd 2.5"))
        assert "用法" in out[0]

    def test_zero_rejected(self, make_rng) -> None:
        p = make_plugin()
        make_rng([6, 6, 6, 1] * 6)
        out = dnd_cmd(p, ev("/dnd 0"))
        assert "至少为 1" in out[0]

    def test_over_limit_rejected(self, make_rng) -> None:
        p = make_plugin()
        make_rng([6, 6, 6, 1] * 6)
        out = dnd_cmd(p, ev("/dnd 21"))
        assert "不能超过 20" in out[0]

    def test_error_does_not_write_history(self, make_rng) -> None:
        p = make_plugin()
        make_rng([6, 6, 6, 1] * 6)
        dnd_cmd(p, ev("/dnd abc"))
        assert "history:group:1" not in p._kv

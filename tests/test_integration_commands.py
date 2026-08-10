"""命令级集成测试：不依赖真实 AstrBot，完整驱动插件指令管线。

在单元测试（纯逻辑）之上，本套件将 TrpgAssistantPlugin 真正实例化，
以「内存 KV 假 Star + 假消息事件」模拟 AstrBot 消息管线，
直接消费 ri_cmd / init_cmd / manage_initiative_tool 的输出，
覆盖：指令参数接线、权限判定、会话隔离、LLM 工具调用。

无法在此环境验证（需真实 AstrBot 实例）：平台消息路由与 `/` 前缀拦截、
插件装载器、KV 跨重启持久化。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest

from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin


class MemoryStar:
    """内存 KV 实现的假 Star，替代真实 AstrBot 的 KV 存储。"""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._data[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._data.pop(key, None)


class FakeEvent:
    """假消息事件：提供插件指令处理所需的全部成员。"""

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


@pytest.fixture
def plugin() -> TrpgAssistantPlugin:
    """构造可用插件实例（确定性 d20=12、内存 KV）。"""
    return make_plugin()


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
    """构造可用插件实例，并将先攻掷骰固定为 12（确定性）。"""
    p = _MemoryPlugin(config=config)
    p._roll_d20 = lambda: 12  # type: ignore[method-assign]
    return p


async def run_cmd(
    plugin: _MemoryPlugin, event: FakeEvent
) -> list[str]:
    """驱动一个指令处理器，返回全部回复文本。"""

    outputs: list[str] = []
    # 根据消息首 token 分派到对应处理器（模拟 AstrBot 的命令路由）。
    token = event.message_str.strip().split(None, 1)[0].lower()
    if token in ("/ri", "ri"):
        gen: AsyncGenerator = plugin.ri_cmd(event)
    elif token in ("/init", "init", "/initiative", "initiative"):
        gen = plugin.init_cmd(event)
    else:  # pragma: no cover
        raise AssertionError(f"未知指令: {token}")
    async for msg in gen:
        outputs.append(msg)
    return outputs


def ev(
    message_str: str,
    origin: str = "group:1",
    sender_id: str = "u1",
    sender_name: str = "Alice",
    private: bool = False,
    admin: bool = False,
) -> FakeEvent:
    return FakeEvent(message_str, origin, sender_id, sender_name, private, admin)


# ---------------------------------------------------------------------------
# /ri 指令：三种录入方式
# ---------------------------------------------------------------------------


class TestRiCommand:
    def test_roll_for_self(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/ri")))
        assert len(outputs) == 1
        assert "d20 → **12**" in outputs[0]  # 固定骰 12，无调整值
        state = asyncio_run(p._initiative.get_state(ev("/ri")))
        assert len(state.entries) == 1
        assert state.entries[0].name == "Alice"
        assert state.entries[0].value == 12
        assert state.entries[0].is_fixed is False

    def test_roll_with_modifier(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/ri +3")))
        assert "d20+3 → **15**" in outputs[0]

    def test_roll_with_modifier_and_name(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/ri +2 食人魔")))
        assert "食人魔" in outputs[0]
        assert "d20+2 → **14**" in outputs[0]
        state = asyncio_run(p._initiative.get_state(ev("/ri")))
        assert state.entries[0].name == "食人魔"
        assert state.entries[0].modifier == 2

    def test_fixed_value_with_name(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/ri 15 哥布林甲")))
        assert "已录入先攻" in outputs[0]
        assert "15" in outputs[0]
        state = asyncio_run(p._initiative.get_state(ev("/ri")))
        assert state.entries[0].name == "哥布林甲"
        assert state.entries[0].value == 15
        assert state.entries[0].is_fixed is True

    def test_invalid_argument_shows_usage(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/ri 瞎写的")))
        assert "用法" in outputs[0]
        state = asyncio_run(p._initiative.get_state(ev("/ri")))
        assert state.entries == []


# ---------------------------------------------------------------------------
# /init 指令：查看 / 推进 / 权限
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_list_empty(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/init")))
        assert "为空" in outputs[0]

    def test_list_with_entries_and_marker(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))
        asyncio_run(run_cmd(p, ev("/ri 7 哥布林")))
        asyncio_run(run_cmd(p, ev("/init end")))  # 开始战斗，current=食人魔

        outputs = asyncio_run(run_cmd(p, ev("/init")))
        text = outputs[0]
        assert "先攻列表" in text
        assert "▶" in text  # 当前行动者标记
        assert "第 1 轮" in text
        # 降序：食人魔在哥布林之前
        assert text.index("食人魔") < text.index("哥布林")

    def test_end_advances_and_wraps_round(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))
        asyncio_run(run_cmd(p, ev("/ri 7 哥布林")))

        out1 = asyncio_run(run_cmd(p, ev("/init end")))
        assert "战斗开始" in out1[0]
        out2 = asyncio_run(run_cmd(p, ev("/init end")))
        assert "哥布林" in out2[0]
        out3 = asyncio_run(run_cmd(p, ev("/init end")))
        assert "第 2 轮开始" in out3[0]
        assert "食人魔" in out3[0]

    def test_del_non_admin_denied_in_group(self) -> None:
        # 群聊 + 白名单关闭 → 回退管理员判定，非管理员被拒绝
        p = make_plugin()  # enable_whitelist 默认 False
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))

        outputs = asyncio_run(
            run_cmd(p, ev("/init del 食人魔", admin=False))
        )
        assert "没有权限" in outputs[0]
        state = asyncio_run(p._initiative.get_state(ev("/init")))
        assert len(state.entries) == 1  # 未被删除

    def test_del_admin_allowed(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))
        asyncio_run(run_cmd(p, ev("/ri 7 哥布林")))

        outputs = asyncio_run(
            run_cmd(p, ev("/init del 食人魔", admin=True))
        )
        assert "已移除 食人魔" in outputs[0]
        state = asyncio_run(p._initiative.get_state(ev("/init")))
        assert [e.name for e in state.entries] == ["哥布林"]

    def test_del_allowed_in_private_chat(self) -> None:
        # 私聊任何人均可执行破坏性指令（仅影响自身数据）
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔", private=True)))

        outputs = asyncio_run(
            run_cmd(p, ev("/init del 食人魔", private=True, admin=False))
        )
        assert "已移除" in outputs[0]

    def test_del_not_found(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))
        outputs = asyncio_run(run_cmd(p, ev("/init del 不存在", admin=True)))
        assert "未找到" in outputs[0]

    def test_clr_non_admin_denied(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))
        outputs = asyncio_run(run_cmd(p, ev("/init clr", admin=False)))
        assert "没有权限" in outputs[0]

    def test_clr_admin_allowed(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔")))
        outputs = asyncio_run(run_cmd(p, ev("/init clr", admin=True)))
        assert "已清空" in outputs[0]
        state = asyncio_run(p._initiative.get_state(ev("/init")))
        assert state.entries == []

    def test_unknown_subcommand_shows_usage(self) -> None:
        p = make_plugin()
        outputs = asyncio_run(run_cmd(p, ev("/init 乱七八糟")))
        assert "用法" in outputs[0]


# ---------------------------------------------------------------------------
# 会话隔离（群聊 A / 群聊 B）
# ---------------------------------------------------------------------------


class TestSessionIsolationIntegration:
    def test_two_groups_do_not_interfere(self) -> None:
        p = make_plugin()
        asyncio_run(run_cmd(p, ev("/ri 18 食人魔", origin="group:1")))
        asyncio_run(run_cmd(p, ev("/ri 7 哥布林", origin="group:2")))

        state_a = asyncio_run(
            p._initiative.get_state(ev("/init", origin="group:1"))
        )
        state_b = asyncio_run(
            p._initiative.get_state(ev("/init", origin="group:2"))
        )
        assert [e.name for e in state_a.entries] == ["食人魔"]
        assert [e.name for e in state_b.entries] == ["哥布林"]

        # 群 A 清空不影响群 B
        asyncio_run(run_cmd(p, ev("/init clr", origin="group:1", admin=True)))
        state_b = asyncio_run(
            p._initiative.get_state(ev("/init", origin="group:2"))
        )
        assert [e.name for e in state_b.entries] == ["哥布林"]


# ---------------------------------------------------------------------------
# LLM 函数工具 manage_initiative
# ---------------------------------------------------------------------------


class TestLlmTool:
    def _tool(self, p: _MemoryPlugin, **kwargs) -> str:
        return asyncio_run(
            p.manage_initiative_tool(ev("/", admin=True), **kwargs)
        )

    def test_tool_roll_and_list(self) -> None:
        p = make_plugin()
        text = self._tool(p, action="roll", name="食人魔", modifier=2)
        assert "食人魔" in text
        assert "d20+2 → **14**" in text

        text = self._tool(p, action="list")
        assert "食人魔" in text
        assert "先攻 14" in text

    def test_tool_fixed(self) -> None:
        p = make_plugin()
        text = self._tool(p, action="fixed", name="哥布林甲", value=15)
        assert "已录入" in text
        assert "15" in text

    def test_tool_end_starts_combat(self) -> None:
        p = make_plugin()
        self._tool(p, action="roll", name="食人魔", modifier=0)
        text = self._tool(p, action="end")
        assert "战斗开始" in text
        assert "食人魔" in text

    def test_tool_remove_and_clear(self) -> None:
        p = make_plugin()
        self._tool(p, action="roll", name="食人魔")
        self._tool(p, action="roll", name="哥布林")
        text = self._tool(p, action="remove", name="食人魔")
        assert "已移除" in text

        text = self._tool(p, action="clear")
        assert "已清空" in text

    def test_tool_unknown_action(self) -> None:
        p = make_plugin()
        text = self._tool(p, action="hack")
        assert "未知的 action" in text

    def test_tool_fixed_without_value(self) -> None:
        p = make_plugin()
        text = self._tool(p, action="fixed", name="哥布林甲")
        assert "value 参数" in text


def asyncio_run(coro):
    """同步运行协程的辅助（与仓库现有测试风格一致）。"""
    return asyncio.run(coro)

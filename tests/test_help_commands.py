"""/帮助 指令集成测试：群聊玩家可查询全部命令。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from astrbot_plugin_trpg_assistant.main import (
    _format_help_overview,
    _format_help_topic,
    TrpgAssistantPlugin,
)


class FakeEvent:
    def __init__(self, message_str: str, origin: str = "group:1") -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self._sender_id = "u1"
        self._sender_name = "Alice"
        self.stopped = False

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_private_chat(self) -> bool:
        return False

    def is_admin(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self) -> None:
        super().__init__(context=None, config=None)
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def collect(gen: AsyncGenerator) -> list[str]:
    return run(_collect(gen))


def make_plugin() -> _MemoryPlugin:
    return _MemoryPlugin()


# ---------------------------------------------------------------------------
# 格式化纯函数
# ---------------------------------------------------------------------------


def test_overview_contains_all_groups() -> None:
    text = _format_help_overview()
    for topic in ("知识库", "先攻", "背包", "骰子", "历史"):
        assert topic in text
    assert "/查法术" in text and "/查怪" in text and "/kb" in text
    assert "/帮助 <组名>" in text


def test_topic_detail() -> None:
    text = _format_help_topic("知识库")
    assert "/查法术 <名称>" in text
    assert "/查职业 <职业> [子职|特性]" in text
    text = _format_help_topic("背包")
    assert "/bag add <名称> <数量>" in text
    assert "/bag party clear" in text


def test_topic_detail_with_custom_prefix() -> None:
    text = _format_help_topic("知识库", display_prefix=".")
    assert ".查法术 <名称>" in text


# ---------------------------------------------------------------------------
# 命令链路
# ---------------------------------------------------------------------------


def test_help_command_overview() -> None:
    p = make_plugin()
    msgs = collect(p.help_cmd(FakeEvent("/帮助")))
    assert "跑团助手指令大全" in msgs[0]
    assert "知识库：/查法术" in msgs[0]


def test_help_command_topic() -> None:
    p = make_plugin()
    msgs = collect(p.help_cmd(FakeEvent("/帮助 知识库")))
    assert "知识库 指令详解" in msgs[0]
    assert "/查法术 <名称>" in msgs[0]
    msgs = collect(p.help_cmd(FakeEvent("/帮助 背包")))
    assert "背包 指令详解" in msgs[0]
    assert "/bag add" in msgs[0]


def test_help_command_topic_alias() -> None:
    p = make_plugin()
    msgs = collect(p.help_cmd(FakeEvent("/帮助 kb")))
    assert "知识库 指令详解" in msgs[0]


def test_help_command_unknown_topic_falls_back_to_overview() -> None:
    p = make_plugin()
    msgs = collect(p.help_cmd(FakeEvent("/帮助 不存在的组")))
    assert "跑团助手指令大全" in msgs[0]


def test_help_command_via_custom_prefix() -> None:
    p = make_plugin()
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = FakeEvent(".帮助 知识库", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs and "知识库 指令详解" in msgs[0]
    assert e.stopped


def test_help_command_via_custom_prefix_aliases() -> None:
    """.commands / .cmds 别名同样经自定义前缀路由命中（v0.41.2）。"""
    p = make_plugin()
    run(p.put_kv_data("custom_prefix:group:9", "."))
    for cmd in (".commands", ".cmds"):
        e = FakeEvent(cmd, origin="group:9")
        msgs = collect(p.custom_prefix_route(e))
        assert msgs and "跑团助手指令大全" in msgs[0], cmd
        assert e.stopped, cmd


def test_help_does_not_interfere_with_kb_commands() -> None:
    """帮助命令不应吞掉 /查法术 等其它指令（custom_prefix 分支顺序正确）。"""
    p = make_plugin()
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = FakeEvent(".查法术", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    # /查法术 无参数 → 命中知识库分支，输出用法而非帮助
    assert msgs and "用法" in msgs[0]
    assert e.stopped

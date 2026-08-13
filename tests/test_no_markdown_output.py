"""纯文本输出守卫测试（v0.42.0，ADR-0017）。

QQ/onebot 不解析 Markdown：所有用户可见输出不得含有成对 **、~~ 加粗/删除
符号，行首不得出现 `>` 引用、`#` 标题（这些符号在纯文本下是视觉噪音）。
允许保留：- 列表、1. 编号、emoji、×/→/（）、骰式表达式内的 >/!/#标签
（如 3d6>3、d20+5#攻击检定 中的符号不成对且居中，按字面渲染）。

本测试驱动代表性命令/工具/format 输出，防止 markdown 符号回潮。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from astrbot_plugin_trpg_assistant.inventory import InventoryManager
from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin
from astrbot_plugin_trpg_assistant.shop import ShopManager
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


class FakeEvent:
    def __init__(self, message_str: str, admin: bool = False, private: bool = False) -> None:
        self.message_str = message_str
        self.unified_msg_origin = "group:1"
        self._admin = admin
        self._private = private
        self.stopped = False

    def get_sender_id(self) -> str:
        return "u1"

    def get_sender_name(self) -> str:
        return "Alice"

    def is_private_chat(self) -> bool:
        return self._private

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self, config: dict | None = None, db_path: Path | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}
        if db_path is not None:
            self._kb_manager = KnowledgeBaseManager(db_path)

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


def _assert_plain(text: str) -> None:
    assert "**" not in text, f"输出含 ** 加粗符号：{text!r}"
    assert "~~" not in text, f"输出含 ~~ 删除线符号：{text!r}"
    for line in text.splitlines():
        stripped = line.lstrip()
        assert not stripped.startswith(">"), f"输出含行首引用符号：{line!r}"
        assert not stripped.startswith("#"), f"输出含行首标题符号：{line!r}"


def _all_outputs(p: _MemoryPlugin, *events) -> list[str]:
    """驱动多条命令/工具调用，收集全部输出文本。"""
    outs: list[str] = []
    for call in events:
        outs.extend(call)
    return outs


@pytest.mark.parametrize(
    "producer",
    [
        # 背包 / 商店 / 角色卡 format 方法
        lambda p: [
            InventoryManager.format_inventory(
                run(p._inventory.get_personal(FakeEvent("/"))), "🎒 背包"
            )
        ],
        lambda p: [
            ShopManager.format_shop(run(p.shop_manager.get("group:1")))
        ],
        # 命令
        lambda p: run(_collect(p.bag_cmd(FakeEvent("/bag")))),
        lambda p: run(_collect(p.bag_cmd(FakeEvent("/bag add 治疗药水 3 w=0.5 v=50")))),
        lambda p: run(_collect(p.grant_cmd(FakeEvent("/发放 治疗药水 3 绳索 1")))),
        lambda p: run(_collect(p.grant_cmd(FakeEvent("/发放 治疗药水 5"))))
        + run(_collect(p.revoke_cmd(FakeEvent("/收回 治疗药水 1", admin=True)))),
        lambda p: run(_collect(p.shop_cmd(FakeEvent("/商店")))),
        lambda p: run(_collect(p.init_cmd(FakeEvent("/init list")))),
        # LLM 工具
        lambda p: [run(p.manage_inventory_tool(FakeEvent("/"), action="list"))],
        lambda p: [
            run(
                p.manage_inventory_tool(
                    FakeEvent("/"),
                    action="add",
                    items=[{"item": "治疗药水", "qty": 2}, {"item": "绳索"}],
                )
            )
        ],
        lambda p: [
            run(
                p.manage_inventory_tool(
                    FakeEvent("/"),
                    action="add",
                    item="治疗药水",
                    qty=2,
                    weight=0.5,
                    value=50,
                )
            )
        ],
        lambda p: [
            run(
                p.manage_inventory_tool(
                    FakeEvent("/", admin=True),
                    action="remove",
                    item="治疗药水",
                    qty=1,
                    to_party=True,
                )
            )
        ],
        lambda p: [run(p.manage_shop_tool(FakeEvent("/"), action="list"))],
        lambda p: [run(p.manage_initiative_tool(FakeEvent("/"), action="list"))],
    ],
    ids=[
        "inventory_format",
        "shop_format",
        "bag_list",
        "bag_add",
        "grant",
        "revoke",
        "shop_list",
        "init_list",
        "tool_inventory_list",
        "tool_inventory_items_add",
        "tool_inventory_single_add",
        "tool_inventory_remove_party",
        "tool_shop_list",
        "tool_initiative_list",
    ],
)
def test_outputs_are_plain_text(producer) -> None:
    p = _MemoryPlugin()
    outputs = producer(p)
    assert outputs, "生产器应产出至少一条输出"
    for text in outputs:
        _assert_plain(str(text))


def test_kb_class_outputs_are_plain_text(tmp_path: Path) -> None:
    """v0.48.0（ADR-0023）：/查职业 概要层与全文分条逐条守卫纯文本。"""
    db = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, db, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    p = _MemoryPlugin(db_path=db)
    # 概要总表（单条）
    for text in run(_collect(p.kb_class_cmd(FakeEvent("/查职业 战士")))):
        _assert_plain(text)
    # 全文分条（多条）
    msgs = run(_collect(p.kb_class_cmd(FakeEvent("/查职业 战士 特性"))))
    assert len(msgs) >= 2
    for text in msgs:
        _assert_plain(text)

"""AstrBot v4.5+ llm_tool 事件注入兼容测试。

新版 agent 体系调用插件 @filter.llm_tool 方法时，注入的第一个参数（event）
是 ContextWrapper（context=AstrAgentContext，真正的事件在 .context.event），
老版直接注入 AstrMessageEvent。_resolve_event 统一解出真实事件对象。

覆盖点：
  - _resolve_event 三种形态（老版事件 / v4.5+ ContextWrapper / 无法识别）。
  - 各 llm_tool 在 ContextWrapper 注入下正常执行（不再 AttributeError）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin, _resolve_event
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
# 测试构建时隔离真实补丁目录（kb_patches/），避免真实补丁污染 fixture 计数。
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


class FakeEvent:
    def __init__(self, message_str: str = "", origin: str = "group:1") -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self.stopped = False

    def get_sender_id(self) -> str:
        return "u1"

    def get_sender_name(self) -> str:
        return "Alice"

    def is_private_chat(self) -> bool:
        return False

    def is_admin(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self, db_path: Path, config: dict | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}
        self._kb_manager = KnowledgeBaseManager(db_path)

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def make_plugin(tmp_path: Path) -> _MemoryPlugin:
    db = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, db, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return _MemoryPlugin(db)


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def _context_wrapper(event: FakeEvent) -> SimpleNamespace:
    """模拟 AstrBot v4.5+ 注入形态：ContextWrapper(context=AstrAgentContext(event=...))。"""
    agent_ctx = SimpleNamespace(event=event)
    return SimpleNamespace(context=agent_ctx, messages=[], tool_call_timeout=60)


class TestResolveEvent:
    def test_legacy_event_passthrough(self) -> None:
        ev = FakeEvent(origin="group:9")
        assert _resolve_event(ev) is ev

    def test_v45_context_wrapper(self) -> None:
        ev = FakeEvent(origin="group:9")
        wrapper = _context_wrapper(ev)
        assert _resolve_event(wrapper) is ev

    def test_nested_via_agent_context_attr(self) -> None:
        ev = FakeEvent()
        wrapper = SimpleNamespace(agent_context=SimpleNamespace(event=ev))
        assert _resolve_event(wrapper) is ev

    def test_unrecognized_returns_none(self) -> None:
        assert _resolve_event(None) is None
        assert _resolve_event(object()) is None


class TestToolsUnderContextWrapper:
    def test_manage_shop_list_under_wrapper(self, tmp_path: Path) -> None:
        p = make_plugin(tmp_path)
        run(p.shop_manager.add_entry("group:1", "长剑", price_cp=1500))
        ev = FakeEvent()
        out = run(p.manage_shop_tool(_context_wrapper(ev), action="list"))
        assert "**长剑**" in out  # 不再 AttributeError

    def test_manage_shop_buy_under_wrapper(self, tmp_path: Path) -> None:
        p = make_plugin(tmp_path)
        run(p.shop_manager.add_entry("group:1", "长剑", price_cp=1500))
        # 先给玩家金币（直接走管理器，事件用真实 FakeEvent）
        ev = FakeEvent()
        run(p._inventory.add_item(ev, "金币", 20, value=100.0))
        out = run(p.manage_shop_tool(_context_wrapper(ev), action="buy", item="长剑", qty=1))
        assert "已购买" in out
        inv = run(p._inventory.get_personal(ev))
        assert inv.find("长剑") is not None
        assert inv.find("金币").qty == 5  # 20金 - 15金

    def test_manage_inventory_list_under_wrapper(self, tmp_path: Path) -> None:
        p = make_plugin(tmp_path)
        ev = FakeEvent()
        run(p._inventory.add_item(ev, "治疗药水", 3))
        out = run(p.manage_inventory_tool(_context_wrapper(ev), action="list"))
        assert "治疗药水" in out

    def test_roll_dice_under_wrapper(self, tmp_path: Path) -> None:
        p = make_plugin(tmp_path)
        out = run(p.roll_dice_tool(_context_wrapper(FakeEvent()), expression="1d20"))
        assert out  # 正常返回掷骰结果

    def test_unresolvable_event_returns_friendly_error(self, tmp_path: Path) -> None:
        p = make_plugin(tmp_path)
        out = run(p.manage_shop_tool(object(), action="list"))
        assert "工具上下文解析失败" in out

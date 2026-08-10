"""advise_build 工具全链路测试（FakeEvent + 内存 KV 插件）。

覆盖点：
  - new_build：返回 JSON 档案文本（含 hint 与守则），群规则 edition/起始等级生效。
  - level_up：无活跃卡提示；有活跃卡 → 特性时间线 + 专长候选前置标注。
  - guide_chargen start 预填透传（race/class_name/background → 状态机跳过）。
  - query_dnd_knowledge：spell_class 职业法术表反查、class_features 等级过滤。
  - ContextWrapper 注入（v4.5+ 事件兼容）下 advise_build 正常执行。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_trpg_assistant.chargen import ChargenManager, ChargenRule
from astrbot_plugin_trpg_assistant.character import (
    AbilityScores,
    CharacterManager,
    CharacterSheet,
    ClassLevel,
)
from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"
LOOKUP = Path(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"


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


class _ContextWrapper:
    """AstrBot v4.5+ 注入形态：真实事件在 .context.event。"""

    def __init__(self, event: FakeEvent) -> None:
        self.context = SimpleNamespace(event=event)


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


@pytest.fixture()
def plugin(tmp_path: Path) -> _MemoryPlugin:
    db = tmp_path / "kb" / "dnd_kb.db"
    build(
        FIXTURE_DIR, db, commit="fixture-abc123",
        patch_root=NO_PATCH_DIR, en_lookup=LOOKUP,
    )
    return _MemoryPlugin(db)


def run(coro):
    return asyncio.run(coro)


def _set_rule(plugin: _MemoryPlugin, edition: str = "2014", level: int = 1) -> None:
    cg: ChargenManager = plugin.chargen_manager
    rule = ChargenRule(
        edition=edition,
        starting_level=level,
        subclass_at_creation="auto",
    )
    run(cg.set_rule(FakeEvent(), rule))


def _save_card(plugin: _MemoryPlugin, **kw) -> None:
    cm: CharacterManager = plugin.character_manager
    defaults = dict(
        name="测试卡",
        edition="2014",
        classes=[ClassLevel(class_name="战士", subclass="", level=1)],
        race="人类",
        feats=[],
        ability_scores=AbilityScores(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=14, charisma=10,
        ),
    )
    defaults.update(kw)
    run(cm.save_card(FakeEvent(), CharacterSheet(**defaults)))


class TestAdviseBuildNewBuild:
    def test_returns_json_dossier(self, plugin: _MemoryPlugin) -> None:
        _set_rule(plugin, "2014", 1)
        text = run(plugin.advise_build_tool(
            FakeEvent(), action="new_build", goal="", keywords="", level=-1
        ))
        body = text.split("\n【守则】")[0]
        d = json.loads(body)
        assert d["edition"] == "2014"
        assert d["level"] == 1
        assert isinstance(d["classes"], list)
        assert "hint" in d
        assert "【守则】" in text and "禁止凭记忆" in text

    def test_level_param_overrides_rule(self, plugin: _MemoryPlugin) -> None:
        _set_rule(plugin, "2014", 1)
        text = run(plugin.advise_build_tool(
            FakeEvent(), action="new_build", level=15
        ))
        d = json.loads(text.split("\n")[0])
        assert d["level"] == 15

    def test_unknown_goal_falls_back(self, plugin: _MemoryPlugin) -> None:
        _set_rule(plugin, "2024", 1)
        text = run(plugin.advise_build_tool(
            FakeEvent(), action="new_build", goal="完全不存在的词"
        ))
        d = json.loads(text.split("\n")[0])
        assert d["edition"] == "2024"


class TestAdviseBuildLevelUp:
    def test_no_active_card(self, plugin: _MemoryPlugin) -> None:
        text = run(plugin.advise_build_tool(FakeEvent(), action="level_up"))
        assert "未找到活跃角色卡" in text

    def test_with_active_card(self, plugin: _MemoryPlugin) -> None:
        _save_card(plugin, name="阿尔文")
        text = run(plugin.advise_build_tool(FakeEvent(), action="level_up"))
        body = text.split("\n【守则】")[0]
        d = json.loads(body)
        assert d["card"]["name"] == "阿尔文"
        assert d["card"]["total_level"] == 1
        # 战士 1→2 特性时间线（fixture 动作如潮）
        assert any(
            t["name"] == "动作如潮" for t in d["class_features_timeline"]
        )
        assert isinstance(d["feat_candidates"], list)

    def test_context_wrapper_injection(self, plugin: _MemoryPlugin) -> None:
        """AstrBot v4.5+ ContextWrapper 注入下 advise_build 正常执行。"""
        _save_card(plugin, name="阿尔文")
        wrapper = _ContextWrapper(FakeEvent())
        text = run(plugin.advise_build_tool(wrapper, action="level_up"))
        assert "未找到活跃角色卡" not in text
        d = json.loads(text.split("\n")[0])
        assert d["card"]["name"] == "阿尔文"


class TestGuideChargenPrefill:
    def test_start_with_prefill_params(self, plugin: _MemoryPlugin) -> None:
        _set_rule(plugin, "2014", 1)
        text = run(plugin.guide_chargen_tool(
            FakeEvent(),
            action="start", race="矮人", class_name="法师", background="侍僧",
        ))
        assert "已预填种族「矮人」" in text
        assert "已预填职业「法师」" in text
        assert "已预填背景「侍僧」" in text
        assert "已完成前置步骤" in text
        draft = run(plugin.chargen_manager.get_draft(FakeEvent()))
        assert draft.state == "ABILITY_ASSIGN"
        assert draft.data["class_name"] == "法师"

    def test_start_prefill_invalid_ignored(self, plugin: _MemoryPlugin) -> None:
        _set_rule(plugin, "2014", 1)
        text = run(plugin.guide_chargen_tool(
            FakeEvent(), action="start", race="矮人", class_name="不存在的职业"
        ))
        assert "不是有效的职业" in text
        draft = run(plugin.chargen_manager.get_draft(FakeEvent()))
        assert draft.state == "CLASS"


class TestQueryToolNewParams:
    def test_spell_class_filter(self, plugin: _MemoryPlugin) -> None:
        text = run(plugin.query_dnd_knowledge_tool(
            FakeEvent(), action="filter", kind="法术",
            spell_class="法师", level=0,
        ))
        # fixture 6 条 spell_classes 行，戏法级有 冰霜射线/火球术(?) 等
        assert "条符合条件的条目" in text
        assert "冰霜射线" in text or "火球术" in text

    def test_spell_class_english_name(self, plugin: _MemoryPlugin) -> None:
        text = run(plugin.query_dnd_knowledge_tool(
            FakeEvent(), action="filter", kind="法术",
            spell_class="Wizard", level=0,
        ))
        assert "条符合条件的条目" in text

    def test_spell_class_unknown(self, plugin: _MemoryPlugin) -> None:
        text = run(plugin.query_dnd_knowledge_tool(
            FakeEvent(), action="filter", kind="法术", spell_class="不存在的职业"
        ))
        assert "找不到职业" in text

    def test_class_features_level_filter(self, plugin: _MemoryPlugin) -> None:
        text = run(plugin.query_dnd_knowledge_tool(
            FakeEvent(), action="class_features", name="战士", class_level=2
        ))
        assert "动作如潮" in text
        assert "战斗风格" not in text  # 1 级特性被等级过滤掉

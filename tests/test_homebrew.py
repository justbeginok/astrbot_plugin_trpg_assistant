"""v0.36.0 运行期私设（homebrew）overlay 测试。

覆盖：加载器（双格式/坏文件/覆盖/去重/怪物 trait 正文）、查询层合并
（search/detail/filter 私设置顶 + 房规标注）、reload 原子替换、命令级
（/kb reload /kb 私设 /查法术 私设命中）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from astrbot_plugin_trpg_assistant.homebrew import (
    HomebrewManager,
    filter_overlay,
    search_overlay,
)
from astrbot_plugin_trpg_assistant.kb import (
    HOMEBREW_FLAG,
    KnowledgeBaseManager,
)
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


def build_db(tmp_path: Path) -> Path:
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return out


def write_homebrew(dir_path: Path, name: str, data) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 加载器单元测试
# ---------------------------------------------------------------------------


def test_parse_5etools_format(tmp_path: Path) -> None:
    write_homebrew(tmp_path / "hb", "spells.json", {
        "spell": [{
            "name": "私设火球", "ENG_name": "Homebrew Fireball", "source": "DM",
            "level": 3, "school": "E",
            "range": {"type": "point", "distance": {"type": "feet", "amount": 150}},
            "components": {"v": True, "s": True},
            "duration": [{"type": "instant"}],
            "entries": ["你指定150尺内一点，火焰爆发造成8d6火焰伤害。"],
        }]
    })
    mgr = HomebrewManager(tmp_path / "hb")
    result = mgr.load()
    assert result.files == 1 and result.entries == 1 and not result.errors
    e = mgr.entries()[0]
    assert e.kind == "spell" and e.source == "DM"
    assert e.side["level"] == 3 and e.side["range_feet"] == 150
    # v0.44.0：私设法术正文走 PHB 卡片式（环位行 + 属性行；fixture 无 time 字段）
    assert e.body.startswith("三环 惑控")
    assert "施法距离：150 尺" in e.body
    assert "法术成分：V、S" in e.body
    assert "持续时间：立即" in e.body


def test_parse_simple_format_and_kind_resolve(tmp_path: Path) -> None:
    write_homebrew(tmp_path / "hb", "simple.json", [
        {"kind": "物品", "name": "房规巨剑", "source": "MYHOUSE", "rarity": "rare",
         "body": "攻击时额外造成1d6火焰伤害。",
         "tags": {"dmg_dealt": ["火焰"]}},
        {"kind": "职业", "name": "龙脉武者", "body": "龙的血脉。",
         "tags": {"class_role": ["武者"]}},
    ])
    mgr = HomebrewManager(tmp_path / "hb")
    result = mgr.load()
    assert result.entries == 2, result.warnings
    by_name = {e.name: e for e in mgr.entries()}
    assert by_name["房规巨剑"].side["rarity"] == "rare"
    assert by_name["房规巨剑"].tags == [("dmg_dealt", "火焰")]
    assert by_name["龙脉武者"].source == "HOMEBREW"
    assert by_name["龙脉武者"].edition == "2014"  # 未知 source 默认 2014


def test_bad_file_and_invalid_entry(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    write_homebrew(hb, "bad.json", "这不是 JSON {{{")
    write_homebrew(hb, "no_name.json", {"items": [{"kind": "item", "body": "x"}]})
    write_homebrew(hb, "empty_body.json", {
        "items": [{"kind": "item", "name": "空正文", "source": "DM"}]})
    mgr = HomebrewManager(hb)
    result = mgr.load()
    assert result.files == 3
    assert len(result.errors) == 1  # bad.json
    assert "bad.json" in result.errors[0]
    assert len(result.warnings) == 2  # 缺 name + 正文空
    assert result.entries == 0


def test_monster_body_from_trait_action(tmp_path: Path) -> None:
    """回归：5etools 怪物正文在 trait/action 字段（无 entries）也必须渲染。"""
    write_homebrew(tmp_path / "hb", "monsters.json", {
        "monsters": [{
            "name": "翡翠软泥", "source": "DM", "size": "M", "type": "ooze",
            "cr": "3",
            "trait": [{"name": "翡翠酸液",
                       "entries": ["接触时造成额外2d6强酸伤害。"]}],
            "action": [{"name": "伪足打击",
                        "entries": ["命中时造成 1d6+2 钝击外加 3d6 强酸伤害。"]}],
        }]
    })
    mgr = HomebrewManager(tmp_path / "hb")
    result = mgr.load()
    assert result.entries == 1 and not result.warnings, result.warnings
    e = mgr.entries()[0]
    assert "【特性】" in e.body and "【动作】" in e.body
    assert e.side["cr"] == 3.0 and e.side["mtype"] == "ooze"


def test_override_count_and_dedup(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    write_homebrew(hb, "a.json", {"items": [
        {"kind": "item", "name": "治愈魔杖", "source": "DMG", "body": "覆盖版。"},
        {"kind": "item", "name": "治愈魔杖", "source": "DMG", "body": "重复版。"},
    ]})
    mgr = HomebrewManager(hb)
    result = mgr.load(official_keys={("item", "治愈魔杖", "DMG")})
    assert result.entries == 1  # 去重保留首个
    assert len(result.warnings) == 1
    assert result.overrides == 1
    assert mgr.entries()[0].is_override is True


def test_search_and_filter_overlay(tmp_path: Path) -> None:
    write_homebrew(tmp_path / "hb", "x.json", {"items": [
        {"kind": "item", "name": "房规巨剑", "source": "DM", "rarity": "rare",
         "body": "火焰。", "tags": {"dmg_dealt": ["火焰"]}},
        {"kind": "item", "name": "房规匕首", "source": "DM", "rarity": "common",
         "body": "小刀。", "tags": {"dmg_dealt": ["穿刺"]}},
    ]})
    mgr = HomebrewManager(tmp_path / "hb")
    mgr.load()
    entries = mgr.entries()
    assert len(search_overlay(entries, "巨剑")) == 1
    assert len(search_overlay(entries, "首")) == 1  # LIKE：「首」在「房规匕首」中
    assert len(search_overlay(entries, "房规巨剑术")) == 1  # 逐字缩短 →「房规巨剑」
    assert len(filter_overlay(entries, "item", rarity="rare")) == 1
    assert len(filter_overlay(entries, "item", rarity="rare",
                              tags=[("dmg_dealt", "火焰")])) == 1
    assert filter_overlay(entries, "item", rarity="rare",
                          tags=[("dmg_dealt", "穿刺")]) == []


# ---------------------------------------------------------------------------
# KbManager 查询层合并
# ---------------------------------------------------------------------------


@pytest.fixture()
def kb_with_hb(tmp_path: Path) -> KnowledgeBaseManager:
    db = build_db(tmp_path)
    write_homebrew(tmp_path / "hb", "spells.json", {"spell": [
        {"name": "火球术", "source": "XPHB",
         "body": "（房规）本团火球术伤害改为10d6。",
         "tags": {"spell_keyword": ["伤害"]}},
        {"name": "私设新法术", "source": "DM", "level": 1,
         "entries": ["一个私设法术的正文。"]},
    ]})
    return KnowledgeBaseManager(db, homebrew_dir=tmp_path / "hb")


def test_search_homebrew_first_and_marked(kb_with_hb: KnowledgeBaseManager) -> None:
    hits = kb_with_hb.search("火球术")
    assert hits, "官方应有火球术命中"
    assert hits[0].is_homebrew, "私设覆盖版应置顶"
    assert hits[0].source == "XPHB"
    assert any(not h.is_homebrew for h in hits), "官方版本应仍在结果中"


def test_search_homebrew_only_hit(kb_with_hb: KnowledgeBaseManager) -> None:
    hits = kb_with_hb.search("私设新法术", kind="spell")
    assert len(hits) == 1 and hits[0].is_homebrew
    hits = kb_with_hb.search("私设新法")  # LIKE 档
    assert len(hits) == 1 and hits[0].is_homebrew


def test_detail_homebrew_override_first(kb_with_hb: KnowledgeBaseManager) -> None:
    entries = kb_with_hb.detail("火球术", kind="spell")
    assert entries
    assert entries[0].is_homebrew
    assert "房规" in entries[0].body
    assert any(not e.is_homebrew for e in entries), "官方版本并存"


def test_detail_homebrew_new_entry(kb_with_hb: KnowledgeBaseManager) -> None:
    entries = kb_with_hb.detail("私设新法术", kind="spell")
    assert len(entries) == 1 and entries[0].is_homebrew
    assert entries[0].level == 1


def test_filter_homebrew_merge(kb_with_hb: KnowledgeBaseManager) -> None:
    # 官方库有 3 环法术；私设 1 环「私设新法术」应混入 1 环结果
    result = kb_with_hb.filter("spell", level=1)
    names = {e.name for e in result.entries}
    assert "私设新法术" in names
    hb_entry = next(e for e in result.entries if e.name == "私设新法术")
    assert hb_entry.is_homebrew
    assert result.total > 0


def test_format_marks_homebrew(kb_with_hb: KnowledgeBaseManager) -> None:
    text = KnowledgeBaseManager.format_entry(kb_with_hb.detail("私设新法术")[0])
    assert HOMEBREW_FLAG in text
    hits = kb_with_hb.search("火球术")
    text = KnowledgeBaseManager.format_hits(hits)
    assert HOMEBREW_FLAG in text
    result = kb_with_hb.filter("spell", level=1)
    text = KnowledgeBaseManager.format_filter_result(result, "法术")
    assert HOMEBREW_FLAG in text


def test_reload_atomic(kb_with_hb: KnowledgeBaseManager, tmp_path: Path) -> None:
    assert kb_with_hb.homebrew_stats().entries == 2
    write_homebrew(tmp_path / "hb", "extra.json", {"monsters": [
        {"name": "私设史莱姆", "source": "DM", "cr": 1,
         "entries": ["一团绿色软泥。"]}]})
    result = kb_with_hb.reload_homebrew()
    assert result.entries == 3
    hits = kb_with_hb.search("史莱姆", kind="monster")
    assert hits and hits[0].is_homebrew


def test_no_homebrew_dir(tmp_path: Path) -> None:
    mgr = KnowledgeBaseManager(build_db(tmp_path))  # 不传 homebrew_dir
    assert mgr.search("私设新法术") == []
    result = mgr.reload_homebrew()
    assert result.entries == 0 and result.files == 0


# ---------------------------------------------------------------------------
# 命令级：/kb reload /kb 私设 /查法术 私设命中
# ---------------------------------------------------------------------------


class FakeEvent:
    def __init__(self, message_str: str, private: bool = False) -> None:
        self.message_str = message_str
        self.unified_msg_origin = "group:1"
        self._private = private
        self.stopped = False

    def get_sender_id(self) -> str:
        return "u1"

    def get_sender_name(self) -> str:
        return "Alice"

    def is_private_chat(self) -> bool:
        return self._private

    def is_admin(self) -> bool:
        return True

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self, db_path: Path, homebrew_dir: Path | None = None) -> None:
        super().__init__(context=None, config=None)
        self._kv: dict[str, object] = {}
        self._kb_manager = KnowledgeBaseManager(db_path, homebrew_dir=homebrew_dir)

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


def test_kb_reload_command(tmp_path: Path) -> None:
    db = build_db(tmp_path)
    hb = tmp_path / "hb"
    write_homebrew(hb, "s.json", {"法术": [
        {"kind": "法术", "name": "飘浮羽毛", "source": "DM",
         "body": "让羽毛飘浮。"}]})
    plugin = _MemoryPlugin(db, homebrew_dir=hb)
    msgs = run(_collect(plugin._handle_kb(FakeEvent("/kb reload"), "reload", "/")))
    text = "".join(msgs)
    assert "私设已重载" in text
    assert "扫描 1 个文件" in text and "加载 1 条私设" in text


def test_kb_homebrew_stats_command(tmp_path: Path) -> None:
    db = build_db(tmp_path)
    hb = tmp_path / "hb"
    write_homebrew(hb, "s.json", {"spell": [
        {"name": "飘浮羽毛", "source": "DM", "body": "让羽毛飘浮。"}]})
    plugin = _MemoryPlugin(db, homebrew_dir=hb)
    run(_collect(plugin._handle_kb(FakeEvent("/kb reload"), "reload", "/")))
    msgs = run(_collect(plugin._handle_kb(FakeEvent("/kb 私设"), "私设", "/")))
    text = "".join(msgs)
    assert "已加载 1 条私设" in text and "来自 1 个文件" in text
    assert "目录：" in text  # 展示实际私设目录路径


def test_kb_lookup_homebrew_hit(tmp_path: Path) -> None:
    """/查法术 直接命中私设（detail 合并 → 私设展示带 🏠房规）。"""
    db = build_db(tmp_path)
    hb = tmp_path / "hb"
    write_homebrew(hb, "s.json", {"spell": [
        {"name": "火球术", "source": "XPHB",
         "body": "（房规）本团火球术伤害改为10d6。"}]})
    plugin = _MemoryPlugin(db, homebrew_dir=hb)
    msgs = run(_collect(plugin._handle_kb_lookup(
        FakeEvent("/查法术 火球术"), "火球术", "spell", "/")))
    text = "".join(msgs)
    assert HOMEBREW_FLAG in text
    assert "本团火球术伤害改为10d6" in text

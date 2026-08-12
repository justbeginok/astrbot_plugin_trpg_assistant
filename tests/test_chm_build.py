"""5e_chm md 法术源构建集成测试：--spell-md 开关、条目对账、富化、spell_classes。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"
LOOKUP = Path(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"

# 最小 md 法术记录（chm_parser 产物格式，含 body 预构建）
MD_SPELLS = [
    {
        "name": "火球术", "aliases": [], "eng_name": "Fireball",
        "source": "PHB24", "source_5e": "XPHB", "edition": "2024",
        "level": 3, "school": "塑能", "classes": ["术士", "法师"],
        "time": "动作", "components": {"v": True, "s": True, "m": True, "costly": False},
        "ritual": False, "concentration": False,
        "has_detail": True,
        "detail": "明亮的闪光从你的指间飞驰向施法距离内你指定的一点。豁免失败者将受到8d6点火焰伤害。",
        "detail_higher": "升环施法。使用的法术位每比三环高一环，此伤害就增加1d6。",
        "detail_time": "动作", "detail_range": "150 尺", "detail_duration": "立即",
        "body": "三环 塑能（术士、法师）\n施法时间：动作\n施法距离：150 尺\n"
                "法术成分：V、S、M\n持续时间：立即\n\n"
                "明亮的闪光从你的指间飞驰向施法距离内你指定的一点。豁免失败者将受到8d6点火焰伤害。\n\n"
                "升环施法。使用的法术位每比三环高一环，此伤害就增加1d6。",
    },
    {
        "name": "造水术", "aliases": ["枯水术", "造水"], "eng_name": "Create or Destroy Water",
        "source": "PHB14", "source_5e": "PHB", "edition": "2014",
        "level": 1, "school": "变化", "classes": ["牧师", "德鲁伊"],
        "time": "动作", "components": {"v": True, "s": True, "m": True, "costly": False},
        "ritual": False, "concentration": False,
        "has_detail": True,
        "detail": "你施法创造水或者使水枯竭。",
        "detail_higher": "",
        "detail_time": "1 动作", "detail_range": "30 尺", "detail_duration": "立即",
        "body": "一环 变化（牧师、德鲁伊）\n施法时间：1 动作\n施法距离：30 尺\n"
                "法术成分：V、S、M\n持续时间：立即\n\n你施法创造水或者使水枯竭。",
    },
]


def _write_md(tmp_path: Path) -> Path:
    p = tmp_path / "spells_chm.json"
    p.write_text(json.dumps(MD_SPELLS, ensure_ascii=False), encoding="utf-8")
    return p


def _conn(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def test_build_with_spell_md(tmp_path: Path) -> None:
    """--spell-md 提供时法术条目以 md 为准：数量/正文/来源/is_machine。"""
    md = _write_md(tmp_path)
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR, spell_md=md)
    conn = _conn(out)
    rows = conn.execute(
        "SELECT e.name, e.eng_name, e.source, e.edition, e.is_machine,"
        " s.level, s.school, s.components, s.range_feet"
        " FROM entries e JOIN spells s ON s.entry_id = e.id"
        " WHERE e.kind='spell' ORDER BY e.source"
    ).fetchall()
    assert len(rows) == 2
    zw, fb = rows  # ORDER BY e.source：PHB < XPHB
    assert zw == ("造水术", "Create or Destroy Water", "PHB", "2014", 0, 1, "T", "VSM", 30)
    assert fb == ("火球术", "Fireball", "XPHB", "2024", 0, 3, "V", "VSM", 150)
    # 别名：造水术 原名「造水」+ 双拼名「枯水术」
    aliases = {r[0] for r in conn.execute(
        "SELECT a.alias FROM aliases a JOIN entries e ON e.id = a.entry_id"
        " WHERE e.name='造水术'"
    )}
    assert {"造水", "枯水术"} <= aliases
    # 正文预构建（环位行 + 属性行 + 详述 + 升环，v0.44.0 PHB 卡片式）
    body = conn.execute(
        "SELECT body FROM entries WHERE name='火球术' AND source='XPHB'"
    ).fetchone()[0]
    assert body.startswith("三环 塑能（术士、法师）")
    assert "升环施法" in body
    # 自动标签（dmg_dealt 从 md 正文提取）
    tags = conn.execute(
        "SELECT facet, value FROM entry_tags t JOIN entries e ON e.id=t.entry_id"
        " WHERE e.name='火球术' AND facet='dmg_dealt'"
    ).fetchall()
    assert ("dmg_dealt", "火焰") in tags
    conn.close()


def test_build_spell_md_with_enrich(tmp_path: Path) -> None:
    """md 法术 + spell_enrich 补丁 → spells.summary / spell_keyword 写入。"""
    md = _write_md(tmp_path)
    patch = tmp_path / "patches"
    (patch / "kb_patches").mkdir(parents=True)
    (patch / "kb_patches" / "spell_enrich.json").write_text(json.dumps([
        {"name": "火球术", "source": "XPHB", "edition": "2024",
         "summary": "爆裂火球灼烧大范围区域。", "keywords": ["火焰", "伤害"]},
    ], ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=patch, spell_md=md)
    conn = _conn(out)
    summary = conn.execute(
        "SELECT s.summary FROM spells s JOIN entries e ON e.id=s.entry_id"
        " WHERE e.name='火球术'"
    ).fetchone()[0]
    assert summary == "爆裂火球灼烧大范围区域。"
    kw = conn.execute(
        "SELECT value FROM entry_tags t JOIN entries e ON e.id=t.entry_id"
        " WHERE e.name='火球术' AND facet='spell_keyword' ORDER BY value"
    ).fetchall()
    assert [r[0] for r in kw] == ["伤害", "火焰"]
    conn.close()


def test_build_spell_md_spell_classes(tmp_path: Path) -> None:
    """md 法术 + en_lookup → spell_classes 职业法术表照常构建（按 eng+source 匹配）。"""
    md = _write_md(tmp_path)
    out = tmp_path / "kb" / "dnd_kb.db"
    build(
        FIXTURE_DIR, out, commit="fixture-abc123",
        patch_root=NO_PATCH_DIR, en_lookup=LOOKUP, spell_md=md,
    )
    conn = _conn(out)
    rows = conn.execute(
        "SELECT e.name, sc.class_name FROM spell_classes sc"
        " JOIN entries e ON e.id = sc.entry_id ORDER BY e.name"
    ).fetchall()
    # en_lookup 把 Fireball 映射到 Wizard → 法师（XPHB 版）；PHB 版同样命中
    assert ("火球术", "法师") in rows
    conn.close()


def test_default_build_unchanged(tmp_path: Path) -> None:
    """不传 --spell-md 时构建行为与旧版一致（6 条 fixture 法术）。"""
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    conn = _conn(out)
    assert conn.execute(
        "SELECT COUNT(*) FROM entries WHERE kind='spell'"
    ).fetchone()[0] == 6
    conn.close()


# ---------------------------------------------------------------------------
# _build_body：chm 记录 → PHB 卡片体（v0.44.0）
# ---------------------------------------------------------------------------


def test_build_body_phb_card() -> None:
    from scripts.chm_parser import _build_body

    rec = {
        "name": "命令术", "level": 1, "school": "惑控",
        "classes": ["吟游诗人", "牧师", "圣武士"],
        "ritual": False, "concentration": False,
        "detail_time": "1 动作", "detail_range": "60 尺",
        "detail_components": "V", "detail_duration": "1 轮",
        "detail": "描述正文。", "detail_higher": "升环施法。每高一环多一个目标。",
    }
    body = _build_body(rec)
    lines = body.split("\n")
    assert lines[0] == "一环 惑控（吟游诗人、牧师、圣武士）"
    assert lines[1] == "施法时间：1 动作"
    assert lines[2] == "施法距离：60 尺"
    assert lines[3] == "法术成分：V"
    assert lines[4] == "持续时间：1 轮"
    assert "描述正文。" in body and "升环施法。每高一环多一个目标。" in body


def test_build_body_cantrip_ritual_noclass() -> None:
    from scripts.chm_parser import _build_body

    # 戏法 + 无 classes
    body = _build_body({
        "name": "魔法伎俩", "level": 0, "school": "变化", "classes": [],
        "ritual": False, "detail_time": "1 动作", "detail_range": "10 尺",
        "detail_components": "V、S", "detail_duration": "至多 1 小时",
        "detail": "正文。",
    })
    assert body.startswith("变化戏法")
    # 仪式 + classes 空 → （仪式）
    body2 = _build_body({
        "name": "魔袋术", "level": 2, "school": "咒法", "classes": [],
        "ritual": True, "detail_time": "1 动作", "detail_range": "自身",
        "detail_components": "S", "detail_duration": "专注，至多一小时",
        "detail": "正文。",
    })
    assert body2.startswith("二环 咒法（仪式）")
    # classes 空且非仪式 → 无括号
    body3 = _build_body({
        "name": "法术", "level": 3, "school": "塑能", "classes": [],
        "ritual": False, "detail_time": "1 动作", "detail_range": "30 尺",
        "detail_components": "V", "detail_duration": "立即", "detail": "正文。",
    })
    assert body3.startswith("三环 塑能")
    assert "（" not in body3.split("\n")[0]


def test_build_body_components_fallback() -> None:
    """缺 detail_components 时由 components 字典兜底拼 V/S/M 字母。"""
    from scripts.chm_parser import _build_body

    body = _build_body({
        "name": "造水术", "level": 1, "school": "变化", "classes": ["牧师"],
        "ritual": False,
        "components": {"v": True, "s": True, "m": True, "costly": False},
        "detail_time": "1 动作", "detail_range": "30 尺", "detail_duration": "立即",
        "detail": "正文。",
    })
    assert "法术成分：V、S、M" in body


# ---------------------------------------------------------------------------
# _spell_body：5etools 记录 → PHB 卡片体（v0.44.0，构建回退 + 私设运行期）
# ---------------------------------------------------------------------------


def test_spell_body_5etools_card() -> None:
    from astrbot_plugin_trpg_assistant.kb_build_lib import _spell_body

    s = {
        "name": "Fireball", "level": 3, "school": "V",
        "time": [{"number": 1, "unit": "action"}],
        "range": {"type": "point", "distance": {"type": "feet", "amount": 150}},
        "components": {"v": True, "s": True, "m": "一小撮硫磺和蝙蝠粪"},
        "duration": [{"type": "instant"}],
        "entries": ["描述正文。"],
        "entriesHigherLevel": ["升环施法。伤害增加1d6。"],
    }
    body = _spell_body(s, classes=["法师", "术士"])
    lines = body.split("\n")
    assert lines[0] == "三环 塑能（法师、术士）"
    assert "施法时间：1 动作" in lines[1]
    assert "施法距离：150 尺" in lines[2]
    assert "法术成分：V、S、M（一小撮硫磺和蝙蝠粪）" in lines[3]
    assert "持续时间：立即" in lines[4]
    assert "描述正文。" in body and "升环施法。伤害增加1d6。" in body
    # 无 classes → 环位行无括号
    body2 = _spell_body(s)
    assert body2.split("\n")[0] == "三环 塑能"


def test_spell_body_range_duration_edge() -> None:
    from astrbot_plugin_trpg_assistant.kb_build_lib import _spell_body

    # 自身/触碰距离、专注 + 可提前消散、材料成分缺省
    s = {
        "level": 1, "school": "E",
        "time": [{"number": 1, "unit": "action"}],
        "range": {"type": "self"},
        "components": {"v": True, "s": False},
        "duration": [{
            "type": "timed",
            "duration": {"type": "minute", "amount": 10},
            "concentration": True, "dismissible": True,
        }],
        "entries": ["正文。"],
    }
    body = _spell_body(s)
    assert "施法距离：自身" in body
    assert "持续时间：专注，至多 10 分钟（可提前消散）" in body
    assert "法术成分：V" in body
    # 触碰 + 永久 + 材料为 dict
    s2 = {
        "level": 0, "school": "T",
        "time": [{"number": 1, "unit": "action"}],
        "range": {"distance": {"type": "touch"}},
        "components": {"v": True, "m": {"text": "特殊材料"}},
        "duration": [{"type": "permanent"}],
        "entries": ["正文。"],
    }
    body2 = _spell_body(s2)
    assert "施法距离：触碰" in body2
    assert "法术成分：V、M（特殊材料）" in body2
    assert "持续时间：永久" in body2

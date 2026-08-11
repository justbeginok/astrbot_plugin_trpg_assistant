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
        "body": "【法术信息】3环｜学派塑能｜施法时间动作｜距离150 尺｜成分言语姿势材料｜持续时间：立即\n\n"
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
        "body": "【法术信息】1环｜学派变化｜施法时间1 动作｜距离30 尺｜成分言语姿势材料｜持续时间：立即\n\n你施法创造水或者使水枯竭。",
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
    # 正文预构建（【法术信息】+ 详述 + 升环）
    body = conn.execute(
        "SELECT body FROM entries WHERE name='火球术' AND source='XPHB'"
    ).fetchone()[0]
    assert body.startswith("【法术信息】3环")
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

"""富化覆盖测试：spell_enrich.json 与 spells_chm.json 键对账 + gen_enrich 规则函数。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.gen_enrich import gen_keywords, gen_summary

PKG = Path(__file__).resolve().parent.parent
SPELLS = PKG / "scripts" / "_md_cache" / "spells_chm.json"
ENRICH = PKG / "kb_patches" / "spell_enrich.json"


def test_gen_summary_basic() -> None:
    rec = {"detail": "明亮的闪光从你的指间飞驰。豁免失败者将受到8d6点火焰伤害。", "level": 3, "school": "塑能"}
    s = gen_summary(rec)
    assert "闪光" in s and len(s) <= 93


def test_gen_summary_leading_clause_merges_next() -> None:
    """引导句（选择一个…）信息不完整 → 合并下一句。"""
    rec = {"detail": "选择一个施法距离内你可见的类人生物。该生物必须通过一次感知豁免。", "level": 1, "school": "惑控"}
    s = gen_summary(rec)
    assert "选择" in s and "豁免" in s


def test_gen_summary_no_detail_fallback() -> None:
    rec = {"detail": "", "level": 0, "school": "塑能"}
    assert "戏法" in gen_summary(rec)


def test_gen_keywords_damage_and_semantic() -> None:
    rec = {"detail": "目标受到8d6点火焰伤害并陷入目盲。", "school": "塑能"}
    kws = gen_keywords(rec)
    assert "火焰" in kws and "目盲" in kws


def test_gen_keywords_school_fallback() -> None:
    """规则未命中时给学派兜底词，保证 100% 覆盖。"""
    rec = {"detail": "你在法术持续时间内知晓所有语言的字面意义。", "school": "预言"}
    kws = gen_keywords(rec)
    assert kws, "兜底词不应为空"
    assert "侦查" in kws  # 预言 → 侦查


@pytest.mark.skipif(not (SPELLS.exists() and ENRICH.exists()),
                    reason="需要 spells_chm.json 与 spell_enrich.json（先跑解析/生成）")
def test_enrich_full_coverage_real() -> None:
    """真实产物：spell_enrich.json 对 spells_chm.json 全键覆盖（summary/keywords 非空）。"""
    spells = json.loads(SPELLS.read_text(encoding="utf-8"))
    enrich = json.loads(ENRICH.read_text(encoding="utf-8"))
    enrich_keys = {(e["name"], e["source"], e["edition"]) for e in enrich}
    missing = [s for s in spells if (s["name"], s["source_5e"], s["edition"]) not in enrich_keys]
    assert not missing, f"无富化法术 {len(missing)} 条: {[(m['name'], m['source_5e']) for m in missing[:5]]}"
    bad = [e for e in enrich if not (e.get("summary") or "").strip() or not e.get("keywords")]
    assert not bad, f"summary/keywords 为空 {len(bad)} 条"


@pytest.mark.skipif(not SPELLS.exists(), reason="未生成 spells_chm.json")
def test_enrich_keys_match_5e_source_codes() -> None:
    """enrich 的 source 必须与 spells_chm 的 source_5e 一致（同一命名空间）。"""
    spells = json.loads(SPELLS.read_text(encoding="utf-8"))
    enrich = json.loads(ENRICH.read_text(encoding="utf-8"))
    enrich_sources = {e["source"] for e in enrich}
    spell_sources = {s["source_5e"] for s in spells}
    # 允许 enrich 里存在库外旧码（如 reprintedAs 跳转的残留），但库内法术必须全有
    assert spell_sources <= enrich_sources | {"T:" + s for s in spell_sources if s.startswith("T:")}

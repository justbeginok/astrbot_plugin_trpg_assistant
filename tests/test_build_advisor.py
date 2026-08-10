"""build_advisor.py（构筑咨询纯函数层）单元测试。

覆盖点：
  - 分词 / 别名归一 / CJK 复合词抽取（「前排打手」→ 坦克）。
  - 全施法者/半施法者法术环上限表。
  - check_prereqs 前置标注（✅/❌/⚠️，标注不过滤）。
  - assemble_new_build：无目标词兜底、版本过滤、档案结构、子职归属职业。
  - assemble_level_up：特性时间线（按卡面版本去双版本行）、专长候选前置标注、
    施法职业法术建议（职业法术表）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from astrbot_plugin_trpg_assistant.build_advisor import (
    _extract_tag_terms,
    _max_spell_level,
    assemble_level_up,
    assemble_new_build,
    check_prereqs,
    dossier_to_text,
    normalize_term,
    split_terms,
)
from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"
LOOKUP = Path(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"


@pytest.fixture()
def kb(tmp_path: Path) -> KnowledgeBaseManager:
    out = tmp_path / "kb" / "dnd_kb.db"
    build(
        FIXTURE_DIR, out, commit="fixture-abc123",
        patch_root=NO_PATCH_DIR, en_lookup=LOOKUP,
    )
    return KnowledgeBaseManager(out)


def _sheet(**kw) -> SimpleNamespace:
    defaults = dict(
        name="测试卡",
        edition="2014",
        classes=[SimpleNamespace(class_name="战士", subclass="", level=1)],
        race="人类",
        feats=[],
        ability_scores=SimpleNamespace(
            strength=10, dexterity=10, constitution=10,
            intelligence=10, wisdom=14, charisma=10,
        ),
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# 分词 / 归一 / 复合词抽取
# ---------------------------------------------------------------------------


class TestTerms:
    def test_split_terms(self) -> None:
        assert split_terms("前排打手") == ["前排打手"]
        assert split_terms("坦克 近战，爆发、承伤") == ["坦克", "近战", "爆发", "承伤"]

    def test_normalize_alias(self) -> None:
        # 前排 → 坦克（职业/子职/种族词表别名一致）
        assert normalize_term("前排") == "坦克"
        assert normalize_term("回血") == "治疗"

    def test_extract_tag_terms_compound(self) -> None:
        # CJK 复合词抽取：取最长别名命中
        assert "坦克" in _extract_tag_terms("前排打手")
        assert _extract_tag_terms("治疗奶妈") == ["治疗"]

    def test_extract_no_hit(self) -> None:
        assert _extract_tag_terms("随便什么") == []


# ---------------------------------------------------------------------------
# 法术环上限
# ---------------------------------------------------------------------------


class TestMaxSpellLevel:
    def test_full_caster(self) -> None:
        assert _max_spell_level("full", 1) == 1
        assert _max_spell_level("full", 5) == 3
        assert _max_spell_level("full", 7) == 4
        assert _max_spell_level("full", 17) == 9
        assert _max_spell_level("full", 20) == 9

    def test_half_and_third_and_pact(self) -> None:
        assert _max_spell_level("1/2", 7) == 2  # 圣武士 7 级 → 2 环
        assert _max_spell_level("artificer", 9) == 3
        assert _max_spell_level("1/3", 7) == 2  # 奥法骑士 7 级 → 2 环
        assert _max_spell_level("1/3", 13) == 3
        assert _max_spell_level("pact", 7) == 4
        assert _max_spell_level("pact", 11) == 5

    def test_non_caster(self) -> None:
        assert _max_spell_level("", 10) == 0


# ---------------------------------------------------------------------------
# 前置标注（标注不过滤）
# ---------------------------------------------------------------------------


class TestCheckPrereqs:
    def test_ability_satisfied_and_missing(self) -> None:
        sheet = _sheet()
        marks = check_prereqs(
            {"prereq_ability": ["感知 13", "力量 13"]}, sheet
        )
        assert "✅感知13" in marks
        assert "❌缺力量13" in marks

    def test_race_and_feat(self) -> None:
        sheet = _sheet(race="银龙龙裔", feats=["巨武器大师"])
        marks = check_prereqs(
            {"prereq_race": ["龙裔"], "prereq_feat": ["巨武器大师"]}, sheet
        )
        assert "✅种族「龙裔」" in marks
        assert "✅专长「巨武器大师」" in marks

    def test_race_feat_missing(self) -> None:
        sheet = _sheet(race="人类", feats=[])
        marks = check_prereqs(
            {"prereq_race": ["精灵"], "prereq_feat": ["巨人打击（云雾打击）"]}, sheet
        )
        assert "❌需种族「精灵」" in marks
        # 去括号基础名匹配
        assert "❌需专长「巨人打击」" in marks

    def test_small_race_and_feature_unverifiable(self) -> None:
        sheet = _sheet(race="人类", feats=[])
        marks = check_prereqs(
            {"prereq_race": ["小型种族"], "prereq_feature": ["战斗风格"]}, sheet
        )
        assert any("⚠️" in m and "小型种族" in m for m in marks)
        assert any("⚠️" in m and "战斗风格" in m for m in marks)

    def test_malformed_ability(self) -> None:
        sheet = _sheet()
        marks = check_prereqs({"prereq_ability": ["感知 十三"]}, sheet)
        assert any("⚠️" in m for m in marks)


# ---------------------------------------------------------------------------
# assemble_new_build（fixture 无能力标签 → 主要覆盖兜底与结构）
# ---------------------------------------------------------------------------


class TestNewBuild:
    def test_fallback_structure(self, kb: KnowledgeBaseManager) -> None:
        d = assemble_new_build("", "", kb, edition="", level=0)
        assert "goal_tags" in d and d["goal_tags"] == {}
        assert "classes" in d and "races" in d and "backgrounds" in d
        assert "feats" in d and "spells" in d and "hint" in d
        assert d["edition"] == "双版本（已标注）"
        # 全部条目来自知识库（fixture 内真实存在的职业）
        assert all(c["name"] in ("战士", "法师") for c in d["classes"])

    def test_edition_filter(self, kb: KnowledgeBaseManager) -> None:
        d = assemble_new_build("", "", kb, edition="2024", level=15)
        assert d["edition"] == "2024"
        # 版本后过滤：全部条目 edition 为 2024（fixture 中 火球术 有 XPHB 行）
        for fam in ("races", "backgrounds", "feats"):
            for e in d[fam]:
                assert e["version"].endswith("·2024") or "2024" in e["version"]

    def test_goal_with_unknown_term_falls_back(
        self, kb: KnowledgeBaseManager
    ) -> None:
        # fixture 无能力标签：goal 词库内不存在 → 丢弃后走兜底（不报错）
        d = assemble_new_build("不存在词", "", kb, edition="", level=1)
        assert d["goal_tags"] == {}
        assert isinstance(d["classes"], list)

    def test_dossier_json_serializable(self, kb: KnowledgeBaseManager) -> None:
        d = assemble_new_build("", "", kb, edition="", level=0)
        text = dossier_to_text(d)
        assert text.startswith("{") and text.endswith("}")
        assert len(text) < 4000  # token 上限内的兜底档案


# ---------------------------------------------------------------------------
# assemble_level_up
# ---------------------------------------------------------------------------


class TestLevelUp:
    def test_timeline_next_level(self, kb: KnowledgeBaseManager) -> None:
        # 战士 1→2：fixture 特性「动作如潮」在 2 级
        d = assemble_level_up(_sheet(), kb)
        names = [t["name"] for t in d["class_features_timeline"]]
        assert "动作如潮" in names
        assert all(t["level"] == 2 for t in d["class_features_timeline"])

    def test_timeline_edition_dedup(self, kb: KnowledgeBaseManager) -> None:
        # 2024 卡只出 2024 行（fixture 战士 PHB/XPHB 双版本同名特性不重复）
        sheet = _sheet(edition="2024")
        d = assemble_level_up(sheet, kb)
        names = [t["name"] for t in d["class_features_timeline"]]
        assert len(names) == len(set(names))

    def test_no_classes_error(self, kb: KnowledgeBaseManager) -> None:
        d = assemble_level_up(_sheet(classes=[]), kb)
        assert "error" in d

    def test_feat_candidates_with_prereq_marks(
        self, kb: KnowledgeBaseManager
    ) -> None:
        # 感知 14 → 满足「感知 13」前置的专长应带 ✅感知13 标注
        # （fixture 中 仪式施法者 XPHB/2024 带感知 13 前置）
        sheet = _sheet(edition="2024")
        d = assemble_level_up(sheet, kb)
        for f in d["feat_candidates"]:
            assert "prereq_check" in f
        satisfied = [
            f for f in d["feat_candidates"]
            if any("✅感知13" in m for m in f["prereq_check"])
        ]
        assert satisfied

    def test_spells_by_class_for_caster(self, kb: KnowledgeBaseManager) -> None:
        # 法师 6→7：职业法术表建议（fixture 6 行 spell_classes）
        sheet = _sheet(
            edition="2014",
            classes=[SimpleNamespace(class_name="法师", subclass="", level=6)],
            ability_scores=SimpleNamespace(
                strength=8, dexterity=14, constitution=14,
                intelligence=20, wisdom=10, charisma=8,
            ),
        )
        d = assemble_level_up(sheet, kb)
        assert "spell_note" in d and "法师" in d["spell_note"]
        assert len(d["spells"]) >= 5
        assert all("version" in s for s in d["spells"])

    def test_non_caster_no_spells(self, kb: KnowledgeBaseManager) -> None:
        d = assemble_level_up(_sheet(), kb)  # 战士
        assert d["spells"] == []

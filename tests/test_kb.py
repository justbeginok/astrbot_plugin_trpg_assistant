"""kb.py 查询层单元测试：基于 fixture 构建的测试库驱动全部查询路径。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from astrbot_plugin_trpg_assistant.kb import (
    KnowledgeBaseManager,
    resolve_db_path,
)
from astrbot_plugin_trpg_assistant.kb_enums import (
    edition_of_source,
    format_rarity,
    resolve_rarity,
)
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
# 测试构建时隔离真实补丁目录（kb_patches/），避免真实补丁污染 fixture 计数。
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


@pytest.fixture()
def manager(tmp_path: Path) -> KnowledgeBaseManager:
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return KnowledgeBaseManager(out)


# ---------------------------------------------------------------------------
# search：三档命中
# ---------------------------------------------------------------------------


def test_search_exact_alias(manager: KnowledgeBaseManager) -> None:
    hits = manager.search("火球术")
    assert len(hits) == 2  # 同名两个版本各一行
    assert {h.source for h in hits} == {"PHB", "XPHB"}

    # 英文名别名也可命中
    hits = manager.search("fireball")
    assert {h.name for h in hits} == {"火球术"}


def test_search_like_partial(manager: KnowledgeBaseManager) -> None:
    hits = manager.search("火球")
    names = {h.name for h in hits}
    assert "火球术" in names
    assert "火球法杖" in names  # LIKE 命中物品名


def test_search_typo_shorten(manager: KnowledgeBaseManager) -> None:
    # 「火球木」无匹配 → 逐字缩短到「火球」命中
    hits = manager.search("火球木")
    assert hits and "火球术" in {h.name for h in hits}


def test_search_kind_filter(manager: KnowledgeBaseManager) -> None:
    hits = manager.search("火球", kind="spell")
    assert {h.name for h in hits} == {"火球术"}
    hits = manager.search("火球", kind="item")
    assert {h.name for h in hits} == {"火球法杖"}


def test_search_empty_and_miss(manager: KnowledgeBaseManager) -> None:
    assert manager.search("") == []
    assert manager.search("不存在的条目") == []


def test_search_fulltext_flag(manager: KnowledgeBaseManager) -> None:
    # 默认不搜正文：「火焰舌剑」是逐字缩短到「火焰」的 NAME 命中；
    # 正文含「火焰伤害」的 火球术/燃烧之手 不出现。
    hits = manager.search("火焰伤害", limit=20)
    names = {h.name for h in hits}
    assert "火焰舌剑" in names
    assert "火球术" not in names
    # fulltext=True：body LIKE 追加命中火球术/燃烧之手
    hits = manager.search("火焰伤害", limit=20, fulltext=True)
    names = {h.name for h in hits}
    assert "火球术" in names
    assert "燃烧之手" in names


# ---------------------------------------------------------------------------
# detail：同名多版本
# ---------------------------------------------------------------------------


def test_detail_all_versions(manager: KnowledgeBaseManager) -> None:
    entries = manager.detail("火球术")
    assert len(entries) == 2
    by_src = {e.source: e for e in entries}
    assert by_src["PHB"].edition == "2014"
    assert by_src["XPHB"].edition == "2024"
    assert by_src["XPHB"].is_machine == 1  # translator=机翻
    assert by_src["PHB"].is_machine == 0
    assert "{@" not in by_src["PHB"].body


def test_detail_kind_and_miss(manager: KnowledgeBaseManager) -> None:
    assert len(manager.detail("火球术", kind="monster")) == 0
    assert manager.detail("不存在") == []


# ---------------------------------------------------------------------------
# filter：结构化过滤
# ---------------------------------------------------------------------------


def test_filter_monster_cr_and_type(manager: KnowledgeBaseManager) -> None:
    # 「挑战等级为 3 的龙类」
    result = manager.filter("monster", cr_min=3.0, cr_max=3.0, mtype="dragon")
    assert [r.name for r in result.entries] == ["少年青铜龙"]
    assert result.total == 1
    result = manager.filter("monster", cr_min=0.0, cr_max=0.5, mtype="beast")
    assert [r.name for r in result.entries] == ["恐狼"]
    # 不限类型只限 CR：成年红龙 CR17 不在
    result = manager.filter("monster", cr_min=0.0, cr_max=5.0)
    assert {r.name for r in result.entries} == {"少年青铜龙", "恐狼"}


def test_filter_spell_level_school(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("spell", level=0)
    assert [r.name for r in result.entries] == ["冰霜射线"]
    result = manager.filter("spell", level=3, school="V")
    assert {r.name for r in result.entries} == {"火球术"}


def test_filter_item_rarity(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("item", rarity="rare")
    # v0.41.0：魔法变体本体（焰舌/火焰抗性护甲，均 rare）并入筛选；
    # v0.42.1：恶毒武器（fixture rare 变体）并入。
    assert {r.name for r in result.entries} == {
        "火球法杖", "火焰舌剑", "机翻魔剑", "焰舌", "火焰抗性护甲", "恶毒武器",
    }


def test_filter_item_rarity_none_and_magic(manager: KnowledgeBaseManager) -> None:
    """非魔法物品（rarity=none）与魔法物品整体反查（v0.15.0）。"""
    result = manager.filter("item", rarity="none")
    assert {r.name for r in result.entries} == {
        "长剑", "手弩", "炽火胶 (扁瓶)", "皮甲", "盾牌",
    }
    assert all(r.rarity == "none" for r in result.entries)
    # 魔法物品整体反查：排除非魔法基础物品
    # （遗物之刃 3 形态 + 变体本体 4 = 13）
    result = manager.filter("item", rarity="magic")
    assert len(result.entries) == 13
    assert "长剑" not in {r.name for r in result.entries}
    assert all(r.rarity != "none" for r in result.entries)


def test_entry_meta_rarity_cn(manager: KnowledgeBaseManager) -> None:
    """筛选列表稀有度后缀中文显示（v0.15.0）。"""
    for e in manager.filter("item", rarity="rare").entries:
        assert KnowledgeBaseManager._entry_meta(e) == "（珍稀）"
    for e in manager.filter("item", rarity="none").entries:
        assert KnowledgeBaseManager._entry_meta(e) == "（非魔法物品）"


def test_edition_of_source_2024_sources() -> None:
    """v0.26.1：2024 规则源判定（含 FRHoF 官方缩写含小写 o 的 upper 归一坑）。"""
    # 2024 核心书 + 2024 数字补充书
    for s in ("XPHB", "XDMG", "XMM", "ABH", "EFA", "FRHoF", "frhof", "LFL", "RHW"):
        assert edition_of_source(s) == "2024", s
    # 老书 / UA 保持原样
    assert edition_of_source("ERLW") == "2014"
    assert edition_of_source("PHB") == "2014"
    assert edition_of_source("UA2020-01") == "other"


def test_format_rarity_cn() -> None:
    """稀有度显示文案：全 10 值映射 + 未知名兜底 + 反查双向。"""
    assert format_rarity("common") == "普通"
    assert format_rarity("uncommon") == "非普通"
    assert format_rarity("rare") == "珍稀"
    assert format_rarity("very rare") == "极珍稀"
    assert format_rarity("legendary") == "传说"
    assert format_rarity("artifact") == "神器"
    assert format_rarity("none") == "非魔法物品"
    assert format_rarity("varies") == "多种稀有度"
    assert format_rarity("unknown") == "未知"
    assert format_rarity("unknown (magic)") == "未知稀有度"
    assert format_rarity("no-such-value") == "no-such-value"  # 兜底原值
    # 反查双向：中文 → 英文（多种稀有度 / 未知稀有度）
    assert resolve_rarity("多种稀有度") == "varies"
    assert resolve_rarity("未知稀有度") == "unknown (magic)"
    assert resolve_rarity("各不相同") == "varies"  # 旧别名仍可反查


def test_filter_invalid_kind(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("condition", rarity="rare")
    assert result.entries == []
    assert result.total == 0


def test_filter_feat(manager: KnowledgeBaseManager) -> None:
    """专长筛选（v0.25.0）：类型 / 属性提升 / 先决种族·属性·专长·特性标签。"""
    # 类型
    result = manager.filter("feat", tags=[("feat_type", "通用")])
    assert {r.name for r in result.entries} == {"幸运", "仪式施法者"}
    # 属性提升：choose 六属性展开（幸运 2024 含力量）
    result = manager.filter("feat", tags=[("ability_increase", "力量")])
    assert {r.name for r in result.entries} == {"幸运"}
    # 先决种族（具体到名字）
    result = manager.filter("feat", tags=[("prereq_race", "精灵")])
    assert [r.name for r in result.entries] == ["精灵之准"]
    # 先决属性门槛
    result = manager.filter("feat", tags=[("prereq_ability", "智力 13")])
    assert [r.name for r in result.entries] == ["仪式施法者"]
    # 先决专长：去括号基础名可命中
    result = manager.filter("feat", tags=[("prereq_feat", "巨人打击")])
    assert [r.name for r in result.entries] == ["云巨人之诡诈"]
    # 组合：类型 + 属性门槛（AND 交集）
    result = manager.filter(
        "feat",
        tags=[("feat_type", "通用"), ("prereq_ability", "智力 13")],
    )
    assert [r.name for r in result.entries] == ["仪式施法者"]


def test_resolve_feat_free_term(manager: KnowledgeBaseManager) -> None:
    """专长裸词自由文本维度判定（/筛专长 裸词消歧）。"""
    assert manager.resolve_feat_free_term("精灵") == "prereq_race"
    assert manager.resolve_feat_free_term("巨人打击") == "prereq_feat"
    assert manager.resolve_feat_free_term("战斗风格") == "prereq_feature"
    assert manager.resolve_feat_free_term("不存在的词") is None


# ---------------------------------------------------------------------------
# filter：特性标签反查（v0.13.0）
# ---------------------------------------------------------------------------


def test_filter_monster_by_damage(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("monster", tags=[("dmg_dealt", "火焰")])
    assert [r.name for r in result.entries] == ["成年红龙"]
    # 组合：伤害 + CR 上限（成年红龙 CR17 被排除）
    result = manager.filter(
        "monster", tags=[("dmg_dealt", "火焰")], cr_max=10.0
    )
    assert result.entries == []
    # 免疫的「闪电」不会混入 dmg_dealt（动作区提取）
    result = manager.filter("monster", tags=[("dmg_dealt", "闪电")])
    assert [r.name for r in result.entries] == ["少年青铜龙"]
    # 环境 + 条件反查
    result = manager.filter("monster", tags=[("environment", "海岸")])
    assert [r.name for r in result.entries] == ["少年青铜龙"]
    result = manager.filter("monster", tags=[("condition_immune", "恐慌")])
    assert [r.name for r in result.entries] == ["少年青铜龙"]
    result = manager.filter("monster", tags=[("condition_inflict", "受擒")])
    assert [r.name for r in result.entries] == ["恐狼"]


def test_filter_spell_by_tags(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("spell", tags=[("spell_shape", "锥形")])
    assert [r.name for r in result.entries] == ["燃烧之手"]
    result = manager.filter(
        "spell", level=4, tags=[("dmg_dealt", "暗蚀")]
    )
    assert [r.name for r in result.entries] == ["枯萎术"]
    # 专注 + 成分 + 距离组合（人类定身术 VSM 也含言语+姿势）
    result = manager.filter(
        "spell",
        concentration=True,
        tags=[("spell_component", "言语"), ("spell_component", "姿势")],
    )
    assert {r.name for r in result.entries} == {"枯萎术", "人类定身术"}
    # 按学派（单字母）反查
    result = manager.filter("spell", school="E")
    assert [r.name for r in result.entries] == ["人类定身术"]
    result = manager.filter("spell", range_max=40, range_type="feet")
    names = {r.name for r in result.entries}
    assert "枯萎术" in names  # 30 尺
    assert "火球术" not in names  # 150 尺
    # 目标类型反查
    result = manager.filter("spell", tags=[("spell_target", "单体")])
    assert {"冰霜射线", "枯萎术", "人类定身术"} <= {r.name for r in result.entries}


def test_filter_item_by_tags(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("item", tags=[("weapon_property", "灵巧")])
    assert {r.name for r in result.entries} == {"火焰舌剑", "暗蚀之刃", "长剑"}
    result = manager.filter("item", tags=[("dmg_dealt", "暗蚀")])
    assert [r.name for r in result.entries] == ["暗蚀之刃"]
    result = manager.filter(
        "item", tags=[("dmg_dealt", "暗蚀"), ("weapon_property", "灵巧")]
    )
    assert [r.name for r in result.entries] == ["暗蚀之刃"]
    result = manager.filter("item", attunement=True, rarity="rare")
    assert {r.name for r in result.entries} == {
        "火球法杖", "火焰舌剑", "机翻魔剑", "焰舌", "火焰抗性护甲",
    }


def test_filter_item_by_base_and_type(manager: KnowledgeBaseManager) -> None:
    # base_item：以「长剑」为原型的魔法武器（不含长剑本身）
    result = manager.filter("item", tags=[("base_item", "长剑")])
    assert {r.name for r in result.entries} == {"火焰舌剑", "暗蚀之刃", "机翻魔剑"}
    # item_type：武器/权杖/药水（含遗物之刃 3 形态）
    result = manager.filter("item", tags=[("item_type", "武器")])
    assert {r.name for r in result.entries} == {
        "长剑", "手弩", "火焰舌剑", "暗蚀之刃", "机翻魔剑",
        "遗物之刃（休眠）", "遗物之刃（觉醒）", "遗物之刃（升华）",
    }
    result = manager.filter("item", tags=[("item_type", "权杖")])
    assert [r.name for r in result.entries] == ["火球法杖"]
    # 组合：基础物品 + 伤害
    result = manager.filter(
        "item", tags=[("base_item", "长剑"), ("dmg_dealt", "暗蚀")]
    )
    assert [r.name for r in result.entries] == ["暗蚀之刃"]


def test_filter_sort_by_cr(manager: KnowledgeBaseManager) -> None:
    """怪物筛选按 CR 升序（0.25 恐狼 → 3 少年青铜龙 → 17 成年红龙）。"""
    result = manager.filter("monster")
    assert [r.name for r in result.entries] == ["恐狼", "少年青铜龙", "成年红龙"]
    # 侧表字段已填充，可显示 CR
    assert result.entries[0].cr == 0.25


# ---------------------------------------------------------------------------
# class_features
# ---------------------------------------------------------------------------


def test_class_features_base_and_subclass(manager: KnowledgeBaseManager) -> None:
    result = manager.class_features("战士")
    assert result.class_name == "战士"
    assert result.eng_name == "Fighter"
    assert result.editions == ["2014"]
    assert [r.name for r in result.base_rows] == ["战斗风格", "动作如潮"]
    # 候选列表展示显示名（name），而非短名（shortName）
    assert result.subclass_candidates == ["冠军武士"]
    assert result.subclass_rows == []


def test_class_features_with_subclass(manager: KnowledgeBaseManager) -> None:
    # 显示名匹配（name != shortName 时也必须能查到）
    result = manager.class_features("战士", subclass="冠军武士")
    assert [r.name for r in result.subclass_rows] == ["精通重击", "非凡运动家"]
    assert result.subclass_rows[0].level == 3
    # 短名同样可匹配（双通道）
    result = manager.class_features("战士", subclass="冠军")
    assert [r.name for r in result.subclass_rows] == ["精通重击", "非凡运动家"]


def test_class_features_unknown(manager: KnowledgeBaseManager) -> None:
    result = manager.class_features("不存在职业")
    assert result.base_rows == []
    assert result.subclass_candidates == []


def test_class_features_feature_all(manager: KnowledgeBaseManager) -> None:
    """v0.29.0：feature="*" → 保留全部本职特性，进入细化模式。"""
    result = manager.class_features("战士", feature="*")
    assert result.feature_query == "*"
    assert [r.name for r in result.base_rows] == ["战斗风格", "动作如潮"]
    # 细化模式下子职字段为空
    assert result.subclass_rows == []
    assert result.subclass_candidates == []


def test_class_features_feature_single(manager: KnowledgeBaseManager) -> None:
    """v0.29.0：feature=特性名 → 只保留跨版本匹配的本职特性行。"""
    result = manager.class_features("战士", feature="动作如潮")
    assert result.feature_query == "动作如潮"
    assert [r.name for r in result.base_rows] == ["动作如潮"]

    # 未匹配 → 空列表（命令层会提示未找到）
    result = manager.class_features("战士", feature="不存在的特性")
    assert result.base_rows == []
    assert result.feature_query == "不存在的特性"


def test_class_features_feature_empty_str_is_all(manager: KnowledgeBaseManager) -> None:
    """feature="" 按全部处理（命令层把空第三段归一为 *）。"""
    result = manager.class_features("战士", feature="")
    assert result.feature_query == "*"
    assert len(result.base_rows) == 2


# ---------------------------------------------------------------------------
# version / 路径解析
# ---------------------------------------------------------------------------


def test_version(manager: KnowledgeBaseManager) -> None:
    v = manager.version()
    assert v["source_commit"] == "fixture-abc123"
    assert v["data_version"].startswith("fixture-abc123-")
    text = KnowledgeBaseManager.format_version(v)
    assert "CC BY-NC-SA 4.0" in text


def test_resolve_db_path(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin.db"
    update = tmp_path / "kb_update.db"
    assert resolve_db_path(builtin, update) == builtin  # update 不存在 → 内置
    update.write_bytes(b"x")
    assert resolve_db_path(builtin, update) == update  # update 存在 → 优先
    assert resolve_db_path(builtin, update, prefer_update=False) == builtin


def test_resolve_db_path_schema_fallback(tmp_path: Path) -> None:
    """旧 schema 的 kb_update.db 必须回退到新版内置库（v0.13.0 防坑）。"""
    builtin = tmp_path / "builtin.db"
    build(FIXTURE_DIR, builtin, commit="fixture-abc123", patch_root=NO_PATCH_DIR)  # schema v2
    old_update = tmp_path / "kb_update.db"
    conn = sqlite3.connect(str(old_update))
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO meta VALUES ('schema_version', '1')"
    )
    conn.commit()
    conn.close()
    # v1 更新库存在 → 仍应选内置 v2 库
    assert resolve_db_path(builtin, old_update) == builtin
    # v2 更新库 → 优先更新库
    new_update = tmp_path / "kb_update_v2.db"
    build(FIXTURE_DIR, new_update, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    assert resolve_db_path(builtin, new_update) == new_update


def test_unavailable_db(tmp_path: Path) -> None:
    mgr = KnowledgeBaseManager(tmp_path / "nope.db")
    assert not mgr.available
    assert mgr.version() == {}


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


def test_format_detail_dual_version(manager: KnowledgeBaseManager) -> None:
    entries = manager.detail("火球术")
    text = KnowledgeBaseManager.format_detail(entries)
    assert "找到 2 个版本" in text
    assert "【火球术 Fireball】[XPHB·2024] ⚠️机翻" in text
    assert "【火球术 Fireball】[PHB·2014]" in text
    assert "以上内容来自知识库原文" not in text  # 命令直查不加 LLM 约束句


def test_format_hits(manager: KnowledgeBaseManager) -> None:
    hits = manager.search("火球木")
    text = KnowledgeBaseManager.format_hits(hits)
    assert "相近候选" in text
    assert "1." in text


def test_format_class_features(manager: KnowledgeBaseManager) -> None:
    result = manager.class_features("战士")
    text = KnowledgeBaseManager.format_class_features(result)
    assert "【战士 Fighter】" in text
    assert "1级：战斗风格" in text
    assert "可选子职：冠军武士" in text

    result = manager.class_features("战士", subclass="冠军武士")
    text = KnowledgeBaseManager.format_class_features(result)
    assert "◆ 3 级 精通重击" in text
    # 子职特性输出全文（含 list 块与嵌套 entries 块），而非仅摘要
    assert "增益一：跳跃距离翻倍" in text
    assert "额外增益：" in text
    assert "当你处于优势时，额外获得 +2 加值。" in text
    assert "◆ 7 级 非凡运动家" in text
    # v0.29.0：非细化模式且该职业有基础特性 → 末尾提示可细化
    assert "查职业 <职业> 特性" in text


def test_format_class_features_detail_all(manager: KnowledgeBaseManager) -> None:
    """v0.29.0：feature="*" → 输出全部本职特性完整正文（按版本分组）。"""
    result = manager.class_features("战士", feature="*")
    text = KnowledgeBaseManager.format_class_features(result)
    assert "【战士 Fighter】" in text
    assert "【2014 版 · 本职特性】" in text
    assert "◆ 1 级 战斗风格：" in text
    assert "你采取一种特别的作战风格作为专长。" in text
    assert "◆ 2 级 动作如潮：" in text
    assert "额外进行一次动作" in text
    # 细化模式不再输出名字总表与子职候选
    assert "可选子职" not in text
    assert "回复「查职业 <职业> 特性」" not in text


def test_format_class_features_detail_single(manager: KnowledgeBaseManager) -> None:
    """v0.29.0：feature=特性名 → 只输出该特性正文，标题带特性名。"""
    result = manager.class_features("战士", feature="动作如潮")
    text = KnowledgeBaseManager.format_class_features(result)
    assert "【战士 Fighter】" in text
    assert "特性「动作如潮」" in text
    assert "◆ 2 级 动作如潮：" in text
    assert "额外进行一次动作" in text
    # 其他特性不出现在正文中
    assert "战斗风格" not in text


def test_format_class_features_detail_not_found(manager: KnowledgeBaseManager) -> None:
    """v0.29.0：细化模式查无该特性 → 明确提示。"""
    result = manager.class_features("战士", feature="不存在的特性")
    text = KnowledgeBaseManager.format_class_features(result)
    assert "未找到该职业的「不存在的特性」特性" in text


# ---------------------------------------------------------------------------
# 格式化：跨库广搜 / 特性筛选（v0.13.0）
# ---------------------------------------------------------------------------


def test_format_hits_grouped(manager: KnowledgeBaseManager) -> None:
    hits = manager.search("火球", limit=20)
    text = KnowledgeBaseManager.format_hits_grouped(hits, query="火球", limit=20)
    assert "跨库搜索「火球」结果" in text
    assert "【法术】" in text
    assert "【物品】" in text
    assert "火球术" in text
    assert "火球法杖" in text
    # 空结果
    empty = KnowledgeBaseManager.format_hits_grouped([], query="不存在")
    assert "未找到" in empty


def test_format_filter_result(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("monster", tags=[("dmg_dealt", "火焰")])
    text = KnowledgeBaseManager.format_filter_result(
        result, "怪物", unknown=["会飞"]
    )
    assert "共 1 条符合条件的怪物" in text
    assert "成年红龙" in text
    assert "CR 17" in text  # 侧表字段展示
    assert "未识别条件：会飞" in text
    assert "收窄" in text


def test_format_filter_result_empty(manager: KnowledgeBaseManager) -> None:
    result = manager.filter("monster", tags=[("dmg_dealt", "火焰")], cr_max=5.0)
    text = KnowledgeBaseManager.format_filter_result(result, "怪物")
    assert "没有符合条件的怪物" in text


# ---------------------------------------------------------------------------
# 状态 / 种族（v0.16.0）
# ---------------------------------------------------------------------------


def test_detail_condition_dual_version(manager: KnowledgeBaseManager) -> None:
    """状态：同名 2014/2024 双版本全部返回。"""
    entries = manager.detail("目盲", kind="condition")
    assert {e.source for e in entries} == {"PHB", "XPHB"}
    assert all(e.kind == "condition" for e in entries)
    # status 合并进来的「专注」也可查
    assert len(manager.detail("专注", kind="condition")) == 1


def test_detail_race(manager: KnowledgeBaseManager) -> None:
    """种族详情：同名多版本 + 头部信息渲染。"""
    entries = manager.detail("阿斯莫", kind="race")
    assert {e.source for e in entries} == {"DMG", "MPMM"}
    body = next(e.body for e in entries if e.source == "MPMM")
    assert "【种族信息】" in body
    assert "体型：中型" in body
    assert "速度：步行30尺、飞行30尺" in body  # fly=true → 30
    assert "黑暗视觉：60尺" in body
    assert "生物类型：类人生物" in body
    assert "抗性：暗蚀、火焰" in body  # choose 展开
    assert "天生施法：致盲术、舞光术" in body


def test_filter_race_speed_and_darkvision(manager: KnowledgeBaseManager) -> None:
    """种族数值反查：速度类型/范围（裸值=至少）+ 黑暗视觉。"""
    # 有飞行速度的种族
    result = manager.filter("race", speed_type="fly")
    assert [r.name for r in result.entries] == ["阿斯莫"]
    # 飞行速度 ≥ 60 → 无（阿斯莫 MPMM 只有 30）
    assert manager.filter("race", speed_type="fly", speed_min=60).entries == []
    # 步行 ≥ 30 → 全部 6 个种族（walk 都是 30）
    assert manager.filter("race", speed_min=30).total == 6
    # 步行 ≥ 31 → 空
    assert manager.filter("race", speed_min=31).entries == []
    # 黑暗视觉 ≥ 60 → 阿斯莫×2 + 半精灵；≥ 120 → 空
    dv = manager.filter("race", darkvision_min=60)
    assert {r.name for r in dv.entries} == {"阿斯莫", "半精灵"}
    assert manager.filter("race", darkvision_min=120).entries == []


def test_filter_race_tags(manager: KnowledgeBaseManager) -> None:
    """种族标签反查：体型/生物类型/抗性/天生施法。"""
    # 体型：中型 → 全部（含半精灵 ["S","M"]）；小型 → 仅半精灵
    assert manager.filter("race", tags=[("size", "M")]).total == 6
    assert [r.name for r in manager.filter(
        "race", tags=[("size", "S")]
    ).entries] == ["半精灵"]
    # 生物类型
    assert [r.name for r in manager.filter(
        "race", tags=[("creature_type", "不死生物")]
    ).entries] == ["骷髅"]
    assert [r.name for r in manager.filter(
        "race", tags=[("creature_type", "泥怪")]
    ).entries] == ["流浆体"]
    assert manager.filter(
        "race", tags=[("creature_type", "类人生物")]
    ).total == 4  # 阿斯莫×2 + 半精灵 + 矮人
    # 天生抗性（文本 + choose 双通道）：火焰 → 阿斯莫两版本
    assert manager.filter("race", tags=[("dmg_resist", "火焰")]).total == 2
    # 天生施法：文本 {@spell} 与 2024 additionalSpells（innate+known）
    assert [r.name for r in manager.filter(
        "race", tags=[("innate_spell", "火球术")]
    ).entries] == ["阿斯莫"]
    assert manager.filter("race", tags=[("innate_spell", "舞光术")]).total == 1
    assert manager.filter("race", tags=[("innate_spell", "致盲术")]).total == 1


def test_entry_meta_race(manager: KnowledgeBaseManager) -> None:
    """筛选列表种族后缀：速度展示。"""
    entries = manager.filter("race", tags=[("size", "S")]).entries
    assert KnowledgeBaseManager._entry_meta(entries[0]) == "（步行30尺）"
    e = manager.filter("race", tags=[("speed_type", "攀爬")]).entries[0]
    assert KnowledgeBaseManager._entry_meta(e) == "（步行30尺｜攀爬30尺）"


# ---------------------------------------------------------------------------
# v0.18.0 规则引擎侧表查询（schema v4）
# ---------------------------------------------------------------------------


def test_class_combat_fighter(manager: KnowledgeBaseManager) -> None:
    row = manager.class_combat("战士")
    assert row is not None
    assert row.hd_faces == 10
    assert row.saves == ["str", "con"]
    assert row.caster == ""  # 非施法者
    assert row.spell_ability == ""


def test_class_combat_wizard_full_caster(manager: KnowledgeBaseManager) -> None:
    row = manager.class_combat("法师")
    assert row is not None
    assert row.hd_faces == 6
    assert row.caster == "full"
    assert row.spell_ability == "int"
    assert row.is_caster is True
    # 按版本过滤：fixture 法师只有 PHB（2014）
    assert manager.class_combat("法师", edition="2024") is None


def test_class_combat_unknown(manager: KnowledgeBaseManager) -> None:
    assert manager.class_combat("不存在职业") is None


def test_subclass_caster(manager: KnowledgeBaseManager) -> None:
    # 奥法骑士 1/3；caster 为空的重复声明行已被构建期过滤
    assert manager.subclass_caster("战士", "奥法骑士") == ("1/3", "int")
    # 无子职施法进度 → None
    assert manager.subclass_caster("战士", "冠军武士") is None
    assert manager.subclass_caster("战士", "不存在") is None


def test_race_ability_half_elf(manager: KnowledgeBaseManager) -> None:
    offer = manager.race_ability("半精灵", edition="2014")
    assert offer is not None
    assert offer.flat == {"cha": 2}
    assert len(offer.chooses) == 1
    spec = offer.chooses[0]
    assert spec.kind == "count"
    assert spec.count == 2
    assert set(spec.from_set) == {"str", "dex", "con", "int", "wis"}
    assert offer.has_choice is True


def test_race_ability_dwarf_flat_only(manager: KnowledgeBaseManager) -> None:
    offer = manager.race_ability("矮人")
    assert offer is not None
    assert offer.flat == {"con": 2}
    assert offer.chooses == []
    assert offer.has_choice is False


def test_race_ability_none(manager: KnowledgeBaseManager) -> None:
    # 阿斯莫（DMG/MPMM）无结构化 ability；2024 种族也无
    assert manager.race_ability("阿斯莫") is None
    assert manager.race_ability("半精灵", edition="2024") is None


def test_background_ability_xphb_weighted(manager: KnowledgeBaseManager) -> None:
    offer = manager.background_ability("侍僧")
    assert offer is not None
    assert offer.flat == {}
    assert len(offer.chooses) == 2  # [2,1] 与 [1,1,1] 二选一
    assert [c.kind for c in offer.chooses] == ["weighted", "weighted"]
    assert offer.chooses[0].weights == [2, 1]
    assert offer.chooses[1].weights == [1, 1, 1]
    assert set(offer.chooses[0].from_set) == {"int", "wis", "cha"}
    # PHB 背景无 ability（库中只有 XPHB 侍僧有行）
    assert manager.background_ability("士兵") is None


def test_race_speed(manager: KnowledgeBaseManager) -> None:
    """v0.23.0：种族步行速度（races.speed_walk）。"""
    assert manager.race_speed("矮人") == 30
    assert manager.race_speed("阿斯莫") == 30
    assert manager.race_speed("不存在种族") is None
    assert manager.race_speed("") is None


def test_race_speed_subrace_fallback(manager: KnowledgeBaseManager) -> None:
    """v0.24.0：子种族名（库中无独立条目）按基础种族回退（山丘矮人→矮人）。"""
    assert manager.race_speed("山丘矮人") == 30  # endswith 矮人 → 回退
    assert manager.race_speed("银龙龙裔") is None  # 无「龙裔」基础名 → 不回退
    assert manager.race_speed("半精灵") == 30  # 精确命中优先于「精灵」后缀回退


def test_race_features(manager: KnowledgeBaseManager) -> None:
    """v0.23.0：种族特性名提取（body 段落「名：」+ 基础键过滤）。"""
    feats = manager.race_features("矮人")
    assert "矮人抗性" in feats  # fixture 具名段落被提取
    # 纯文本段落（无「名：」）不产出特性名
    assert manager.race_features("阿斯莫") == []
    assert manager.race_features("半精灵") == []
    assert manager.race_features("不存在种族") == []


def test_race_features_subrace_fallback(manager: KnowledgeBaseManager) -> None:
    """v0.24.0：子种族名特性回退（山丘矮人 → 矮人 body 提取）。"""
    assert "矮人抗性" in manager.race_features("山丘矮人")
    assert manager.race_features("银龙龙裔") == []


def test_item_combat_armor_and_shield(manager: KnowledgeBaseManager) -> None:
    leather = manager.item_combat("皮甲")
    assert leather is not None
    assert leather.ac == 11 and leather.armor_type == "LA"
    assert leather.is_shield is False

    shield = manager.item_combat("盾牌")
    assert shield is not None
    assert shield.ac == 2 and shield.armor_type == "S"
    assert shield.is_shield is True


def test_item_combat_weapon_properties(manager: KnowledgeBaseManager) -> None:
    sword = manager.item_combat("长剑")
    assert sword is not None
    assert sword.dmg1 == "1d8"
    assert "F" in sword.properties  # PHB 被 reprintedAs 指向 XPHB，|后缀已切分
    assert sword.is_finesse is True
    assert sword.is_two_handed is False

    xbow = manager.item_combat("手弩")
    assert xbow is not None
    assert xbow.is_ranged is True


def test_item_combat_unknown(manager: KnowledgeBaseManager) -> None:
    assert manager.item_combat("不存在物品") is None


# --- v0.20.0 商店：item_price / list_init_shop_items / item_stats_lines ---


def test_item_price_xphb_preferred(manager: KnowledgeBaseManager) -> None:
    # 长剑 PHB(100 铜) 被 reprintedAs 跳转，只剩 XPHB(1500 铜)；手弩 2500 铜
    assert manager.item_price("长剑") == (1500, 3)
    assert manager.item_price("手弩") == (2500, 3)
    # 非物品 / 未知 → None
    assert manager.item_price("火球术") is None
    assert manager.item_price("不存在的物品") is None
    assert manager.item_price("") is None


def test_list_init_shop_items_dedup(manager: KnowledgeBaseManager) -> None:
    seeds = manager.list_init_shop_items()
    by_name = {s[0]: s for s in seeds}
    # 只收 PHB/XPHB 非魔法有价物品；同名去重后 2024/XPHB 优先
    assert len(seeds) == 5
    assert by_name["长剑"][1] == "XPHB"  # XPHB 优先（PHB 版被 reprintedAs 跳转）
    assert by_name["长剑"][3] == 1500
    assert by_name["手弩"][3] == 2500
    assert by_name["手弩"][4] == 3  # 重量随行
    # 无 value 的物品（如魔法物品）不入候选
    assert "火球法杖" not in by_name


def test_item_stats_lines(manager: KnowledgeBaseManager) -> None:
    entries = manager.detail("长剑", kind="item")
    lines = manager.item_stats_lines(entries)
    assert len(lines) == 1
    assert "价值：15金" in lines[0]
    assert "重量：3 磅" in lines[0]
    # 非物品无附加行
    assert manager.item_stats_lines(manager.detail("火球术")) == []
    assert manager.item_stats_lines([]) == []



# ---------------------------------------------------------------------------
# v0.34.0 种族/背景标签词表与别名
# ---------------------------------------------------------------------------


def test_race_background_keyword_tables_are_valid() -> None:
    """词表结构：分类 → canonical 词；所有 canonical 自反解析（词表内即别名）。"""
    from astrbot_plugin_trpg_assistant.kb_enums import (
        BACKGROUND_KEYWORD_TAGS,
        RACE_KEYWORD_TAGS,
        resolve_background_keyword,
        resolve_race_keyword,
    )

    for table, resolver in (
        (RACE_KEYWORD_TAGS, resolve_race_keyword),
        (BACKGROUND_KEYWORD_TAGS, resolve_background_keyword),
    ):
        assert isinstance(table, dict) and table
        for cat, words in table.items():
            assert isinstance(cat, str) and cat
            assert isinstance(words, tuple) and words
            for w in words:
                assert isinstance(w, str) and w.strip()
                assert resolver(w) == w, f"{w} 未自反解析"


def test_resolve_race_keyword_aliases() -> None:
    """种族标签别名归一（口语/技能名/官方 traitTags 词）。"""
    from astrbot_plugin_trpg_assistant.kb_enums import resolve_race_keyword

    assert resolve_race_keyword("夜视") == "黑暗视觉"
    assert resolve_race_keyword("潜行") == "隐匿"
    assert resolve_race_keyword("变身") == "变形"
    assert resolve_race_keyword("骑乘") == "坐骑"
    assert resolve_race_keyword("负重") == "强力构筑"
    assert resolve_race_keyword("种族施法") == "天生施法"
    assert resolve_race_keyword("魔法抗性") == "魔法抗性"
    assert resolve_race_keyword("不存在的词") is None


def test_resolve_background_keyword_aliases() -> None:
    """背景标签别名归一（民间译名/工具别称/身份口语）。"""
    from astrbot_plugin_trpg_assistant.kb_enums import resolve_background_keyword

    assert resolve_background_keyword("游说") == "说服"
    assert resolve_background_keyword("特技") == "体操"
    assert resolve_background_keyword("伪装工具") == "易容工具"
    assert resolve_background_keyword("盗贼工具") == "盗贼工具"
    assert resolve_background_keyword("锻造工具") == "铁匠工具"
    assert resolve_background_keyword("雇佣兵") == "佣兵"
    assert resolve_background_keyword("贤者") == "学者"
    assert resolve_background_keyword("开局专长") == "起始专长"
    assert resolve_background_keyword("不存在的词") is None


# ---------------------------------------------------------------------------
# v0.35.0 构筑咨询查询：spell_classes / 等级过滤 / 前置 facet / 值集 facet
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager_with_lookup(tmp_path: Path) -> KnowledgeBaseManager:
    """带英文源查找表的构建（spell_classes 有数据）。"""
    from pathlib import Path as _P

    lookup = _P(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR,
          en_lookup=lookup)
    return KnowledgeBaseManager(out)


def test_filter_kind_guard_no_tags(manager: KnowledgeBaseManager) -> None:
    """filter() 无标签时也只返回该 kind 的条目（LEFT JOIN 类 kind 的回归）。"""
    for kind in ("background", "feat", "class", "subclass"):
        res = manager.filter(kind, limit=100)
        assert res.entries, f"{kind} 兜底应返回条目"
        assert all(e.kind == kind for e in res.entries), kind
    # total 是全量该 kind（不是全库）
    bg = manager.filter("background", limit=100)
    assert bg.total == 2  # fixture 2 个背景


def test_filter_spell_class_and_level_max(
    manager_with_lookup: KnowledgeBaseManager,
) -> None:
    res = manager_with_lookup.filter(
        "spell", spell_class="法师", level_max=1, limit=20
    )
    assert res.entries
    assert all(e.level is not None and e.level <= 1 for e in res.entries)
    # 全部来自职业法术表（fireball/ray of frost 等 fixture 映射）
    names = {e.name for e in res.entries}
    assert "冰霜射线" in names or "燃烧之手" in names


def test_spells_by_class(manager_with_lookup: KnowledgeBaseManager) -> None:
    res = manager_with_lookup.spells_by_class("法师")
    assert len(res.entries) == 6  # fixture 5 法术（火球术双版本）
    assert all(e.kind == "spell" for e in res.entries)
    # edition 后过滤
    res24 = manager_with_lookup.spells_by_class("法师", edition="2024")
    assert res24.entries
    assert all(e.edition == "2024" for e in res24.entries)


def test_feat_prereq_facets(manager: KnowledgeBaseManager) -> None:
    # 精灵之准：prereq_race=精灵
    feats = manager.filter("feat", limit=100).entries
    elven = next(e for e in feats if e.name == "精灵之准")
    facets = manager.feat_prereq_facets(elven.entry_id)
    assert facets.get("prereq_race") == ["精灵"]
    # 仪式施法者：三属性前置
    ritual = next(e for e in feats if e.name == "仪式施法者")
    facets2 = manager.feat_prereq_facets(ritual.entry_id)
    assert set(facets2.get("prereq_ability", [])) == {"感知 13", "智力 13", "魅力 13"}


def test_value_facets(manager: KnowledgeBaseManager) -> None:
    # fixture 中「战斗风格」同时是 prereq_feature 值；「精灵」是 prereq_race 值
    facets = manager.value_facets(
        "精灵", ("prereq_race", "feat_keyword", "spell_keyword")
    )
    assert "prereq_race" in facets
    # 不存在的词返回空
    assert manager.value_facets("不存在词", ("prereq_race",)) == []


def test_entry_tags_of(manager: KnowledgeBaseManager) -> None:
    # 战士/法师 fixture 无 class_keyword（富化为空），返回空 dict 不报错
    assert manager.entry_tags_of("战士", "class") == {}
    assert manager.entry_tags_of("不存在", "class") == {}
    # 精灵之准的 prereq_race 可经条目名取到
    tags = manager.entry_tags_of("精灵之准", "feat")
    assert "精灵" in tags.get("prereq_race", [])


# ---------------------------------------------------------------------------
# v0.35.0 构筑咨询查询：spell_classes / 等级过滤 / 前置 facet / 值集 facet
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager_with_lookup(tmp_path: Path) -> KnowledgeBaseManager:
    """带英文源查找表的构建（spell_classes 有数据）。"""
    from pathlib import Path as _P

    lookup = _P(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR,
          en_lookup=lookup)
    return KnowledgeBaseManager(out)


def test_filter_kind_guard_no_tags(manager: KnowledgeBaseManager) -> None:
    """filter() 无标签时也只返回该 kind 的条目（LEFT JOIN 类 kind 的回归）。"""
    for kind in ("background", "feat", "class", "subclass"):
        res = manager.filter(kind, limit=100)
        assert res.entries, f"{kind} 兜底应返回条目"
        assert all(e.kind == kind for e in res.entries), kind
    # total 是全量该 kind（不是全库）
    bg = manager.filter("background", limit=100)
    assert bg.total == 2  # fixture 2 个背景


def test_filter_spell_class_and_level_max(
    manager_with_lookup: KnowledgeBaseManager,
) -> None:
    res = manager_with_lookup.filter(
        "spell", spell_class="法师", level_max=1, limit=20
    )
    assert res.entries
    assert all(e.level is not None and e.level <= 1 for e in res.entries)
    # 全部来自职业法术表（fireball/ray of frost 等 fixture 映射）
    names = {e.name for e in res.entries}
    assert "冰霜射线" in names or "燃烧之手" in names


def test_spells_by_class(manager_with_lookup: KnowledgeBaseManager) -> None:
    res = manager_with_lookup.spells_by_class("法师")
    assert len(res.entries) == 6  # fixture 5 法术（火球术双版本）
    assert all(e.kind == "spell" for e in res.entries)
    # edition 后过滤
    res24 = manager_with_lookup.spells_by_class("法师", edition="2024")
    assert res24.entries
    assert all(e.edition == "2024" for e in res24.entries)


def test_feat_prereq_facets(manager: KnowledgeBaseManager) -> None:
    # 精灵之准：prereq_race=精灵
    feats = manager.filter("feat", limit=100).entries
    elven = next(e for e in feats if e.name == "精灵之准")
    facets = manager.feat_prereq_facets(elven.entry_id)
    assert facets.get("prereq_race") == ["精灵"]
    # 仪式施法者：三属性前置
    ritual = next(e for e in feats if e.name == "仪式施法者")
    facets2 = manager.feat_prereq_facets(ritual.entry_id)
    assert set(facets2.get("prereq_ability", [])) == {"感知 13", "智力 13", "魅力 13"}


def test_value_facets(manager: KnowledgeBaseManager) -> None:
    # fixture 中「战斗风格」同时是 prereq_feature 值；「精灵」是 prereq_race 值
    facets = manager.value_facets(
        "精灵", ("prereq_race", "feat_keyword", "spell_keyword")
    )
    assert "prereq_race" in facets
    # 不存在的词返回空
    assert manager.value_facets("不存在词", ("prereq_race",)) == []


def test_entry_tags_of(manager: KnowledgeBaseManager) -> None:
    # 战士/法师 fixture 无 class_keyword（富化为空），返回空 dict 不报错
    assert manager.entry_tags_of("战士", "class") == {}
    assert manager.entry_tags_of("不存在", "class") == {}
    # 精灵之准的 prereq_race 可经条目名取到
    tags = manager.entry_tags_of("精灵之准", "feat")
    assert "精灵" in tags.get("prereq_race", [])

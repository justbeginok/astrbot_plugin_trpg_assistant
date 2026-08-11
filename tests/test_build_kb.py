"""构建脚本（scripts/build_kb.py）单元测试：喂 fixture 目录断言产物。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
# 测试构建时隔离真实补丁目录（kb_patches/），避免真实补丁污染 fixture 计数。
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


@pytest.fixture()
def built_db(tmp_path: Path) -> Path:
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return out


def _conn(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def test_schema_and_counts(built_db: Path) -> None:
    conn = _conn(built_db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {
        "entries", "aliases", "spells", "monsters", "items",
        "class_features", "entry_tags", "races", "meta",
    } <= tables
    # v0.18 schema v4 侧表
    assert {
        "class_combat", "subclass_caster", "class_starting_equipment",
        "background_ability", "race_ability", "item_combat",
    } <= tables
    # 6 法术 + 3 怪物 + 18 物品（11 fixture + 3 遗物链 + 4 魔法变体本体）
    # + 9 专长 + 2 背景 + 4 状态 + 6 种族 + 2 职业 + 2 子职（奥法骑士重复行合并）= 52
    assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 52
    assert conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] >= 9 * 2
    assert conn.execute("SELECT COUNT(*) FROM spells").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 18
    assert conn.execute(
        "SELECT COUNT(*) FROM entries WHERE kind='feat'"
    ).fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM class_features").fetchone()[0] == 5
    # schema v4：meta 版本 + entry_tags 有数据 + races 侧表 + 规则引擎侧表
    assert conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0] == "10"
    # schema v10（v0.35.0 构筑咨询）：spell_classes 侧表存在（职业法术表）
    assert "spell_classes" in tables
    sc_cols = {r[1] for r in conn.execute("PRAGMA table_info(spell_classes)")}
    assert {"entry_id", "class_name"} <= sc_cols
    assert conn.execute("SELECT COUNT(*) FROM entry_tags").fetchone()[0] >= 50
    # schema v6（v0.26.0 专长标签反查）：feats 侧表存在
    feat_tables = {r[1] for r in conn.execute("PRAGMA table_info(feats)")}
    assert {"entry_id", "summary"} <= feat_tables
    # schema v7（v0.27.0 法术标签反查）：spells 表含 summary 列
    spell_cols = {r[1] for r in conn.execute("PRAGMA table_info(spells)")}
    assert "summary" in spell_cols
    # schema v8（v0.33.0 职业/子职富化）：classes 侧表存在（概要+定位）
    assert "classes" in tables
    class_cols = {r[1] for r in conn.execute("PRAGMA table_info(classes)")}
    assert {"entry_id", "summary", "role"} <= class_cols
    # schema v9（v0.34.0 种族/背景富化）：races.summary 列 + backgrounds 侧表
    race_cols = {r[1] for r in conn.execute("PRAGMA table_info(races)")}
    assert "summary" in race_cols
    assert "backgrounds" in tables
    bg_cols = {r[1] for r in conn.execute("PRAGMA table_info(backgrounds)")}
    assert {"entry_id", "summary"} <= bg_cols
    # schema v5（v0.20.0 商店）：items 表含价值/重量列且已填充
    item_cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    assert {"value_cp", "weight_lb"} <= item_cols
    row = conn.execute(
        "SELECT i.value_cp, i.weight_lb FROM items i"
        " JOIN entries e ON e.id = i.entry_id"
        " WHERE e.name='长剑' AND e.source='XPHB'"
    ).fetchone()
    assert row == (1500, 3)  # 15 金 / 3 磅
    # 无 value 的条目 value_cp 为 NULL（不硬塞 0）
    nulls = conn.execute(
        "SELECT COUNT(*) FROM items i JOIN entries e ON e.id = i.entry_id"
        " WHERE i.value_cp IS NULL"
    ).fetchone()[0]
    assert nulls >= 1
    # 规则引擎侧表（v0.18）
    assert conn.execute("SELECT COUNT(*) FROM class_combat").fetchone()[0] == 2
    # 奥法骑士：caster=1/3 入库，caster 为空的重复声明行被过滤
    rows = conn.execute(
        "SELECT subclass_name, caster FROM subclass_caster"
    ).fetchall()
    assert rows == [("奥法骑士", "1/3")]
    assert conn.execute("SELECT COUNT(*) FROM race_ability").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM background_ability").fetchone()[0] == 1
    # item_combat：长剑 + 手弩 + 炽火胶 + 皮甲 + 盾牌 + 3 把魔法武器
    # + 遗物之刃 3 形态（休眠/觉醒/升华）= 12
    assert conn.execute("SELECT COUNT(*) FROM item_combat").fetchone()[0] == 12
    conn.close()


def test_dual_version_spell(built_db: Path) -> None:
    conn = _conn(built_db)
    rows = conn.execute(
        "SELECT name, source, edition, is_machine, body FROM entries"
        " WHERE kind='spell' AND name='火球术' ORDER BY source"
    ).fetchall()
    assert len(rows) == 2
    sources = {r[1] for r in rows}
    assert sources == {"PHB", "XPHB"}
    editions = {r[2] for r in rows}
    assert editions == {"2014", "2024"}
    # 机翻判定：PHB 由不全书翻译 → 0；XPHB translator=机翻 → 1
    by_src = {r[1]: r for r in rows}
    assert by_src["PHB"][3] == 0
    assert by_src["XPHB"][3] == 1
    # 标签已清洗：正文不含 {@ 残留
    for r in rows:
        assert "{@" not in r[4]
    # 学派显示为中文（「学派塑能」而非「学派V」）
    assert "学派塑能" in by_src["PHB"][4]
    # 2024 版正文含升环施法段
    assert "升环施法" in by_src["XPHB"][4]
    conn.close()


def test_monster_filter_columns(built_db: Path) -> None:
    conn = _conn(built_db)
    row = conn.execute(
        "SELECT m.cr, m.mtype, m.size FROM monsters m"
        " JOIN entries e ON e.id = m.entry_id WHERE e.name='恐狼'"
    ).fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.25)  # 分数 CR 解析
    assert row[1] == "beast"
    assert row[2] == "L"
    # 龙类 CR 过滤可用
    dragons = conn.execute(
        "SELECT e.name FROM monsters m JOIN entries e ON e.id = m.entry_id"
        " WHERE m.mtype='dragon' AND m.cr <= 3.5 ORDER BY e.name"
    ).fetchall()
    assert [d[0] for d in dragons] == ["少年青铜龙"]
    conn.close()


def test_monster_body_contains_hp_ac(built_db: Path) -> None:
    """怪物正文必须包含 AC/HP 及全部关键词条（属性/豁免/技能/免疫/施法/装备等）。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT body FROM entries WHERE kind='monster' AND name='少年青铜龙'"
    ).fetchone()[0]
    assert "【数据】" in body
    assert "大型龙类，守序善良" in body  # 体型中文化 + 类型 + 阵营（v0.40.0）
    assert "AC 17" in body
    assert "HP 68（8d10 + 24）" in body
    assert "速度步行30尺、飞行60尺、游泳30尺" in body
    assert "挑战等级CR3（XP700；PB+2）" in body
    assert "【属性】力量17(+3)、敏捷10(+0)、体质15(+2)" in body
    assert "【豁免】敏捷+5、体质+4" in body
    assert "【技能】察觉+5、隐匿+2" in body
    assert "【免疫】闪电、钝击、穿刺（来自非魔法攻击）" in body
    assert "【抗性】寒冷" in body
    assert "【状态免疫】恐慌" in body
    assert "【语言】通用语、龙语" in body
    assert "【感官】黑暗视觉 60 尺、被动察觉13" in body
    assert "【装备】短剑、法球" in body
    assert "【环境】沿海、山地" in body
    assert "【施法】" in body
    assert "施法：（施法属性：智力）" in body
    assert "戏法：光亮术" in body
    assert "1环（2个法术位）：侦测魔法、雷电术" in body
    conn.close()


def test_format_alignment() -> None:
    """阵营字母码 → 中文（与 5etools-cn js/parser.js 规则一致，v0.40.0）。"""
    from astrbot_plugin_trpg_assistant.kb_enums import format_alignment

    assert format_alignment(["L", "G"]) == "守序善良"
    assert format_alignment(["N", "G"]) == "中立善良"
    assert format_alignment(["C", "N"]) == "混乱中立"
    assert format_alignment(["L", "N"]) == "守序中立"
    assert format_alignment(["N", "E"]) == "中立邪恶"
    assert format_alignment(["C", "E"]) == "混乱邪恶"
    assert format_alignment(["N"]) == "绝对中立"  # True Neutral
    assert format_alignment(["U"]) == "无阵营"
    assert format_alignment(["A"]) == "任意阵营"
    # NX/NY 特殊组合（2014 MM 巫妖等）
    assert format_alignment(["L", "NX", "C", "E"]) == "任意邪恶阵营"
    assert format_alignment(["NX", "C", "G", "NY", "E"]) == "任意非守序阵营"
    # 缺失/概率/字符串形态
    assert format_alignment(None) == ""
    assert format_alignment([]) == ""
    assert format_alignment("U") == "无阵营"
    assert format_alignment({"alignment": ["N", "G"], "chance": 50}) == (
        "中立善良（50%）"
    )


def test_monster_body_header_new_format(built_db: Path) -> None:
    """怪物【数据】头部：体型中文化 + 类型 + 阵营（v0.40.0）。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT body FROM entries WHERE kind='monster' AND name='成年红龙'"
    ).fetchone()[0]
    assert "巨型龙类，混乱邪恶" in body
    body = conn.execute(
        "SELECT body FROM entries WHERE kind='monster' AND name='恐狼'"
    ).fetchone()[0]
    assert "大型野兽，无阵营" in body
    conn.close()


def test_monster_body_type_dict_tags() -> None:
    """2024 型 dict type（带 tags）与缺失阵营兜底（v0.40.0）。"""
    from astrbot_plugin_trpg_assistant.kb_build_lib import _monster_body

    body = _monster_body({
        "name": "测试巫妖", "size": ["M"],
        "type": {"type": "undead", "tags": ["法师"]},
        "alignment": ["N", "E"],
        "cr": {"cr": "21", "xpLair": 41000},
    })
    assert "中型不死生物（法师），中立邪恶" in body
    assert "挑战等级CR21（XP33,000，或巢穴内41,000；PB+7）" in body
    # 缺失阵营 → 不固定阵营
    body = _monster_body({"name": "测试", "size": ["M"], "type": "beast", "cr": "1"})
    assert "中型野兽，不固定阵营" in body
    assert "挑战等级CR1（XP200；PB+2）" in body


def test_class_features_rows(built_db: Path) -> None:
    conn = _conn(built_db)
    rows = conn.execute(
        "SELECT class_name, subclass_name, subclass_short, level, name, summary"
        " FROM class_features ORDER BY subclass_name, level"
    ).fetchall()
    assert sorted(r[3:] for r in rows if not r[1]) == [
        (1, "战斗风格", "你采取一种特别的作战风格作为专长。"),
        (1, "施法", "你已学会施展法术。"),
        (2, "动作如潮", "在你的回合中，你可以额外进行一次动作，除施放法术外。"),
    ]
    # 子职显示名与短名双存：subclass_name=冠军武士（name），subclass_short=冠军（shortName）
    subs = [r for r in rows if r[1] == "冠军武士"]
    assert len(subs) == 2
    assert all(r[2] == "冠军" for r in subs)
    assert subs[0][3] == 3 and subs[0][4] == "精通重击"

    # list 块（{"type":"list","items":[...]}）与带 name 的 entries 块必须完整入 body
    body = conn.execute(
        "SELECT body FROM class_features WHERE name='非凡运动家'"
    ).fetchone()[0]
    assert "增益一：跳跃距离翻倍" in body
    assert "增益二：攀爬速度等同步行速度" in body
    assert "额外增益：" in body
    conn.close()


def test_item_columns(built_db: Path) -> None:
    conn = _conn(built_db)
    row = conn.execute(
        "SELECT i.rarity, i.attunement FROM items i"
        " JOIN entries e ON e.id = i.entry_id WHERE e.name='火球法杖'"
    ).fetchone()
    assert row == ("rare", 1)
    conn.close()


def test_item_body_rarity_cn(built_db: Path) -> None:
    """物品正文稀有度/类型中文化（v0.15.0）。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT e.body FROM items i JOIN entries e ON e.id = i.entry_id"
        " WHERE e.name='火球法杖'"
    ).fetchone()[0]
    assert "稀有度：珍稀" in body
    assert "类型：权杖" in body  # RD → 权杖
    assert "需要同调" in body
    # 基础物品（items-base）：直接显示「非魔法物品」，不带「稀有度：」前缀
    body = conn.execute(
        "SELECT e.body FROM items i JOIN entries e ON e.id = i.entry_id"
        " WHERE e.name='长剑'"
    ).fetchone()[0]
    assert "非魔法物品" in body
    assert "类型：武器" in body  # M → 武器
    assert "稀有度：" not in body
    conn.close()


def _tags_of(conn: sqlite3.Connection, name: str) -> dict[str, set[str]]:
    """某条目 entry_tags → {facet: set(values)}。"""
    out: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT t.facet, t.value FROM entry_tags t"
        " JOIN entries e ON e.id = t.entry_id WHERE e.name = ?",
        (name,),
    ):
        out.setdefault(r[0], set()).add(r[1])
    return out


def test_monster_tags(built_db: Path) -> None:
    """怪物特性标签：造成的伤害（动作区）+ 防御 + 环境 + 状态。"""
    conn = _conn(built_db)
    tags = _tags_of(conn, "少年青铜龙")
    # 动作区提取：啃咬穿刺 + 吐息闪电（免疫段落的「闪电」不会误入 dmg_dealt）
    assert tags["dmg_dealt"] == {"穿刺", "闪电"}
    assert "闪电" in tags["dmg_immune"]  # 免疫（含 dict 变体）
    assert "钝击" in tags["dmg_immune"]
    assert tags["dmg_resist"] == {"寒冷"}
    assert tags["condition_immune"] == {"恐慌"}
    # 环境归一化：沿海→海岸；「位面, X」→位面
    assert tags["environment"] == {"海岸", "山地"}
    tags = _tags_of(conn, "成年红龙")
    assert tags["dmg_dealt"] == {"火焰"}
    tags = _tags_of(conn, "恐狼")
    assert tags["dmg_dealt"] == {"穿刺"}
    assert tags["condition_inflict"] == {"受擒"}  # {@condition 受擒} 标签
    conn.close()


def test_spell_tags(built_db: Path) -> None:
    """法术特性标签：伤害/状态/成分/形状/目标 + spells 新列。"""
    conn = _conn(built_db)
    tags = _tags_of(conn, "枯萎术")
    assert "暗蚀" in tags["dmg_dealt"]
    assert tags["condition_inflict"] == {"麻痹"}
    assert tags["spell_component"] == {"言语", "姿势"}
    assert tags["spell_target"] == {"单体"}
    row = conn.execute(
        "SELECT s.level, s.concentration, s.components, s.range_feet, s.range_type"
        " FROM spells s JOIN entries e ON e.id = s.entry_id WHERE e.name = '枯萎术'"
    ).fetchone()
    assert row == (4, 1, "VS", 30, "feet")
    tags = _tags_of(conn, "燃烧之手")
    assert tags["spell_shape"] == {"锥形"}  # range.type=cone 结构化
    assert tags["spell_target"] == {"多体"}
    tags = _tags_of(conn, "火球术")
    assert "球形" in tags["spell_shape"]  # point + 文本「半径 20 尺」启发式
    assert tags["spell_target"] == {"多体"}
    tags = _tags_of(conn, "冰霜射线")
    assert tags["spell_target"] == {"单体"}
    # 人类定身术：惑控系 + 专注 + 麻痹 + 单体 + VSM + 60 尺
    tags = _tags_of(conn, "人类定身术")
    assert tags["condition_inflict"] == {"麻痹"}
    assert tags["spell_component"] == {"言语", "姿势", "材料"}
    assert tags["spell_target"] == {"单体"}
    row = conn.execute(
        "SELECT s.level, s.school, s.concentration, s.components, s.range_feet"
        " FROM spells s JOIN entries e ON e.id = s.entry_id"
        " WHERE e.name = '人类定身术'"
    ).fetchone()
    assert row == (2, "E", 1, "VSM", 60)
    conn.close()


def test_item_tags_and_copy(built_db: Path) -> None:
    """物品特性标签：dmgType 码 + 附加伤害文本 + 武器属性；_copy 继承基字段。"""
    conn = _conn(built_db)
    tags = _tags_of(conn, "火焰舌剑")
    # dmgType=S(挥砍) + entries 文本「火焰伤害」→ 两种伤害都被标记
    assert tags["dmg_dealt"] == {"挥砍", "火焰"}
    assert tags["weapon_property"] == {"灵巧"}
    tags = _tags_of(conn, "暗蚀之刃")
    assert tags["dmg_dealt"] == {"挥砍", "暗蚀"}
    assert tags["condition_inflict"] == {"魅惑"}
    assert tags["weapon_property"] == {"灵巧", "轻型"}
    # _copy 浅合并：炼金术士之毁灭继承基条目（炽火胶）的 dmgType=F / property=T
    tags = _tags_of(conn, "炼金术士之毁灭")
    assert tags["dmg_dealt"] == {"火焰"}
    assert tags["weapon_property"] == {"投掷"}
    conn.close()


def test_item_base_and_type_tags(built_db: Path) -> None:
    """物品类型反查：base_item（以X为基础的魔法物品）+ item_type（物品大类码表）。"""
    conn = _conn(built_db)
    # baseItem：火焰舌剑/暗蚀之刃/机翻魔剑 都以「长剑」为原型
    assert _tags_of(conn, "火焰舌剑")["base_item"] == {"长剑"}
    assert _tags_of(conn, "暗蚀之刃")["base_item"] == {"长剑"}
    assert _tags_of(conn, "机翻魔剑")["base_item"] == {"长剑"}
    # item_type 码表：M=武器、RD=权杖、P=药水、G=冒险装备（_copy 继承）
    assert _tags_of(conn, "长剑")["item_type"] == {"武器"}
    assert _tags_of(conn, "手弩")["item_type"] == {"武器"}
    assert _tags_of(conn, "机翻魔剑")["item_type"] == {"武器"}
    assert _tags_of(conn, "火球法杖")["item_type"] == {"权杖"}
    assert _tags_of(conn, "治疗药水")["item_type"] == {"药水"}
    assert _tags_of(conn, "炽火胶 (扁瓶)")["item_type"] == {"冒险装备"}
    conn.close()


def test_reprinted_as_redirect(built_db: Path) -> None:
    """reprintedAs：旧版（PHB 长剑）跳过，旧名成为再版（XPHB）条目的别名。"""
    conn = _conn(built_db)
    rows = conn.execute(
        "SELECT name, source FROM entries WHERE name = '长剑'"
    ).fetchall()
    assert rows == [("长剑", "XPHB")]
    alias = conn.execute(
        "SELECT e.source FROM aliases a JOIN entries e ON e.id = a.entry_id"
        " WHERE a.alias = '长剑'"
    ).fetchall()
    assert alias == [("XPHB",)]
    conn.close()


def test_feat_dual_version_reprint(built_db: Path) -> None:
    """专长 reprintedAs 豁免（v0.24.1）：2014/2024 规则版本并存，旧版不再被跳转丢弃。"""
    conn = _conn(built_db)
    rows = conn.execute(
        "SELECT name, source, edition, body FROM entries"
        " WHERE kind='feat' AND name='幸运' ORDER BY source"
    ).fetchall()
    assert len(rows) == 2
    by_src = {r[1]: r for r in rows}
    assert by_src["PHB"][2] == "2014"
    assert by_src["XPHB"][2] == "2024"
    # 2014 版保留原文（三点幸运骰），未被子版（英雄激励）覆盖
    assert "三点幸运骰" in by_src["PHB"][3]
    assert "英雄激励" in by_src["XPHB"][3]
    # 旧名别名指向 2014 行本身（不再跳转新版）
    alias = conn.execute(
        "SELECT e.source FROM aliases a JOIN entries e ON e.id = a.entry_id"
        " WHERE a.alias = '幸运' ORDER BY e.source"
    ).fetchall()
    assert [r[0] for r in alias] == ["PHB", "XPHB"]
    conn.close()


def test_feat_prereq_cn(built_db: Path) -> None:
    """专长前置条件中文化（v0.24.1）：结构化 prerequisite 渲染为可读中文，无 JSON 泄露。"""
    conn = _conn(built_db)

    def body_of(name: str) -> str:
        return conn.execute(
            "SELECT body FROM entries WHERE kind='feat' AND name=?", (name,)
        ).fetchone()[0]

    # 熟练前置（2014 无等级）
    assert "前置条件：中甲熟练" in body_of("中甲大师")
    # 公共等级 + 三属性任选（2024）：{@ 无残留、无 Python 字面量
    assert "前置条件：等级 4+，且 智力 13、感知 13 或 魅力 13" in body_of("仪式施法者")
    # 前置专长（3 段取中文显示名）
    assert "前置条件：等级 4+，且 前置专长：巨人打击（云雾打击）" in body_of("云巨人之诡诈")
    # 种族前置
    assert "前置条件：种族：精灵" in body_of("精灵之准")
    # 施法能力前置
    assert "前置条件：施法能力" in body_of("元素掌控")
    # 全部专长 body 无 Python 字面量泄露（[{' / 引号包裹的 dict）
    leaked = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE kind='feat'"
        " AND (body LIKE '%[{%' OR body LIKE \"%'{'%\")"
    ).fetchone()[0]
    assert leaked == 0
    conn.close()


def test_feat_tags(built_db: Path) -> None:
    """专长反查标签（v0.25.0）：类型 / 属性提升 / 先决种族·属性·专长·特性。"""
    conn = _conn(built_db)
    # 类型 + choose 六属性提升（2024 幸运，同名 PHB 无 category/ability 被聚合忽略）
    tags = _tags_of(conn, "幸运")
    assert tags.get("feat_type") == {"通用"}
    assert {"力量", "敏捷", "体质", "智力", "感知", "魅力"} <= tags.get(
        "ability_increase", set()
    )
    # choose 三属性 + 属性门槛（智力/感知/魅力 13）
    tags = _tags_of(conn, "仪式施法者")
    assert tags.get("feat_type") == {"通用"}
    assert {"智力", "感知", "魅力"} <= tags.get("ability_increase", set())
    assert {"智力 13", "感知 13", "魅力 13"} <= tags.get("prereq_ability", set())
    # 前置专长：全名 + 去括号基础名双标签
    tags = _tags_of(conn, "云巨人之诡诈")
    assert {"巨人打击", "巨人打击（云雾打击）"} <= tags.get("prereq_feat", set())
    # 前置种族（具体到名字）
    tags = _tags_of(conn, "精灵之准")
    assert tags.get("prereq_race") == {"精灵"}
    # 全部专长标签均在既有 entry_tags 通道中（facet 命名一致）
    rows = conn.execute(
        "SELECT DISTINCT t.facet FROM entry_tags t"
        " JOIN entries e ON e.id = t.entry_id WHERE e.kind='feat'"
    ).fetchall()
    assert {r[0] for r in rows} <= {
        "feat_type", "ability_increase",
        "prereq_race", "prereq_ability", "prereq_feat", "prereq_feature",
        "feat_keyword",
    }
    conn.close()


def test_feat_enrich_merge(tmp_path: Path) -> None:
    """v0.26.0 专长概要/关键字补丁合并：feats.summary 写入 + feat_keyword 标签。"""
    # patch_root 语义同 _load_patches：根目录，内部再拼 kb_patches/
    patch_root = tmp_path / "patch_root"
    patch_dir = patch_root / "kb_patches"
    patch_dir.mkdir(parents=True)
    (patch_dir / "feat_enrich.json").write_text(
        json.dumps([
            {
                "name": "坚韧", "source": "PHB", "edition": "2014",
                "summary": "提升生命值上限，随等级持续增长。",
                "keywords": ["生命", "防御"],
            },
            {
                "name": "箭术", "source": "XPHB", "edition": "2024",
                "summary": "远程武器攻击加值提高。",
                "keywords": ["远程", "命中"],
            },
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    out = tmp_path / "kb2" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=patch_root)
    conn = _conn(out)
    # 概要写入 feats 侧表
    row = conn.execute(
        "SELECT f.summary FROM feats f JOIN entries e ON e.id = f.entry_id"
        " WHERE e.kind='feat' AND e.name='坚韧'"
    ).fetchone()
    assert row and "生命值上限" in row[0]
    # 关键字写入 entry_tags（feat_keyword facet）
    tags = _tags_of(conn, "箭术")
    assert {"远程", "命中"} <= tags.get("feat_keyword", set())
    # 未在补丁中的专长无 feats 行
    assert conn.execute(
        "SELECT COUNT(*) FROM feats f JOIN entries e ON e.id = f.entry_id"
        " WHERE e.name='幸运'"
    ).fetchone()[0] == 0
    conn.close()


def test_reprinted_as_eng_alias(built_db: Path) -> None:
    """reprintedAs 跳转时英文旧版名（ENG_name）也注册为别名，精确命中新版条目。"""
    conn = _conn(built_db)
    # fixture：长剑（PHB，ENG=Longsword）reprintedAs → 长剑（XPHB）
    row = conn.execute(
        "SELECT e.name, e.source FROM aliases a JOIN entries e ON e.id = a.entry_id"
        " WHERE a.alias = 'longsword'"
    ).fetchall()
    assert row == [("长剑", "XPHB")]
    conn.close()


def test_meta_and_version_json(built_db: Path, tmp_path: Path) -> None:
    conn = _conn(built_db)
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    conn.close()
    assert meta["data_version"].startswith("fixture-abc123-")
    assert meta["source_commit"] == "fixture-abc123"
    assert "build_time" in meta

    vp = built_db.with_name("version.json")
    version = json.loads(vp.read_text(encoding="utf-8"))
    assert version["source_commit"] == "fixture-abc123"
    assert version["schema_version"] == "10"


def test_spell_classes_from_en_lookup(tmp_path: Path) -> None:
    """v0.35.0：英文源查找表 → spell_classes 职业法术表。

    fixture en_spell_lookup.json 把 5 条 phb 法术映射到 Wizard/Sorcerer/Warlock，
    其中只有 Wizard 能解析为库内职业「法师」，其余职业未收录应被跳过（不阻塞）。
    """
    lookup = Path(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"
    out = tmp_path / "kb" / "dnd_kb.db"
    build(
        FIXTURE_DIR, out, commit="fixture-abc123",
        patch_root=NO_PATCH_DIR, en_lookup=lookup,
    )
    conn = _conn(out)
    rows = conn.execute(
        "SELECT e.name, sc.class_name FROM spell_classes sc"
        " JOIN entries e ON e.id = sc.entry_id ORDER BY e.name"
    ).fetchall()
    # 5 条法术（火球术 PHB+XPHB 双版本）= 6 行，仅 Wizard→法师 可解析
    # （Sorcerer/Warlock 未收录被跳过）；SQLite 按 Unicode 码位排序：枯<火
    assert rows == [
        ("人类定身术", "法师"),
        ("冰霜射线", "法师"),
        ("枯萎术", "法师"),
        ("火球术", "法师"),
        ("火球术", "法师"),
        ("燃烧之手", "法师"),
    ]
    # 未提供英文源时不产出 spell_classes（默认构建路径不受影响）
    out2 = tmp_path / "kb2" / "dnd_kb.db"
    build(FIXTURE_DIR, out2, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    conn2 = _conn(out2)
    assert conn2.execute("SELECT COUNT(*) FROM spell_classes").fetchone()[0] == 0
    conn.close()
    conn2.close()


def test_load_en_spell_classes_merges_class_variant(tmp_path: Path) -> None:
    """v0.40.1：classVariant（扩展书对职业列表的增补，如 XGE/TCE 法术）
    必须并入主职业表，否则扩展书法术漏挂职业。"""
    from scripts.build_kb import _load_en_spell_classes

    lk = tmp_path / "lk.json"
    lk.write_text(json.dumps({
        "xge": {
            "absorb elements": {
                "classVariant": {
                    "PHB": {
                        "Druid": True, "Ranger": True,
                        "Sorcerer": True, "Wizard": True,
                    }
                }
            }
        },
        "phb": {
            "fireball": {"class": {"PHB": {"Wizard": True}}}
        }
    }), encoding="utf-8")
    out = _load_en_spell_classes(lk)
    assert out[("absorb elements", "xge")] == ["Druid", "Ranger", "Sorcerer", "Wizard"]
    assert ("fireball", "phb") in out  # class 原路径不受影响


def test_fmt_spellcasting_top_level_daily() -> None:
    """v0.40.1：spellcasting 顶层 daily/charges/rest 必须渲染（奥喀斯回归）。

    5etools-cn 的「每项N/日」施法挂在 spellcasting 顶层 daily（874 只怪物），
    此前只在 spells 环阶里查找（源数据无此形态），导致漏渲染。
    """
    from astrbot_plugin_trpg_assistant.kb_build_lib import _fmt_spellcasting

    out = _fmt_spellcasting({
        "name": "施法", "ability": "cha",
        "headerEntries": ["奥喀斯施展以下一道法术："],
        "will": ["{@spell 侦测魔法}"],
        "daily": {"1": ["{@spell 时间停止}"], "3": ["{@spell 解除魔法}"]},
    })
    assert "随意施展：侦测魔法" in out
    # 次数降序：3/日 先于 1/日
    assert out.index("每项3/日：解除魔法") < out.index("每项1/日：时间停止")

    # 键带 e 尾缀（5e.tools 的 N/day each 记法，如 XMM 巫妖）→ 显示去 e
    out = _fmt_spellcasting({
        "name": "施法", "ability": "int",
        "will": ["{@spell 侦测魔法}"],
        "daily": {"1e": ["{@spell 连锁闪电}"], "2e": ["{@spell 活化死尸}"]},
    })
    assert "每项2/日：活化死尸" in out
    assert "每项1/日：连锁闪电" in out
    assert "2e/日" not in out

    out = _fmt_spellcasting({
        "name": "魔杖施法",
        "will": ["{@spell 枯萎术}"],
        "charges": {
            "1e": ["{@spell 死亡法阵}", "{@spell 死亡一指}"],
            "2e": ["{@spell 律令死亡}"],
        },
    })
    assert "1充能：死亡法阵、死亡一指" in out
    assert "2充能：律令死亡" in out

    out = _fmt_spellcasting({
        "name": "施法",
        "rest": {"1": ["{@spell 黑暗术}"]},
        "restLong": {"1": ["{@spell 预言术}"]},
    })
    assert "每次短休或长休：黑暗术" in out
    assert "每次长休：预言术" in out


def test_condition_dual_version_and_status(built_db: Path) -> None:
    """状态：condition + status 合并入库；reprintedAs 豁免保留双版本。"""
    conn = _conn(built_db)
    rows = conn.execute(
        "SELECT name, source FROM entries WHERE kind='condition'"
        " ORDER BY name, source"
    ).fetchall()
    assert rows == [
        ("专注", "PHB"),  # status[] 合并进 kind=condition
        ("力竭", "PHB"),
        ("目盲", "PHB"),
        ("目盲", "XPHB"),
    ]
    # reprintedAs（目盲 PHB → XPHB）不跳过：2014/2024 规则版本并存
    body = conn.execute(
        "SELECT body FROM entries WHERE kind='condition' AND name='目盲' AND source='PHB'"
    ).fetchone()[0]
    assert "无法看见" in body
    conn.close()


def test_race_side_table_columns(built_db: Path) -> None:
    """races 侧表：速度数值列（bool true = 等同步行）+ darkvision。"""
    conn = _conn(built_db)
    row = conn.execute(
        "SELECT e.name, e.source, r.speed_walk, r.speed_climb, r.speed_swim,"
        " r.speed_fly, r.speed_burrow, r.darkvision"
        " FROM races r JOIN entries e ON e.id = r.entry_id"
        " WHERE e.name='阿斯莫' AND e.source='MPMM'"
    ).fetchone()
    assert row[2:] == (30, None, None, 30, None, 60)  # fly=true → 30
    row = conn.execute(
        "SELECT r.speed_walk, r.speed_climb, r.darkvision FROM races r"
        " JOIN entries e ON e.id = r.entry_id WHERE e.name='矮人'"
    ).fetchone()
    assert row == (30, 30, None)  # climb=true → 30
    row = conn.execute(
        "SELECT r.speed_swim FROM races r JOIN entries e ON e.id = r.entry_id"
        " WHERE e.name='流浆体'"
    ).fetchone()
    assert row == (30,)
    conn.close()


def test_race_tags_dual_channel(built_db: Path) -> None:
    """种族标签：体型/生物类型（默认类人生物）/速度类型/抗性/施法双通道。"""
    conn = _conn(built_db)
    # 2014 文本通道：{@spell} 天生施法 + 「对X伤害具有抗性」
    tags = _tags_of(conn, "阿斯莫")
    # 注意同名双版本（DMG+MPMM）_tags_of 聚合两行
    assert "火球术" in tags.get("innate_spell", set())  # 文本 {@spell 火球术}
    assert "火焰" in tags.get("dmg_resist", set())  # 文本抗性
    assert "类人生物" in tags.get("creature_type", set())  # 无字段默认
    # 2024 结构化通道：choose 展开 + innate/known 施法 + fly=true 速度类型
    mpmm = _tags_of(conn, "阿斯莫")  # 与 DMG 行共享断言（同上）
    assert {"致盲术", "舞光术"} <= mpmm.get("innate_spell", set())
    assert {"火焰", "暗蚀"} <= mpmm.get("dmg_resist", set())  # choose 展开
    assert "飞行" in mpmm.get("speed_type", set())
    # 非类人生物按字段标注（骷髅→不死生物、流浆体→泥怪）
    assert _tags_of(conn, "骷髅")["creature_type"] == {"不死生物"}
    assert _tags_of(conn, "流浆体")["creature_type"] == {"泥怪"}
    assert "游泳" in _tags_of(conn, "流浆体")["speed_type"]
    # 半精灵：多体型 ["S","M"] 逐条打标
    assert _tags_of(conn, "半精灵")["size"] == {"S", "M"}
    # 骷髅正文「对毒素伤害免疫」→ dmg_immune
    assert _tags_of(conn, "骷髅")["dmg_immune"] == {"毒素"}
    conn.close()


def test_copy_chain_recursive(built_db: Path) -> None:
    """二层 _copy 链（升华→觉醒→休眠）递归合并：升华条目不再漏收，正文含全部进阶词条。"""
    conn = _conn(built_db)
    rows = conn.execute(
        "SELECT e.name, e.source, e.body FROM entries e"
        " WHERE e.name LIKE '遗物之刃%' ORDER BY e.name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert names == {"遗物之刃（休眠）", "遗物之刃（觉醒）", "遗物之刃（升华）"}
    by_name = {r[0]: r[2] for r in rows}
    # 休眠正文不含觉醒/升华词条
    assert "觉醒" not in by_name["遗物之刃（休眠）"]
    # 觉醒正文 = 休眠正文 + _mod insertArr 插入的「觉醒」词条
    assert "觉醒" in by_name["遗物之刃（觉醒）"]
    assert "加值增至 +2" in by_name["遗物之刃（觉醒）"]
    # 升华正文 = 递归合并后含觉醒 + 升华词条（appendArr）
    assert "觉醒" in by_name["遗物之刃（升华）"]
    assert "加值增至 +2" in by_name["遗物之刃（升华）"]
    assert "加值增至 +3" in by_name["遗物之刃（升华）"]
    # 正文长度递增：升华 > 觉醒 > 休眠
    assert len(by_name["遗物之刃（升华）"]) > len(by_name["遗物之刃（觉醒）"]) > len(
        by_name["遗物之刃（休眠）"]
    )
    conn.close()


def test_mod_replace_arr() -> None:
    """_mod replaceArr：按 name 替换数组条目（纯函数，怪物动作区场景）。"""
    from scripts.build_kb import _apply_copy
    index = {
        "基础怪": [(
            "MM",
            {"name": "基础怪", "source": "MM", "action": [
                {"name": "啃咬", "entries": ["造成穿刺伤害。"]},
            ]},
        )],
    }
    entry = {
        "name": "变体怪",
        "source": "BGDIA",
        "_copy": {
            "name": "基础怪",
            "source": "MM",
            "_mod": {
                "action": {
                    "mode": "replaceArr",
                    "replace": "啃咬",
                    "items": {"name": "利爪猛击", "entries": ["造成挥砍伤害。"]},
                }
            },
        },
    }
    merged = _apply_copy(entry, index)
    assert merged["action"][0]["name"] == "利爪猛击"
    assert "挥砍" in merged["action"][0]["entries"][0]
    # 基础怪未被污染
    assert index["基础怪"][0][1]["action"][0]["name"] == "啃咬"


def test_mod_insert_and_replace_txt() -> None:
    """_mod insertArr（负索引倒数插入）/ appendArr / replaceTxt（跳过 {@tag}）。"""
    from scripts.build_kb import _apply_copy
    index = {"base": [("S", {"name": "base", "source": "S", "entries": ["a", "b"]})]}
    # insertArr index=-1：官方 ~index 真值 → 从倒数插入
    merged = _apply_copy({
        "name": "v2", "source": "S",
        "_copy": {"name": "base", "source": "S", "_mod": {
            "entries": {"mode": "insertArr", "index": -1, "items": "X"},
        }},
    }, index)
    assert merged["entries"] == ["a", "X", "b"]
    # appendArr 追加
    merged = _apply_copy({
        "name": "v3", "source": "S",
        "_copy": {"name": "base", "source": "S", "_mod": {
            "entries": {"mode": "appendArr", "items": "Y"},
        }},
    }, index)
    assert merged["entries"] == ["a", "b", "Y"]
    # replaceTxt：默认不替换 {@tag}，只替换普通文本
    merged = _apply_copy({
        "name": "v4", "source": "S",
        "_copy": {"name": "base", "source": "S", "_mod": {
            "entries": {"mode": "replaceTxt", "replace": "a", "with": "A"},
        }},
    }, index)
    assert merged["entries"] == ["A", "b"]
    # 父条目未被污染（深拷贝）
    assert index["base"][0][1]["entries"] == ["a", "b"]


def test_magic_variants_expansion(built_db: Path) -> None:
    """魔法变体（magicvariants.json）：本体入库 + 展开名做别名（v0.41.0）。"""
    conn = _conn(built_db)
    # 变体本体「焰舌」入库（GV 变体不展开成大量武器条目）
    rows = conn.execute(
        "SELECT name, source, edition, rarity, attunement FROM items i"
        " JOIN entries e ON e.id = i.entry_id WHERE e.name = '焰舌'"
    ).fetchall()
    assert len(rows) == 1
    name, src, edition, rarity, attunement = rows[0]
    assert (name, src, edition) == ("焰舌", "DMG", "2014")
    assert rarity == "rare"
    assert attunement == 1
    # 正文渲染变体效果
    body = conn.execute(
        "SELECT body FROM entries WHERE name = '焰舌' AND source = 'DMG'"
    ).fetchone()[0]
    assert "焰舌" in body or "火焰" in body
    assert "2d6" in body
    # 展开名作为别名指向本体（搜「焰舌长剑」命中「焰舌」本体）
    alias_hits = conn.execute(
        "SELECT e.name FROM aliases a JOIN entries e ON e.id = a.entry_id"
        " WHERE a.alias = '焰舌长剑'"
    ).fetchall()
    assert alias_hits and alias_hits[0][0] == "焰舌"
    # 不生成独立展开条目（库内无「焰舌长剑」条目）
    assert conn.execute(
        "SELECT COUNT(*) FROM entries WHERE name = '焰舌长剑'"
    ).fetchone()[0] == 0


def test_magic_variant_item_entry_resolution(built_db: Path) -> None:
    """变体本体含 {#itemEntry 引用} 时展开为模板文本（抗性护甲类）。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT body FROM entries WHERE name = '火焰抗性护甲' AND source = 'DMG'"
    ).fetchone()[0]
    assert "{#itemEntry" not in body
    assert "火焰" in body  # 模板变量 {{item.resist}} 被填充
    assert "抗性" in body
    # 本体入库且带稀有度/同调
    row = conn.execute(
        "SELECT i.rarity, i.attunement FROM items i JOIN entries e ON e.id = i.entry_id"
        " WHERE e.name = '火焰抗性护甲' AND e.source = 'DMG'"
    ).fetchone()
    assert row == ("rare", 1)


def test_magic_variant_var_fill(built_db: Path) -> None:
    """变体正文 {=字段} 变量替换（v0.42.1）：{=bonusWeapon} → +1。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT body FROM entries WHERE name = '+1 武器' AND source = 'XDMG'"
    ).fetchone()[0]
    assert "{=" not in body
    assert "获得+1的额外加成" in body


def test_magic_variant_top_entries_priority(built_db: Path) -> None:
    """顶层 entries 优先于 inherits.entries（v0.42.1）：恶毒武器的顶层
    平实描述（无 {=dmgType}）应被采用，inherits 模板被忽略。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT body FROM entries WHERE name = '恶毒武器' AND source = 'DMG'"
    ).fetchone()[0]
    assert "{=" not in body
    assert "该武器类型的损害" in body
    assert "dmgType" not in body


def test_item_entry_func_template_fill(built_db: Path) -> None:
    """itemEntry 模板函数 {{getFullImmRes item.resist}} 展开（v0.42.1）。"""
    conn = _conn(built_db)
    body = conn.execute(
        "SELECT body FROM entries WHERE name = '火焰抗性护甲' AND source = 'DMG'"
    ).fetchone()[0]
    assert "{{" not in body
    assert "getFullImmRes" not in body
    assert "对火焰伤害拥有抗性" in body


def test_built_db_no_placeholder_residue(built_db: Path) -> None:
    """全库断言：无 {=字段} 变量 / {{模板}} 残留（v0.42.1 渲染完整性）。"""
    conn = _conn(built_db)
    n = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE body LIKE '%{=%' OR body LIKE '%{{%'"
    ).fetchone()[0]
    assert n == 0

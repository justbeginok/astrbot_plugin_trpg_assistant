"""build_kb.py — 从 5etools 中文站数据构建知识库 SQLite（离线脚本）。

用法：
    python scripts/build_kb.py <data_dir> [--out kb_data/dnd_kb.db] [--commit <sha>]

- data_dir：5etools-cn 仓库（cn2.0 分支）data/ 目录的本地路径。
- 每次全量重建（数据量小，全量比增量简单且强一致）。
- 输出先写临时文件再 os.replace 原子替换。
- 机翻判定规则与 5etools-cn 网站一致（js/render.js）：
    有 translator 且属于人工译者白名单（当前仅「不全书」）→ 已校对；
    无 translator 或 translator 为「机翻」等 → 机翻（is_machine=1）。

本脚本不随插件加载，仅作为开发/发版工具。schema 见下方 SCHEMA。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# 允许从插件根目录直接运行：python scripts/build_kb.py
# 插件包目录自身无 __init__.py，以命名空间包方式导入，需将包目录的父目录加入 path。
_PKG_DIR = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _PKG_DIR.parent
for _p in (_PLUGIN_ROOT, _PKG_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from astrbot_plugin_trpg_assistant.kb_enums import (  # noqa: E402
    BACKGROUND_KEYWORD_TAGS,
    CLASS_KEYWORD_TAGS,
    CONDITION_CN,
    CREATURE_TYPE_CN_NORM,
    DAMAGE_TYPE_CODE,
    DAMAGE_TYPE_CN,
    FEAT_KEYWORD_TAGS,
    ITEM_TYPE_CODE,
    RACE_KEYWORD_TAGS,
    SCHOOL_CN_REV,
    SIZE_CN_REV,
    SPELL_KEYWORD_TAGS,
    SUBCLASS_KEYWORD_TAGS,
    WEAPON_PROPERTY_CODE,
    edition_of_source,
    format_rarity,
    normalize_environment,
)
from astrbot_plugin_trpg_assistant.kb_tags import clean_5etools_tags  # noqa: E402
from astrbot_plugin_trpg_assistant.kb_build_lib import (  # noqa: E402
    HUMAN_TRANSLATORS,
    RE_DAMAGE_WORD,
    RE_ANY_DAMAGE,
    RE_CONDITION_TAG,
    RE_RACE_DEFENSE,
    RE_SPELL_TAG,
    _ABIL_CN,
    _ABIL_ORDER,
    _DAMAGE_WARN_STOPWORDS,
    _DEFENSE_HINTS,
    _DMG_WORDS,
    _FEAT_ABILITY_CN,
    _FEAT_ARMOR_CN,
    _FEAT_CATEGORY_CN,
    _FEAT_WEAPON_CN,
    _RACE_SPEED_CN,
    _SKILL_CN,
    _SPEED_CN,
    _TIME_UNIT_CN,
    _ability_payload,
    _background_body,
    _collect_spell_names,
    _condition_body,
    _defense_list,
    _extract_conditions,
    _extract_damage,
    _feat_body,
    _feat_prereq_cn,
    _first_sentence,
    _flatten_entries,
    _flatten_entry,
    _flatten_table,
    _fmt_abil_scores,
    _fmt_ac,
    _fmt_damage_traits,
    _fmt_dict_bonuses,
    _fmt_hp,
    _fmt_spellcasting,
    _fmt_speed,
    _fmt_str_list,
    _item_body,
    _item_combat_cols,
    _item_value_weight,
    _kind_body,
    _lvl_sort_key,
    _monster_body,
    _monster_type,
    _parse_cr,
    _prereq_item_cn,
    _prereq_join,
    _race_body,
    _race_defense_tags,
    _race_innate_spells,
    _race_speed_cols,
    _race_speed_map,
    _race_structured_defense,
    _spell_body,
    _unmatched_damage_words,
    _walk_texts,
    is_machine_entry,
)

# 人工校对译者白名单：translator 不在其中即视为机翻（与 5etools-cn 渲染规则一致）。

# 六类条目对应的源文件定位器：(目录或文件名, JSON 顶层 key)
# 注：item 特殊处理（items.json + items-base.json 双文件，见 _load_items）；
#     condition 特殊处理（conditionsdiseases.json 双 key，见 _load_conditions）。
KIND_SOURCES: dict[str, tuple[str, str]] = {
    "spell": ("spells/spells-*.json", "spell"),
    "monster": ("bestiary/bestiary-*.json", "monster"),
    "item": ("items.json", "item"),
    "feat": ("feats.json", "feat"),
    "background": ("backgrounds.json", "background"),
    "condition": ("conditionsdiseases.json", "condition"),
    "race": ("races.json", "race"),
}
CLASS_FILES = "class/class-*.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  eng_name TEXT DEFAULT '',
  source TEXT NOT NULL,
  edition TEXT NOT NULL,
  body TEXT NOT NULL,
  is_machine INTEGER DEFAULT 0,
  UNIQUE(kind, name, source)
);
CREATE TABLE IF NOT EXISTS aliases(
  alias TEXT NOT NULL,
  entry_id INTEGER NOT NULL,
  PRIMARY KEY(alias, entry_id)
);
CREATE TABLE IF NOT EXISTS spells(
  entry_id INTEGER PRIMARY KEY,
  level INTEGER,
  school TEXT,
  ritual INTEGER DEFAULT 0,
  concentration INTEGER DEFAULT 0,
  components TEXT DEFAULT '',
  range_feet INTEGER,
  range_type TEXT DEFAULT '',
  summary TEXT DEFAULT ''
);
-- v0.35.0「职业法术表」：法术→主职业（英文 5e.tools 源 classes.fromClassList，
-- 按 ENG_name+source 匹配回中文条目），class_name 为归一后的中文职业名
-- （与 entries.kind='class' 的 name 一致）。子职/领域附赠法术不做。
CREATE TABLE IF NOT EXISTS spell_classes(
  entry_id INTEGER NOT NULL REFERENCES entries(id),
  class_name TEXT NOT NULL,
  PRIMARY KEY(entry_id, class_name)
);
CREATE INDEX IF NOT EXISTS idx_spell_classes_cn ON spell_classes(class_name);
CREATE TABLE IF NOT EXISTS monsters(
  entry_id INTEGER PRIMARY KEY,
  cr REAL,
  mtype TEXT,
  size TEXT
);
CREATE TABLE IF NOT EXISTS items(
  entry_id INTEGER PRIMARY KEY,
  rarity TEXT,
  attunement INTEGER DEFAULT 0,
  value_cp INTEGER,
  weight_lb REAL
);
CREATE TABLE IF NOT EXISTS class_features(
  id INTEGER PRIMARY KEY,
  class_name TEXT NOT NULL,
  subclass_name TEXT DEFAULT '',
  subclass_short TEXT DEFAULT '',
  source TEXT DEFAULT '',
  level INTEGER,
  name TEXT NOT NULL,
  summary TEXT DEFAULT '',
  body TEXT NOT NULL
);
-- v0.13.0「特性反查」：通用特性标签表。
-- facet 编码维度+关系（dmg_dealt/dmg_resist/dmg_immune/dmg_vuln/
--   condition_immune/condition_inflict/environment/
--   weapon_property/spell_component/spell_shape/spell_target/
--   base_item/item_type/speed_type/size/creature_type/innate_spell），
-- value 为构建期归一化后的 canonical 中文。
CREATE TABLE IF NOT EXISTS entry_tags(
  entry_id INTEGER NOT NULL,
  facet TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY(entry_id, facet, value)
);
CREATE INDEX IF NOT EXISTS idx_tags_fv ON entry_tags(facet, value);
-- v0.16.0「种族」：数值维度侧表（速度/黑暗视觉），仿 spells.range_feet；
-- 等值维度（体型/生物类型/抗性/天生施法/速度类型）走 entry_tags。
-- v0.34.0：加 summary 列（AI 一句话概要，源 kb_patches/race_enrich.json）。
CREATE TABLE IF NOT EXISTS races(
  entry_id INTEGER PRIMARY KEY,
  speed_walk INTEGER,
  speed_climb INTEGER,
  speed_swim INTEGER,
  speed_fly INTEGER,
  speed_burrow INTEGER,
  darkvision INTEGER,
  summary TEXT DEFAULT ''
);
-- v0.18.0「规则引擎」：战斗侧表（schema v4）。
-- class_combat：职业生命骰/豁免熟练/施法进度/施法属性（规则引擎 HP/法术位/法术攻击用）。
CREATE TABLE IF NOT EXISTS class_combat(
  entry_id INTEGER PRIMARY KEY,
  hd_faces INTEGER,
  saves TEXT,
  caster TEXT,
  spell_ability TEXT
);
-- subclass_caster：子职施法进度（奥法骑士/诡术师 1/3 等）。
-- 源数据同名单条常有 caster 为空的行（重复声明），构建期已过滤非空。
CREATE TABLE IF NOT EXISTS subclass_caster(
  id INTEGER PRIMARY KEY,
  class_name TEXT NOT NULL,
  subclass_name TEXT NOT NULL,
  subclass_short TEXT DEFAULT '',
  source TEXT DEFAULT '',
  caster TEXT,
  spell_ability TEXT
);
-- class_starting_equipment：起始装备（startingEquipment 原样 JSON）。
CREATE TABLE IF NOT EXISTS class_starting_equipment(
  entry_id INTEGER PRIMARY KEY,
  payload TEXT
);
-- background_ability / race_ability：属性加值（ability 数组原样 JSON）。
-- 2014 种族平铺 {"str":2,...} 或含 choose（半精灵式）；2024 背景 weighted choose；
-- PHB 背景 / 2024 种族 / 无结构化 ability 的条目不插行。
CREATE TABLE IF NOT EXISTS background_ability(
  entry_id INTEGER PRIMARY KEY,
  payload TEXT
);
CREATE TABLE IF NOT EXISTS race_ability(
  entry_id INTEGER PRIMARY KEY,
  payload TEXT
);
-- item_combat：护甲/武器战斗字段（AC/类型/力量需求/隐匿劣势/伤害骰/特性/射程）。
CREATE TABLE IF NOT EXISTS item_combat(
  entry_id INTEGER PRIMARY KEY,
  ac INTEGER,
  armor_type TEXT DEFAULT '',
  strength INTEGER,
  stealth INTEGER,
  dmg1 TEXT DEFAULT '',
  properties TEXT DEFAULT '',
  range_note TEXT DEFAULT ''
);
-- v0.20.0「商店」：items 加 value_cp（价值，铜币）/weight_lb（重量，磅）（schema v5）。
-- 源数据 value 单位为铜币（1金币=10银币=100铜币），weight 单位为磅；缺失存 NULL。
-- v0.26.0「专长标签反查」：feats 侧表存 AI 生成的一句话概要（summary，schema v6）；
-- 关键字标签走 entry_tags 的 feat_keyword facet（词表见 kb_enums.FEAT_KEYWORD_TAGS）。
CREATE TABLE IF NOT EXISTS feats(
  entry_id INTEGER PRIMARY KEY,
  summary TEXT DEFAULT ''
);
-- v0.27.0「法术标签反查」：spells 侧表加 summary（AI 生成的一句话概要，schema v7）；
-- 语义大类标签（控场/治疗/增益/减益/召唤/位移/防护/侦查/潜行/社交/探索/幻术/即死/
-- 造物/战斗辅助/施法辅助）走 entry_tags 的 spell_keyword facet（词表见
-- kb_enums.SPELL_KEYWORD_TAGS），与既有 dmg_dealt/condition_inflict 互补。
-- v0.33.0「职业/子职富化」：classes 侧表存 AI 生成的一句话概要（summary）与
-- 职业定位（role，武者/奥法/神职/专家，schema v8）；关键字走 entry_tags 的
-- class_keyword / subclass_keyword facet，定位同时写 class_role facet 供反查
-- （词表见 kb_enums.CLASS_KEYWORD_TAGS / SUBCLASS_KEYWORD_TAGS）。
CREATE TABLE IF NOT EXISTS classes(
  entry_id INTEGER PRIMARY KEY,
  summary TEXT DEFAULT '',
  role TEXT DEFAULT ''
);
-- v0.34.0「种族/背景富化」：backgrounds 侧表存 AI 生成的一句话概要
-- （summary，源 kb_patches/background_enrich.json）；语义标签走 entry_tags 的
-- background_keyword facet（词表见 kb_enums.BACKGROUND_KEYWORD_TAGS）。
-- 种族侧表 races 加 summary 列（见上），语义标签走 race_keyword facet
-- （词表见 kb_enums.RACE_KEYWORD_TAGS）。
CREATE TABLE IF NOT EXISTS backgrounds(
  entry_id INTEGER PRIMARY KEY,
  summary TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

# 数据库 schema 版本：结构变更（新表/新列）时 +1。
SCHEMA_VERSION = "10"


# ---------------------------------------------------------------------------
# 条目正文渲染（5etools entries 树 → 纯文本）
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# 特性标签提取（v0.13.0「特性反查」的数据基础）
#
# 源数据的伤害类型没有任何结构化字段：{@damage} 标签里是骰子（2d8+8），
# 伤害类型只以中文文本出现（「…点穿刺伤害加上7点火焰伤害」）。因此构建期用
# canonical 词表正则从动作区文本提取；immune/resist/vulnerable/environment
# 是结构化中文列表，直接映射。{@condition} 标签先于 clean_5etools_tags 提取。
# 译名归一化全部放构建期，查询期只做别名 → canonical 映射（见 kb_enums）。
# ---------------------------------------------------------------------------




# 伤害词正则：canonical + 别名交替（长词优先），要求紧跟「伤害」二字。
# 供构建期告警：统计正文中未收录的「X伤害」词（伤害词均为双字，且前面需是
# 非中文边界，避免把「成功则伤害」之类的中间位置误报为词）。
# 告警停用词：常见的非伤害类型「X伤害」搭配（量词/动词/通用名词），无需告警。
# 状态标签：{@condition 恐慌} 或 {@condition 恐慌|PHB}，必须在标签清洗前提取。
# 防御语义词：命中句中含这些词说明是「免疫/抗性/伤害减半」描述而非「造成伤害」。
# 减半/一半 针对「穿过水墙的火焰伤害减半」这类防御性描述；火球术的
# 「…火焰伤害，豁免成功则减半」按逗号拆句后互不干扰（见 _extract_damage）。

# 构建期兜底告警：词表未覆盖的「X伤害」上下文词计数（见 _extract_damage）。








def _monster_tags(m: dict) -> list[tuple[str, str]]:
    """怪物的特性标签：造成伤害/施加状态（动作区）+ 防御 + 环境。"""
    tags: list[tuple[str, str]] = []
    texts: list[str] = []
    for key in ("trait", "action", "bonus", "reaction", "legendary", "mythic"):
        for block in m.get(key) or []:
            if isinstance(block, dict):
                texts.extend(_walk_texts(block.get("entries")))
    for d in _extract_damage(texts):
        tags.append(("dmg_dealt", d))
    for c in _extract_conditions(texts):
        tags.append(("condition_inflict", c))
    for facet, key in (
        ("dmg_immune", "immune"),
        ("dmg_resist", "resist"),
        ("dmg_vuln", "vulnerable"),
    ):
        for v in _defense_list(m.get(key)):
            canonical = DAMAGE_TYPE_CN.get(v, v)
            tags.append((facet, canonical))
    for c in _defense_list(m.get("conditionImmune")):
        canonical = CONDITION_CN.get(c, c)
        tags.append(("condition_immune", canonical))
    for e in _defense_list(m.get("environment")):
        canonical = normalize_environment(e)
        if canonical:
            tags.append(("environment", canonical))
    return tags


_RANGE_SHAPE_CN = {
    "cone": "锥形",
    "cube": "立方",
    "emanation": "弥漫",
    "hemisphere": "半球",
    "line": "线形",
    "sphere": "球形",
    "radius": "球形",
}


def _spell_shape(s: dict, texts: list[str]) -> str | None:
    """法术范围形状：自原点法术直接由 range.type 决定，否则文本启发式。"""
    rtype = (s.get("range") or {}).get("type")
    if rtype in _RANGE_SHAPE_CN:
        return _RANGE_SHAPE_CN[rtype]
    if rtype == "special":
        return "特殊"
    joined = "\n".join(texts)
    if re.search(r"半径\s*\d+\s*尺|球状|球形", joined):
        return "球形"
    if "锥" in joined:
        return "锥形"
    if "立方" in joined:
        return "立方"
    if re.search(r"线(状|形)|直线", joined):
        return "线形"
    if re.search(r"柱(状|形)", joined):
        return "柱形"
    return None


def _spell_target(texts: list[str]) -> str | None:
    """法术目标类型（单体/多体/自我）启发式：歧义不打标，宁缺毋滥。"""
    joined = "\n".join(texts)
    # 注意「每个回合结束时」是豁免时机而非目标，需限定 每个/所有 后接生物类名词。
    if re.search(
        r"任意数量|每个(?:生物|目标|敌人|怪物)|所有(?:生物|目标)?|至多|一群|若干",
        joined,
    ):
        return "多体"
    if re.search(r"以你自己|以自身|自己为目标|自身", joined):
        return "自我"
    if re.search(
        r"一个(?:可见的|自愿的|你(?:能)?(?:看见|选择|指向|指定)?的)?"
        r"(?:生物|目标|物体|植物|类人生物)",
        joined,
    ):
        return "单体"
    return None


def _spell_tags(s: dict, enrich: dict | None = None) -> list[tuple[str, str]]:
    """法术的特性标签：伤害/状态/成分/形状/目标 + spell_keyword 语义大类。

    - dmg_dealt / condition_inflict：伤害类型/状态（正文启发式提取）；
    - spell_component / spell_shape / spell_target：成分/形状/目标；
    - spell_keyword（v0.27.0）：AI 生成的语义大类标签（enrich["keywords"]，
      控场/治疗/增益/减益/召唤/位移/防护/侦查/潜行/社交/探索/幻术/即死/造物/
      战斗辅助/施法辅助），词表外自定义词打印告警（不拒绝，自由词也是反查资产）。
    """
    tags: list[tuple[str, str]] = []
    texts: list[str] = []
    texts.extend(_walk_texts(s.get("entries")))
    texts.extend(_walk_texts(s.get("entriesHigherLevel")))
    for d in _extract_damage(texts):
        tags.append(("dmg_dealt", d))
    for c in _extract_conditions(texts):
        tags.append(("condition_inflict", c))
    comps = s.get("components") or {}
    for flag, cn in (
        (comps.get("v"), "言语"),
        (comps.get("s"), "姿势"),
        (comps.get("m"), "材料"),
    ):
        if flag:
            tags.append(("spell_component", cn))
    shape = _spell_shape(s, texts)
    if shape:
        tags.append(("spell_shape", shape))
    tgt = _spell_target(texts)
    if tgt:
        tags.append(("spell_target", tgt))
    if enrich:
        vocab = {
            kw
            for words in SPELL_KEYWORD_TAGS.values()
            for kw in words
        }
        for kw in enrich.get("keywords") or []:
            tags.append(("spell_keyword", kw))
            if kw not in vocab:
                _spell_kw_outside[kw] = _spell_kw_outside.get(kw, 0) + 1
    return tags


# 词表外法术关键字统计（构建末尾告警，便于补别名/收敛同义词）。
_spell_kw_outside: dict[str, int] = {}


def _item_tags(it: dict) -> list[tuple[str, str]]:
    """物品的特性标签：造成的伤害 + 武器属性 + 状态 + 基础物品 + 物品大类。"""
    tags: list[tuple[str, str]] = []
    for d in _extract_damage(_walk_texts(it.get("entries"))):
        tags.append(("dmg_dealt", d))
    for c in _extract_conditions(_walk_texts(it.get("entries"))):
        tags.append(("condition_inflict", c))
    dt = it.get("dmgType")
    if dt:
        canonical = DAMAGE_TYPE_CODE.get(str(dt))
        if canonical:
            tags.append(("dmg_dealt", canonical))
        else:
            print(f"  [warn] 未知 dmgType 码 {dt!r}（{it.get('name')}）")
    for p in it.get("property") or []:
        if not isinstance(p, str):
            continue
        code = p.split("|", 1)[0].strip()
        canonical = WEAPON_PROPERTY_CODE.get(code)
        if canonical:
            tags.append(("weapon_property", canonical))
        else:
            print(f"  [warn] 未知 property 码 {code!r}（{it.get('name')}）")
    # 基础物品：以该物品为基础武器的魔法物品反查（如「长剑」→ 黎明使者/月刃…）
    base = it.get("baseItem")
    if isinstance(base, str) and base.strip():
        base_name = base.split("|", 1)[0].strip()
        if base_name:
            tags.append(("base_item", base_name))
    # 物品大类：type 码（M/R/S/HA…）→ canonical 中文
    itype = it.get("type")
    if isinstance(itype, str) and itype.strip():
        code = itype.split("|", 1)[0].strip()
        canonical = ITEM_TYPE_CODE.get(code)
        if canonical:
            tags.append(("item_type", canonical))
        else:
            print(f"  [warn] 未知 item type 码 {code!r}（{it.get('name')}）")
    return tags


# ---------------------------------------------------------------------------
# 各类型条目的正文组装
# ---------------------------------------------------------------------------


# 施法时间/持续时间单位 → 中文（源数据为英文枚举）。




# 属性/豁免/技能的中英文映射（5e 标准六属性与十八技能）。




















# --- 专长前置条件中文化（v0.24.1） ---
# 5etools feats.json 的 prerequisite 为结构化列表：数组元素间 OR（满足任一）、
# 单元素内多条件 AND。构建期解析为可读中文，取代早期 str() 直写产生的
# 「前置条件：[{'level': 4, ...}]」Python 字面量泄露。
# 属性缩写 → 中文（与 character.py ABILITY_CN 一致）。
# 护甲熟练码 → 中文。
# 武器/武器组熟练码 → 中文。
# 专长类别码 → 中文（EFA 龙纹专长）。


# --- 专长反查标签（v0.25.0） ---
# 五etools category 码全集 → canonical 中文（FS:P/FS:R 归入战斗风格）。
_FEAT_TYPE_CN: dict[str, str] = {
    "G": "通用", "O": "起源", "FS": "战斗风格",
    "FS:P": "战斗风格", "FS:R": "战斗风格",
    "EB": "传奇恩惠", "DG": "黑暗赠礼", "D": "龙纹",
}


def _feat_tags(f: dict, enrich: dict | None = None) -> list[tuple[str, str]]:
    """专长反查标签（entry_tags）：

    - feat_type：专长类型（通用/起源/战斗风格/传奇恩惠/黑暗赠礼/龙纹，
      仅 category 字段存在时打标——2014 专长无类型概念）；
    - ability_increase：属性提升（ability 字段展开 choose.from；固定键同源）；
    - prereq_race / prereq_ability / prereq_feat / prereq_feature：先决条件
      （种族名、属性门槛「敏捷 13」、前置专长（含去括号基础名）、前置特性）；
    - feat_keyword（v0.26.0）：AI 生成的语义关键字（enrich["keywords"]），
      词表外自定义词打印告警（不拒绝，自由词也是反查资产）。
    """
    tags: list[tuple[str, str]] = []
    cat = f.get("category")
    if isinstance(cat, str) and cat in _FEAT_TYPE_CN:
        tags.append(("feat_type", _FEAT_TYPE_CN[cat]))
    ab = f.get("ability")
    if isinstance(ab, list):
        seen: set[str] = set()
        for a in ab:
            if not isinstance(a, dict):
                continue
            for k, v in a.items():
                if k == "choose" and isinstance(v, dict):
                    for code in (v.get("from") or []):
                        name = _FEAT_ABILITY_CN.get(code)
                        if name and name not in seen:
                            seen.add(name)
                            tags.append(("ability_increase", name))
                elif k in _FEAT_ABILITY_CN and v:
                    name = _FEAT_ABILITY_CN[k]
                    if name not in seen:
                        seen.add(name)
                        tags.append(("ability_increase", name))
    for pr in (f.get("prerequisite") or []):
        if not isinstance(pr, dict):
            continue
        for r in (pr.get("race") or []):
            if isinstance(r, dict) and r.get("name"):
                tags.append(("prereq_race", str(r["name"])))
        for a in (pr.get("ability") or []):
            if isinstance(a, dict):
                for k, v in a.items():
                    name = _FEAT_ABILITY_CN.get(k)
                    if name and isinstance(v, int):
                        tags.append(("prereq_ability", f"{name} {v}"))
        for s in (pr.get("feat") or []):
            seg = str(s).split("|")
            disp = seg[2] if len(seg) >= 3 else seg[0]
            tags.append(("prereq_feat", disp))
            base = re.sub(r"\s*（.*）$", "", disp).strip()
            if base and base != disp:
                tags.append(("prereq_feat", base))
        for name in (pr.get("feature") or []):
            tags.append(("prereq_feature", str(name)))
    if enrich:
        vocab = {
            kw
            for words in FEAT_KEYWORD_TAGS.values()
            for kw in words
        }
        for kw in enrich.get("keywords") or []:
            tags.append(("feat_keyword", kw))
            if kw not in vocab:
                _feat_kw_outside[kw] = _feat_kw_outside.get(kw, 0) + 1
    return tags


# 词表外专长关键字统计（构建末尾告警，便于补别名/收敛同义词）。
_feat_kw_outside: dict[str, int] = {}
# 词表外职业关键字统计（v0.33.0，构建末尾告警）。
_class_kw_outside: dict[str, int] = {}
# 词表外子职关键字统计（v0.33.0，构建末尾告警）。
_subclass_kw_outside: dict[str, int] = {}
# 词表外种族/背景关键字统计（v0.34.0，构建末尾告警）。
_race_kw_outside: dict[str, int] = {}
_background_kw_outside: dict[str, int] = {}
# v0.26.1：带 category 却未落 2024 的专长（源列表漏判）统计。
_feat_category_wrong: set[str] = set()














# --- 种族（v0.16.0） ---

# 英文速度键 → 中文（speed dict 键展示用）。

# 2014 种族正文抗性/免疫/易伤文本：对X伤害(和Y伤害)*[具有]抗性|免疫|易伤。
# 伤害词限定 canonical（DAMAGE_TYPE_CN 键），防「对魅惑免疫」「豁免优势」误标。

# 2014 种族正文天生施法：{@spell 法术名|source}（标签基本均为种族施法能力）。














def _race_tags(r: dict) -> list[tuple[str, str]]:
    """种族反查标签：体型/生物类型/速度类型/抗性/天生施法。"""
    tags: list[tuple[str, str]] = []
    size = r.get("size")
    if isinstance(size, list):
        for s in size:
            tags.append(("size", s))
    cts = r.get("creatureTypes")
    if isinstance(cts, list) and cts:
        for ct in cts:
            if isinstance(ct, str):
                tags.append(("creature_type", CREATURE_TYPE_CN_NORM.get(ct, ct)))
    else:
        # 无字段的 2014 种族：规则上默认类人生物（grill 确认）
        tags.append(("creature_type", "类人生物"))
    spd = r.get("speed")
    if isinstance(spd, dict):
        for key, cn in (("climb", "攀爬"), ("swim", "游泳"), ("fly", "飞行"), ("burrow", "掘穴")):
            v = spd.get(key)
            if v is True or isinstance(v, int):
                tags.append(("speed_type", cn))
    tags += _race_defense_tags(r)
    for sp in _race_innate_spells(r):
        tags.append(("innate_spell", sp))
    return tags






# ---------------------------------------------------------------------------
# 机翻判定
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 数值解析
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def _load_kind_files(data_dir: Path, pattern: str, key: str) -> list[dict]:
    files = sorted(data_dir.glob(pattern))
    if not files:
        return []
    out = []
    for f in files:
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  [warn] 跳过无法解析的 {f.name}: {e}")
            continue
        entries = payload.get(key)
        if not isinstance(entries, list):
            continue
        out.extend(entries)
    return out


def _load_en_spell_classes(en_lookup: Path | None) -> dict[tuple[str, str], list[str]]:
    """从英文 5e.tools 生成的法术源查找表提取法术→主职业表。

    自 2024 数据模型起，5e.tools 不再在 spell 条目内嵌 classes 字段，而是发布
    站点生成的 gendata-spell-source-lookup.json（https://5e.tools/data/generated/）。
    结构：{source小写: {法术名小写: {"class": {职业源: {职业英文名: true}}, ...}}}。
    v0.40.1 起同时合并 `class` 与 `classVariant`：5e.tools 语义中，扩展书
    （XGE/TCE/FTD/BMT/EGW 等）对既有职业列表的增补法术（如 XGE「吸收元素」）
    只挂在 `classVariant` 下，不合并会漏掉约 99 条扩展书法术的主职业归属。
    subclass/feat/race 等（子职/领域附赠/种族赠予/专长授予）仍不在主职业表范围。
    返回：(法术名小写, source) → 职业英文名列表；en_lookup 为 None 或不存在时返回空。
    """
    if en_lookup is None or not Path(en_lookup).is_file():
        return {}
    try:
        payload = json.loads(Path(en_lookup).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 无法解析英文法术源查找表 {en_lookup}: {e}")
        return {}
    out: dict[tuple[str, str], list[str]] = {}
    n = 0
    for src_low, by_name in payload.items():
        if not isinstance(by_name, dict):
            continue
        for name_low, info in by_name.items():
            if not isinstance(info, dict):
                continue
            cls_names: set[str] = set()
            for key in ("class", "classVariant"):
                cls_map = info.get(key)
                if not isinstance(cls_map, dict):
                    continue
                for by_src in cls_map.values():
                    if not isinstance(by_src, dict):
                        continue
                    cls_names.update(
                        str(cname).strip() for cname in by_src if str(cname).strip()
                    )
            if cls_names:
                out[(str(name_low).strip().lower(), str(src_low).strip().lower())] = (
                    sorted(cls_names)
                )
                n += 1
    print(f"  [en] 英文法术源查找表: {n} 条法术命中主职业表")
    return out


def _load_items(data_dir: Path) -> list[dict]:
    """物品 = 魔法物品（items.json, key=item）+ 基础物品（items-base.json, key=baseitem）
    + 魔法变体本体（magicvariants.json, key=magicvariant）。

    魔法变体（焰舌/霜铭/+N 武器等）不按基础武器展开成大量具体条目（避免搜索刷屏），
    只把变体本身作为一条 item 入库，并将所有可能展开名（如「焰舌长剑」「焰舌巨剑」）
    注册为别名指向本体——搜索「焰舌长剑」也能命中「焰舌」。
    """
    out = _load_kind_files(data_dir, "items.json", "item")
    baseitems = _load_kind_files(data_dir, "items-base.json", "baseitem")
    for b in baseitems:
        b["_is_base_item"] = True
    out += baseitems
    variants = _load_kind_files(data_dir, "magicvariants.json", "magicvariant")
    if variants:
        out += _expand_magic_variants(variants, out)
    return out


# ---------------------------------------------------------------------------
# 魔法变体（magicvariants.json）展开：本体入库 + 展开名做别名
# ---------------------------------------------------------------------------


def _expand_magic_variants(variants: list[dict], all_items: list[dict]) -> list[dict]:
    """魔法变体 → 变体本体条目（带 _expand_aliases 展开名列表）。

    变体本体字段：name=变体名、source=inherits.source、type 保留 GV|XX 码、
    rarity/reqAttune 取自 inherits、entries=inherits.entries（变体效果正文）。
    展开名通过 requires/excludes/edition 匹配 baseitem 计算（对齐 5e.tools
    render.js _createSpecificVariants），只作别名不生成独立条目。
    """
    baseitems = [it for it in all_items if it.get("_is_base_item")]
    out: list[dict] = []
    for v in variants:
        name = str(v.get("name") or "").strip()
        inh = v.get("inherits") or {}
        if not name or not isinstance(inh, dict):
            continue
        entry: dict = {
            "name": name,
            "ENG_name": str(v.get("ENG_name") or ""),
            "source": str(inh.get("source") or v.get("source") or "UNKNOWN"),
            "type": v.get("type"),  # GV|DMG / GV|XDMG
            "rarity": inh.get("rarity"),
            "reqAttune": inh.get("reqAttune"),
            "entries": inh.get("entries") or v.get("entries") or [],
            "_expand_aliases": [],
        }
        if v.get("translator"):
            entry["translator"] = v["translator"]
        # itemEntry 模板变量填充需条目字段（{{item.resist}}/{{item.detail1}}…）：
        # 复制 inherits 中可能被模板引用的字段（抗性/免疫/易伤/细节位等）。
        for _k in ("resist", "immune", "vuln", "conditionImmune",
                   "detail1", "detail2", "detail3", "bonusWeapon", "bonusAc"):
            if _k in inh:
                entry[_k] = inh[_k]
        # 再版跳转：2014 变体（如焰舌|DMG）reprintedAs 到 2024 版时，按既有
        # 物品跳转约定跳过旧版、旧名成为新版别名（避免 2014/2024 双行重复）。
        ra = inh.get("reprintedAs")
        if isinstance(ra, list) and ra:
            entry["reprintedAs"] = [str(r) for r in ra]
        # 展开名（只作别名）：
        for b in baseitems:
            if not _variant_matches_base(v, b):
                continue
            en = _variant_expanded_name(b, v)
            if en and en.lower() != name.lower():
                entry["_expand_aliases"].append(en)
        out.append(entry)
    if out:
        print(f"  [variant] magicvariants.json: 变体本体 {len(out)} 条入库")
    return out


def _variant_matches_base(variant: dict, base: dict) -> bool:
    """基础物品是否匹配变体的 edition/requires/excludes（对齐 5e.tools）。

    - edition：2014 基础物品只配 classic 变体，2024 只配 one/null，无 edition 配全部。
    - requires：数组任一满足（some）；单个 req 内所有键都要匹配（every）。
    - excludes：任一键匹配即排除（some）。
    """
    if not _variant_edition_match(base.get("edition"), variant.get("edition")):
        return False
    reqs = variant.get("requires")
    if isinstance(reqs, list):
        if not any(
            isinstance(r, dict) and _variant_key_match(base, r, "every")
            for r in reqs
        ):
            return False
    elif isinstance(reqs, dict):
        if not _variant_key_match(base, reqs, "every"):
            return False
    ex = variant.get("excludes")
    if isinstance(ex, dict) and _variant_key_match(base, ex, "some"):
        return False
    return True


def _variant_edition_match(base_edition: object, variant_edition: object) -> bool:
    if base_edition == variant_edition:
        return True
    if base_edition == "classic":
        return False
    if base_edition is None:
        return True
    if base_edition == "one":
        return variant_edition != "classic"
    return False


def _variant_key_match(candidate: dict, requirements: dict, method: str) -> bool:
    """递归键值匹配。method=every 全键满足 / some 任一满足。"""
    if not isinstance(candidate, dict) or not isinstance(requirements, dict):
        return False
    checks = []
    for key, val in requirements.items():
        if key == "ENG_name":  # 5etools-cn 中文版附带英文名，非匹配条件
            continue
        checks.append(_variant_val_match(candidate.get(key), val))
    return all(checks) if method == "every" else any(checks)


def _variant_val_match(cand_val: object, req_val: object) -> bool:
    if isinstance(req_val, list):
        if isinstance(cand_val, list):
            return any(cv in req_val for cv in cand_val)
        return cand_val in req_val
    if isinstance(req_val, dict):
        return _variant_key_match(
            cand_val if isinstance(cand_val, dict) else {}, req_val, "every"
        )
    if isinstance(cand_val, list):
        return req_val in cand_val
    return cand_val == req_val


def _variant_expanded_name(base: dict, variant: dict) -> str:
    """展开条目名 = 基础物品名应用 inherits.nameRemove/namePrefix/nameSuffix。"""
    inh = variant.get("inherits") or {}
    name = str(base.get("name") or "")
    rm = inh.get("nameRemove")
    if isinstance(rm, str) and rm:
        name = name.replace(rm, "")
    if inh.get("namePrefix"):
        name = str(inh["namePrefix"]) + name
    if inh.get("nameSuffix"):
        name = name + str(inh["nameSuffix"])
    return name.strip()


# ---------------------------------------------------------------------------
# itemEntry 引用解析（{#itemEntry 抗性护甲|XDMG} → 模板文本）
# ---------------------------------------------------------------------------

RE_ITEM_ENTRY_REF = re.compile(r"\{#itemEntry\s+([^}|]+)(?:\|([^}]+))?\}")

# 模板变量：{{item.xxx}} / {{getFullImmRes item.resist}} 等（5e.tools applyTemplate）。
RE_TPL_VAR = re.compile(r"\{\{\s*(item|getFullImmRes|getFullImm)\s*\.?([a-zA-Z0-9_]+)\s*\}\}")


def _load_item_entry_templates(data_dir: Path) -> dict[tuple[str, str], list]:
    """加载 items-base.json 的 itemEntry 模板 → {(名称小写, source): entriesTemplate}。

    source 可能缺失（引用不带 |来源 时回退），额外登记 (名称小写, "") 指向
    首个同名校验模板（防御多版本只取第一版）。
    """
    payload: dict[tuple[str, str], list] = {}
    f = data_dir / "items-base.json"
    if not f.is_file():
        return payload
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    first_by_name: dict[str, list] = {}
    for ent in data.get("itemEntry") or []:
        if not isinstance(ent, dict):
            continue
        nm = str(ent.get("name") or "").strip().lower()
        src = str(ent.get("source") or "").strip()
        tpl = ent.get("entriesTemplate")
        if nm and isinstance(tpl, list):
            payload[(nm, src)] = tpl
            first_by_name.setdefault(nm, tpl)
    for nm, tpl in first_by_name.items():
        payload.setdefault((nm, ""), tpl)
    return payload


def _fill_item_template(text: str, entry: dict) -> str:
    """填充模板变量：{{item.resist}} → 条目字段；{{getFullImmRes item.resist}}
    → 抗性/免疫列表中文（「火焰、寒冷」）。"""

    def repl(m: re.Match) -> str:
        kind, name = m.group(1), m.group(2)
        if kind == "item":
            val = entry.get(name)
            if isinstance(val, list):
                return "、".join(str(v) for v in val if v)
            return str(val) if val is not None else ""
        # getFullImmRes / getFullImm：免疫+抗性合并描述（简化：取条目的 resist/immune/vuln）
        if name in ("resist", "immune", "vuln", "conditionImmune"):
            val = entry.get(name)
            if isinstance(val, list):
                return "、".join(str(v) for v in val if v)
            return str(val) if val is not None else ""
        val = entry.get(name)
        if isinstance(val, list):
            return "、".join(str(v) for v in val if v)
        return str(val) if val is not None else ""

    return RE_TPL_VAR.sub(repl, text)


def _resolve_item_entries(
    entries: object, templates: dict[tuple[str, str], list], entry: dict
) -> list:
    """递归把 entries 中的 {#itemEntry 名称|来源} 替换为模板文本。

    模板 entriesTemplate 可能是纯文本数组或嵌套 dict（item/list 等），展开后
    递归填充模板变量；匹配失败保留原引用（防御脏数据）。
    """
    if isinstance(entries, str):
        m = RE_ITEM_ENTRY_REF.match(entries.strip())
        if not m:
            return [entries]
        tname, tsrc = m.group(1).strip().lower(), (m.group(2) or "").strip()
        tpl = templates.get((tname, tsrc)) or templates.get((tname, ""))
        if tpl is None:
            return [entries]
        return _resolve_item_entries(tpl, templates, entry)
    if not isinstance(entries, list) or not entries:
        return entries if isinstance(entries, list) else []
    out: list = []
    for ent in entries:
        if isinstance(ent, str):
            m = RE_ITEM_ENTRY_REF.match(ent.strip())
            if m:
                tname, tsrc = m.group(1).strip().lower(), (m.group(2) or "").strip()
                tpl = templates.get((tname, tsrc)) or templates.get((tname, ""))
                if tpl is not None:
                    resolved = _resolve_item_entries(tpl, templates, entry)
                    out.extend(resolved)
                    continue
            out.append(_fill_item_template(ent, entry))
        elif isinstance(ent, dict):
            d2 = dict(ent)
            if isinstance(ent.get("entries"), list):
                d2["entries"] = _resolve_item_entries(ent["entries"], templates, entry)
            if isinstance(ent.get("items"), list):
                d2["items"] = _resolve_item_entries(ent["items"], templates, entry)
            out.append(d2)
        else:
            out.append(ent)
    return out


# 数据补丁目录：存放 5e.tools 上游未收录、由其他来源（如 5E 不全书 CHM 站）补全的条目。
# 文件命名 {kind}.json，顶层 key 与 KIND_SOURCES 一致（item → {"items": [...]}）。
# 生成方式：scripts/fetch_chm_patch.py。
PATCH_DIR_NAME = "kb_patches"


def _load_patches(plugin_root: Path, kind: str) -> list[dict]:
    """从插件根目录 kb_patches/ 加载指定 kind 的补丁条目（不存在则返回空）。

    文件名兼容两种命名：{kind}.json 与 {kind}s.json（后者与 KIND_SOURCES 的
    JSON 顶层 key 一致，为抓取脚本默认输出名）。
    """
    patch_dir = plugin_root / PATCH_DIR_NAME
    patch_file = patch_dir / f"{kind}.json"
    if not patch_file.is_file():
        patch_file = patch_dir / f"{kind}s.json"
    if not patch_file.is_file():
        return []
    try:
        payload = json.loads(patch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 补丁文件解析失败 {patch_file}: {e}")
        return []
    # 顶层 key 与 KIND_SOURCES 的 JSON key 一致（item→items、monster→monster...），
    # 兼容两种命名：kind 本身 或 kind+"s"。
    entries = payload.get(kind)
    if entries is None:
        entries = payload.get(kind + "s")
    if not isinstance(entries, list):
        print(f"  [warn] 补丁文件缺少 {kind} 数组: {patch_file}")
        return []
    return entries


# 专长概要/关键字补丁文件：kb_patches/feat_enrich.json。
# 内容由 AI 逐条读取专长正文生成（非脚本可提取的语义数据）：
#   [{"name": "神射手", "source": "PHB", "edition": "2014",
#     "summary": "远程武器命中与伤害强化，无视掩体/劣势。", "keywords": ["远程", ...]}, ...]
# 键 (name, source, edition) 与 entries 唯一键一致；edition 由 source 推得，仅作校验。
FEAT_ENRICH_FILE = "feat_enrich.json"


def _load_feat_enrich(plugin_root: Path) -> dict[tuple[str, str, str], dict]:
    """加载专长概要/关键字补丁 → {(name, source, edition): {"summary","keywords"}}。"""
    patch_file = plugin_root / PATCH_DIR_NAME / FEAT_ENRICH_FILE
    if not patch_file.is_file():
        return {}
    try:
        payload = json.loads(patch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 专长补丁文件解析失败 {patch_file}: {e}")
        return {}
    if not isinstance(payload, list):
        print(f"  [warn] 专长补丁文件应为数组: {patch_file}")
        return {}
    out: dict[tuple[str, str, str], dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        edition = str(item.get("edition") or "").strip()
        if not name or not source:
            continue
        rec: dict = {}
        if isinstance(item.get("summary"), str) and item["summary"].strip():
            rec["summary"] = item["summary"].strip()
        kw = item.get("keywords")
        if isinstance(kw, list):
            rec["keywords"] = [
                str(k).strip() for k in kw if isinstance(k, str) and k.strip()
            ]
        if rec:
            out[(name, source, edition)] = rec
    if out:
        print(f"  [patch] feat_enrich: 合并 {len(out)} 条专长概要/关键字")
    return out


# 法术概要/关键字补丁文件：kb_patches/spell_enrich.json。
# 内容由 AI 逐条读取法术正文生成（非脚本可提取的语义数据）：
#   [{"name": "火球术", "source": "XPHB", "edition": "2024",
#     "summary": "爆裂火球灼烧大范围区域，造成高额火焰伤害。", "keywords": ["伤害"]}, ...]
# 键 (name, source, edition) 与 entries 唯一键一致；edition 由 source 推得，仅作校验。
# 554 条全覆盖（2014 补充书 + 2024 双书），缺失时法术无概要/标签但构建不失败。
SPELL_ENRICH_FILE = "spell_enrich.json"


def _load_spell_enrich(plugin_root: Path) -> dict[tuple[str, str, str], dict]:
    """加载法术概要/关键字补丁 → {(name, source, edition): {"summary","keywords"}}。"""
    patch_file = plugin_root / PATCH_DIR_NAME / SPELL_ENRICH_FILE
    if not patch_file.is_file():
        return {}
    try:
        payload = json.loads(patch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 法术补丁文件解析失败 {patch_file}: {e}")
        return {}
    if not isinstance(payload, list):
        print(f"  [warn] 法术补丁文件应为数组: {patch_file}")
        return {}
    out: dict[tuple[str, str, str], dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        edition = str(item.get("edition") or "").strip()
        if not name or not source:
            continue
        rec: dict = {}
        if isinstance(item.get("summary"), str) and item["summary"].strip():
            rec["summary"] = item["summary"].strip()
        kw = item.get("keywords")
        if isinstance(kw, list):
            rec["keywords"] = [
                str(k).strip() for k in kw if isinstance(k, str) and k.strip()
            ]
        if rec:
            out[(name, source, edition)] = rec
    if out:
        print(f"  [patch] spell_enrich: 合并 {len(out)} 条法术概要/关键字")
    return out


# 职业/子职概要/关键字/定位补丁文件：kb_patches/class_enrich.json。
# 内容由 AI 逐条读取职业特性/子职特性生成（非脚本可提取的语义数据）：
#   [{"name": "战士", "source": "PHB", "edition": "2014",
#     "summary": "精通所有武器与护甲的战斗大师。", "role": "武者",
#     "keywords": ["近战", "重甲", "坦克", "爆发", "力量"]}, ...]
# 键 (name, source, edition) 与 entries 唯一键一致；role 仅职业有（13 职业×2 版
# + TCE 协力者按同名定位归类；UA 秘术师无 role），子职 role 留空。
# 职业 29 条 / 子职 186 条全覆盖（排除 UA 与 Plane Shift 跨界），缺失时无概要
# 但构建不失败。
CLASS_ENRICH_FILE = "class_enrich.json"


def _load_class_enrich(plugin_root: Path) -> dict[tuple[str, str, str], dict]:
    """加载职业/子职概要/关键字/定位补丁 → {(name, source, edition): dict}。"""
    patch_file = plugin_root / PATCH_DIR_NAME / CLASS_ENRICH_FILE
    if not patch_file.is_file():
        return {}
    try:
        payload = json.loads(patch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 职业补丁文件解析失败 {patch_file}: {e}")
        return {}
    if not isinstance(payload, list):
        print(f"  [warn] 职业补丁文件应为数组: {patch_file}")
        return {}
    out: dict[tuple[str, str, str], dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        edition = str(item.get("edition") or "").strip()
        if not name or not source:
            continue
        rec: dict = {}
        if isinstance(item.get("summary"), str) and item["summary"].strip():
            rec["summary"] = item["summary"].strip()
        if isinstance(item.get("role"), str) and item["role"].strip():
            rec["role"] = item["role"].strip()
        kw = item.get("keywords")
        if isinstance(kw, list):
            rec["keywords"] = [
                str(k).strip() for k in kw if isinstance(k, str) and k.strip()
            ]
        if rec:
            out[(name, source, edition)] = rec
    if out:
        print(f"  [patch] class_enrich: 合并 {len(out)} 条职业/子职概要/关键字/定位")
    return out


# 种族概要/关键字补丁文件：kb_patches/race_enrich.json。
# 内容由 AI 逐条读取种族正文生成（非脚本可提取的语义数据）：
#   [{"name": "提夫林", "source": "PHB", "edition": "2014",
#     "summary": "炼狱血脉的人类后裔，以火焰抗性与天生法术行走世间。",
#     "keywords": ["魅力", "恶魔", "火焰", "黑暗", "天生施法"]}, ...]
# 键 (name, source, edition) 与 entries 唯一键一致；edition 由 source 推得，仅作校验。
# 160 条全覆盖（同名多版本分别生成，与 feat_enrich 双版本先例一致），缺失时
# 种族无概要/标签但构建不失败。
RACE_ENRICH_FILE = "race_enrich.json"


def _load_race_enrich(plugin_root: Path) -> dict[tuple[str, str, str], dict]:
    """加载种族概要/关键字补丁 → {(name, source, edition): {"summary","keywords"}}。"""
    patch_file = plugin_root / PATCH_DIR_NAME / RACE_ENRICH_FILE
    if not patch_file.is_file():
        return {}
    try:
        payload = json.loads(patch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 种族补丁文件解析失败 {patch_file}: {e}")
        return {}
    if not isinstance(payload, list):
        print(f"  [warn] 种族补丁文件应为数组: {patch_file}")
        return {}
    out: dict[tuple[str, str, str], dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        edition = str(item.get("edition") or "").strip()
        if not name or not source:
            continue
        rec: dict = {}
        if isinstance(item.get("summary"), str) and item["summary"].strip():
            rec["summary"] = item["summary"].strip()
        kw = item.get("keywords")
        if isinstance(kw, list):
            rec["keywords"] = [
                str(k).strip() for k in kw if isinstance(k, str) and k.strip()
            ]
        if rec:
            out[(name, source, edition)] = rec
    if out:
        print(f"  [patch] race_enrich: 合并 {len(out)} 条种族概要/关键字")
    return out


# 背景概要/关键字补丁文件：kb_patches/background_enrich.json。
# 内容由 AI 逐条读取背景正文生成（非脚本可提取的语义数据）：
#   [{"name": "侍僧", "source": "XPHB", "edition": "2024",
#     "summary": "献身于神祇或信仰的侍奉者，掌握宗教学识与洞悉人心的能力。",
#     "keywords": ["智力", "感知", "魅力", "洞悉", "宗教", "书法工具",
#                  "起始专长", "教士"]}, ...]
# 键 (name, source, edition) 与 entries 唯一键一致；edition 由 source 推得，仅作校验。
# 148 条全覆盖（同名多版本分别生成），缺失时背景无概要/标签但构建不失败。
BACKGROUND_ENRICH_FILE = "background_enrich.json"


def _load_background_enrich(plugin_root: Path) -> dict[tuple[str, str, str], dict]:
    """加载背景概要/关键字补丁 → {(name, source, edition): {"summary","keywords"}}。"""
    patch_file = plugin_root / PATCH_DIR_NAME / BACKGROUND_ENRICH_FILE
    if not patch_file.is_file():
        return {}
    try:
        payload = json.loads(patch_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] 背景补丁文件解析失败 {patch_file}: {e}")
        return {}
    if not isinstance(payload, list):
        print(f"  [warn] 背景补丁文件应为数组: {patch_file}")
        return {}
    out: dict[tuple[str, str, str], dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        edition = str(item.get("edition") or "").strip()
        if not name or not source:
            continue
        rec: dict = {}
        if isinstance(item.get("summary"), str) and item["summary"].strip():
            rec["summary"] = item["summary"].strip()
        kw = item.get("keywords")
        if isinstance(kw, list):
            rec["keywords"] = [
                str(k).strip() for k in kw if isinstance(k, str) and k.strip()
            ]
        if rec:
            out[(name, source, edition)] = rec
    if out:
        print(f"  [patch] background_enrich: 合并 {len(out)} 条背景概要/关键字")
    return out


def _load_conditions(data_dir: Path) -> list[dict]:
    """状态 = conditionsdiseases.json 的 condition[] + status[]（disease 不收录）。"""
    out = _load_kind_files(data_dir, "conditionsdiseases.json", "condition")
    out += _load_kind_files(data_dir, "conditionsdiseases.json", "status")
    return out


def _index_by_name(entries: list[dict]) -> dict[str, list[tuple[str, dict]]]:
    """name.lower() → [(source, entry), ...]。"""
    index: dict[str, list[tuple[str, dict]]] = {}
    for e in entries:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        index.setdefault(name.lower(), []).append((str(e.get("source") or ""), e))
    return index


def _apply_copy(entry: dict, index: dict[str, list[tuple[str, dict]]]) -> dict:
    """_copy 合并：递归解析 _copy 链并应用 _mod（对齐 5etools 官方 copyApplier 语义）。

    - 递归：父条目仍含 _copy 时继续向上一级合并（升华→觉醒→休眠 三层链）。
    - 合并顺序：先浅合并字段（本条目覆盖父条目），再应用 _mod。
    - _mod 语义（5etools js/utils.js _doMod_handleProp）：
        * 值统一为数组（normaliseMods）。
        * 键 `*`：应用到 COPY_ENTRY_PROPS（怪物 action/trait/legendary 等 12 字段）；
        * 键 `_`：无 prop 的特殊操作（replaceSpells 等）；
        * 其他键：prop.split(".") 路径。
        * mode：insertArr（~index 负索引=倒数）、appendArr、prependArr、replaceArr
          （replace 为 name/index/regex 三种匹配）、removeArr（names/items）、
          renameArr、replaceTxt（正则全局替换，tagInsensitive 时含 {@tag} 文本）、
          appendStr、replaceName。
    """
    cp = entry.get("_copy") or {}
    if not isinstance(cp, dict):
        return entry
    base_name = str(cp.get("name") or "").strip().lower()
    base_src = str(cp.get("source") or "")
    if not base_name:
        return entry
    for cand_src, cand in index.get(base_name, []):
        if base_src and cand_src != base_src:
            continue
        # 递归解析父条目自身的 _copy 链（父条目可能是原始条目，也可能是已合并结果）
        if cand.get("_copy"):
            cand = _apply_copy(cand, index)
        merged = json.loads(json.dumps(cand))  # 深拷贝：_mod 原地改数组不能污染父条目
        for k, v in entry.items():
            if k != "_copy":
                merged[k] = json.loads(json.dumps(v))
        if not merged.get("name"):
            merged["name"] = cand.get("name")
        _apply_mods(merged, cp)
        merged.pop("_copy", None)
        return merged
    return entry


# 5etools COPY_ENTRY_PROPS：`*` 键 _mod 应用的默认字段（怪物动作/特性区）。
_COPY_ENTRY_PROPS: tuple[str, ...] = (
    "action", "bonus", "reaction", "trait", "legendary", "mythic", "variant",
    "spellcasting", "actionHeader", "bonusHeader", "reactionHeader",
    "legendaryHeader", "mythicHeader",
)


def _apply_mods(copy_to: dict, copy_meta: dict) -> None:
    """按 5etools 语义应用 _mod。copy_meta 为 _copy 字典（含 _mod）。"""
    mod = copy_meta.get("_mod") or {}
    if not isinstance(mod, dict) or not mod:
        return
    # normaliseMods：值统一为数组
    norm: dict[str, list] = {}
    for k, v in mod.items():
        norm[k] = v if isinstance(v, list) else [v]
    # 排序：_ 与 * 放最后（官方 _sortProps：_PROPS_TAIL=[_,*]）
    ordered = sorted(norm.items(), key=lambda kv: (kv[0] in ("_", "*"), kv[0]))
    for prop, mod_infos in ordered:
        if prop == "*":
            for p in _COPY_ENTRY_PROPS:
                _apply_mod_infos(copy_to, mod_infos, p)
        elif prop == "_":
            _apply_mod_infos(copy_to, mod_infos, None)
        else:
            _apply_mod_infos(copy_to, mod_infos, prop)


def _apply_mod_infos(copy_to: dict, mod_infos: list, prop: str | None) -> None:
    prop_path = prop.split(".") if prop else None
    for mod_info in mod_infos:
        if isinstance(mod_info, str):
            if mod_info == "remove" and prop:
                copy_to.pop(prop, None)
            continue
        if not isinstance(mod_info, dict):
            continue
        mode = mod_info.get("mode")
        if mode == "insertArr":
            _do_mod_insert_arr(copy_to, mod_info, prop_path)
        elif mode == "appendArr":
            _do_mod_append_arr(copy_to, mod_info, prop_path)
        elif mode == "prependArr":
            _do_mod_prepend_arr(copy_to, mod_info, prop_path)
        elif mode == "replaceArr":
            _do_mod_replace_arr(copy_to, mod_info, prop_path)
        elif mode == "replaceOrAppendArr":
            if not _do_mod_replace_arr(copy_to, mod_info, prop_path, is_throw=False):
                _do_mod_append_arr(copy_to, mod_info, prop_path)
        elif mode == "appendIfNotExistsArr":
            _do_mod_append_if_not_exists(copy_to, mod_info, prop_path)
        elif mode == "removeArr":
            _do_mod_remove_arr(copy_to, mod_info, prop_path)
        elif mode == "renameArr":
            _do_mod_rename_arr(copy_to, mod_info, prop_path)
        elif mode == "replaceTxt":
            _do_mod_replace_txt(copy_to, mod_info, prop_path)
        elif mode == "appendStr":
            _do_mod_append_str(copy_to, mod_info, prop_path)
        elif mode == "replaceName":
            _do_mod_replace_name(copy_to, mod_info, prop_path)
        # calculateProp/scalarAddProp 等数值类在物品/怪物数据中几乎不用，跳过。


def _mod_target(copy_to: dict, prop_path: list[str] | None):
    if not prop_path:
        return copy_to
    cur = copy_to
    for seg in prop_path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _mod_set(copy_to: dict, prop_path: list[str] | None, value) -> None:
    if not prop_path:
        return
    cur = copy_to
    for seg in prop_path[:-1]:
        if not isinstance(cur, dict):
            return
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    if isinstance(cur, dict):
        cur[prop_path[-1]] = value


def _do_mod_ensure_list(mod_info: dict, key: str) -> list:
    v = mod_info.get(key)
    return v if isinstance(v, list) else ([v] if v is not None else [])


def _do_mod_insert_arr(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    items = _do_mod_ensure_list(mod_info, "items")
    target = _mod_target(copy_to, prop_path)
    if not isinstance(target, list):
        return
    index = mod_info.get("index")
    if index is None or not (isinstance(index, int) and index < 0):
        # 官方：~modInfo.index 真值才用指定索引，否则追加末尾；负索引从倒数插入
        if isinstance(index, int) and index < 0:
            pos = max(0, len(target) + index + 1)
        else:
            pos = len(target)
    else:
        pos = index
    target[pos:pos] = items


def _do_mod_append_arr(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    items = _do_mod_ensure_list(mod_info, "items")
    target = _mod_target(copy_to, prop_path)
    if isinstance(target, list):
        target.extend(items)
    else:
        _mod_set(copy_to, prop_path, items)


def _do_mod_prepend_arr(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    items = _do_mod_ensure_list(mod_info, "items")
    target = _mod_target(copy_to, prop_path)
    if isinstance(target, list):
        target[0:0] = items
    else:
        _mod_set(copy_to, prop_path, items)


def _do_mod_replace_arr(
    copy_to: dict, mod_info: dict, prop_path: list[str] | None, is_throw: bool = True
) -> bool:
    items = _do_mod_ensure_list(mod_info, "items")
    target = _mod_target(copy_to, prop_path)
    if not isinstance(target, list):
        return False
    replace = mod_info.get("replace")
    ix_old = None
    if isinstance(replace, dict):
        if replace.get("regex"):
            import re as _re
            rx = _re.compile(replace["regex"], getattr(_re, replace.get("flags") or "", 0))
            ix_old = next(
                (i for i, it in enumerate(target)
                 if (it.get("name") if isinstance(it, dict) else (rx.search(str(it)) if isinstance(it, str) else False))
                 if isinstance(it, dict) and it.get("name") and rx.search(it["name"])
                 or isinstance(it, str) and rx.search(it)),
                None,
            )
        elif replace.get("index") is not None:
            ix_old = replace["index"]
    elif isinstance(replace, str):
        ix_old = next(
            (i for i, it in enumerate(target)
             if (it.get("name") if isinstance(it, dict) else it) == replace),
            None,
        )
    if ix_old is None:
        return False
    if not isinstance(ix_old, int) or not (0 <= ix_old < len(target)):
        return False
    target[ix_old:ix_old + 1] = items
    return True


def _do_mod_append_if_not_exists(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    items = _do_mod_ensure_list(mod_info, "items")
    target = _mod_target(copy_to, prop_path)
    if not isinstance(target, list):
        _mod_set(copy_to, prop_path, items)
        return
    for item in items:
        name = item.get("name") if isinstance(item, dict) else item
        if name is not None and any(
            (it.get("name") if isinstance(it, dict) else it) == name for it in target
        ):
            continue
        target.append(item)


def _do_mod_remove_arr(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    target = _mod_target(copy_to, prop_path)
    if not isinstance(target, list):
        return
    names = mod_info.get("names")
    if isinstance(names, str):
        names = [names]
    if isinstance(names, list):
        for name in names:
            ix = next(
                (i for i, it in enumerate(target)
                 if (it.get("name") if isinstance(it, dict) else it) == name),
                None,
            )
            if ix is not None:
                target.pop(ix)
        return
    items = _do_mod_ensure_list(mod_info, "items")
    for item in items:
        ix = next(
            (i for i, it in enumerate(target)
             if (it.get("name") if isinstance(it, dict) else it) == item),
            None,
        )
        if ix is not None:
            target.pop(ix)


def _do_mod_rename_arr(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    target = _mod_target(copy_to, prop_path)
    if not isinstance(target, list):
        return
    renames = mod_info.get("renames")
    if isinstance(renames, dict):
        renames = [renames]
    if not isinstance(renames, list):
        return
    for rn in renames:
        if not isinstance(rn, dict):
            continue
        old = rn.get("rename")
        new = rn.get("with")
        for it in target:
            if isinstance(it, dict) and it.get("name") == old:
                it["name"] = new
                break


def _do_mod_replace_txt(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    """replaceTxt：正则全局替换。props 默认 [null,'entries','headerEntries','footerEntries']；
    默认不替换 {@tag}（tagInsensitive 为真才含）。"""
    replace = mod_info.get("replace")
    with_str = mod_info.get("with")
    if not isinstance(replace, str) or not isinstance(with_str, str):
        return
    flags = mod_info.get("flags") or ""
    try:
        rx = re.compile(replace, re.IGNORECASE if "i" in flags else 0)
    except re.error:
        return
    tag_insensitive = bool(mod_info.get("tagInsensitive"))
    props = mod_info.get("props")
    target = _mod_target(copy_to, prop_path)

    def _sub(s: str) -> str:
        if not tag_insensitive:
            # 默认跳过 {@tag} 块
            parts = re.split(r"(\{@[^}]*\})", s)
            return "".join(p if p.startswith("{@") else rx.sub(with_str, p) for p in parts)
        return rx.sub(with_str, s)

    if props is None:
        props = [None, "entries", "headerEntries", "footerEntries"]
    if not isinstance(props, list):
        props = [props]
    if isinstance(target, list):
        # 官方逻辑：props 含 None → 先处理数组内的纯字符串元素
        if None in props:
            for i, it in enumerate(target):
                if isinstance(it, str):
                    target[i] = _sub(it)
        for ent in target:
            if isinstance(ent, dict):
                for p in props:
                    if p is None:
                        continue
                    val = ent.get(p)
                    if isinstance(val, str):
                        ent[p] = _sub(val)
                    elif isinstance(val, list):
                        ent[p] = [_sub(x) if isinstance(x, str) else x for x in val]
    elif isinstance(target, dict):
        for p in props:
            if p is None:
                continue
            val = target.get(p)
            if isinstance(val, str):
                target[p] = _sub(val)
            elif isinstance(val, list):
                target[p] = [_sub(x) if isinstance(x, str) else x for x in val]


def _do_mod_append_str(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    target = _mod_target(copy_to, prop_path)
    joiner = mod_info.get("joiner") or ""
    s = mod_info.get("str") or ""
    if isinstance(target, str):
        _mod_set(copy_to, prop_path, target + joiner + s)
    else:
        _mod_set(copy_to, prop_path, s)


def _do_mod_replace_name(copy_to: dict, mod_info: dict, prop_path: list[str] | None) -> None:
    """replaceName：替换数组内条目的 name 字段（正则匹配）。"""
    replace = mod_info.get("replace")
    with_str = mod_info.get("with")
    if not isinstance(replace, str) or not isinstance(with_str, str):
        return
    flags = mod_info.get("flags") or ""
    try:
        rx = re.compile(replace, re.IGNORECASE if "i" in flags else 0)
    except re.error:
        return
    target = _mod_target(copy_to, prop_path)
    if not isinstance(target, list):
        return
    for ent in target:
        if isinstance(ent, dict) and ent.get("name") and rx.search(str(ent["name"])):
            ent["name"] = rx.sub(with_str, str(ent["name"]))


def _load_class_data(data_dir: Path) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {
        "class": [],
        "subclass": [],
        "classFeature": [],
        "subclassFeature": [],
    }
    for f in sorted(data_dir.glob(CLASS_FILES)):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in result:
            items = payload.get(key)
            if isinstance(items, list):
                result[key].extend(items)
    return result








def _git_short_sha(data_dir: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(data_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = out.stdout.strip()
        return sha if sha else "unknown"
    except Exception:  # noqa: BLE001 — 非 git 目录（如 fixture）时回退
        return "unknown"


def build(
    data_dir: Path,
    out_path: Path,
    commit: str | None = None,
    patch_root: Path | None = None,
    en_lookup: Path | None = None,
) -> dict:
    """全量构建知识库。

    patch_root：数据补丁目录（默认插件根 kb_patches/）；测试传 fixture 空目录隔离。
    en_lookup：英文 5e.tools 生成的法术源查找表 gendata-spell-source-lookup.json
        （可选）；提供后构建 v0.35.0 职业法术表 spell_classes（按英文名+source
        匹配，未命中写入报告不阻塞）。
    """
    if not data_dir.is_dir():
        raise SystemExit(f"错误：数据目录不存在: {data_dir}")

    print(f"[build_kb] 读取数据目录: {data_dir}")
    sha = commit or _git_short_sha(data_dir)

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)

    counts: dict[str, int] = {}
    skipped = 0
    _feat_kw_outside.clear()
    _feat_category_wrong.clear()
    _spell_kw_outside.clear()
    _class_kw_outside.clear()
    _subclass_kw_outside.clear()
    _race_kw_outside.clear()
    _background_kw_outside.clear()
    feat_enrich = _load_feat_enrich(
        patch_root if patch_root is not None else _PKG_DIR
    )
    spell_enrich = _load_spell_enrich(
        patch_root if patch_root is not None else _PKG_DIR
    )
    class_enrich = _load_class_enrich(
        patch_root if patch_root is not None else _PKG_DIR
    )
    race_enrich = _load_race_enrich(
        patch_root if patch_root is not None else _PKG_DIR
    )
    background_enrich = _load_background_enrich(
        patch_root if patch_root is not None else _PKG_DIR
    )
    # v0.35.0：英文源职业法术表（(法术名小写, source) → 主职业英文名列表）。
    en_spell_classes = _load_en_spell_classes(en_lookup)
    # v0.41.0：itemEntry 模板（{#itemEntry X} 引用展开用，来自 items-base.json）。
    item_templates = _load_item_entry_templates(data_dir)
    # 法术→职业待落库行（entry_id, 职业英文名）；职业中文名在职业段落库后解析。
    pending_spell_classes: list[tuple[int, str]] = []
    # 未命中英文源的法术（en_dir 提供时统计）。
    spell_class_miss: list[str] = []
    # v0.34.0：种族/背景标签词表（词表外自由词告警用）。
    race_vocab = {kw for words in RACE_KEYWORD_TAGS.values() for kw in words}
    background_vocab = {
        kw for words in BACKGROUND_KEYWORD_TAGS.values() for kw in words
    }

    # --- 各类型普通条目 ---
    for kind, (pattern, key) in KIND_SOURCES.items():
        entries = (
            _load_items(data_dir) if kind == "item"
            else _load_conditions(data_dir) if kind == "condition"
            else _load_kind_files(data_dir, pattern, key)
        )
        # 数据补丁（上游缺失条目的补充源，见 _load_patches / fetch_chm_patch.py）
        patches = _load_patches(patch_root if patch_root is not None else _PKG_DIR, kind)
        if patches:
            entries = list(entries) + patches
            print(f"  [patch] {kind}: 合并 {len(patches)} 条补丁条目")
        index = _index_by_name(entries)
        # reprintedAs（再版跳转）：目标版本存在 → 本条目跳过，旧名成为再版条目的别名。
        # 注：condition/race/feat 豁免——状态、种族与专长的 2014/2024 是规则版本并存
        #     （目盲 PHB+XPHB 两行、阿斯莫 DMG/VGM/MPMM/XPHB 多行、幸运 PHB+XPHB 两行），
        #     旧版规则文本（2014 幸运/巨武器大师等）仍须保留，否则 2014 团查不到原版。
        # reprint_redirect: 中文旧名 -> (新版中文名, 新版来源)；eng_redirect 同存英文旧名，
        # 使「英文旧版名」（如 Ring of Fire Elemental Command）能精确命中库内新版条目。
        reprint_redirect: dict[str, tuple[str, str]] = {}
        eng_redirect: dict[str, tuple[str, str]] = {}

        def _reprint_target_exists(entry: dict) -> bool:
            for t in entry.get("reprintedAs") or []:
                if not isinstance(t, str):
                    continue
                tname, _, tsrc = t.partition("|")
                for cand_src, _ in index.get(tname.strip().lower(), []):
                    if cand_src == tsrc.strip():
                        return True
            return False

        n = 0
        for raw in entries:
            e = _apply_copy(raw, index)
            name = (e.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            source = str(e.get("source") or "UNKNOWN")
            if (
                kind not in ("condition", "race", "feat")
                and raw.get("reprintedAs") and _reprint_target_exists(raw)
            ):
                for t in raw["reprintedAs"]:
                    if not isinstance(t, str):
                        continue
                    tname, _, tsrc = t.partition("|")
                    for cand_src, _ in index.get(tname.strip().lower(), []):
                        if cand_src == tsrc.strip():
                            reprint_redirect[name.lower()] = (
                                tname.strip(), tsrc.strip()
                            )
                            old_eng = str(raw.get("ENG_name") or "").strip().lower()
                            if old_eng:
                                eng_redirect[old_eng] = (
                                    tname.strip(), tsrc.strip()
                                )
                            break
                    if name.lower() in reprint_redirect:
                        break
                skipped += 1
                continue
            # 各类型正文来源不同（法术在 entries、怪物在 trait/action 等），
            # 统一以「渲染后正文是否为空」作为脏数据判定。
            if kind == "item" and item_templates:
                e["entries"] = _resolve_item_entries(
                    e.get("entries") or [], item_templates, e
                )
            body = _kind_body(kind, e)
            if not body:
                skipped += 1
                continue
            eng = str(e.get("ENG_name") or "")
            edition = edition_of_source(source)
            cur = conn.execute(
                "INSERT OR REPLACE INTO entries"
                " (kind, name, eng_name, source, edition, body, is_machine)"
                " VALUES (?,?,?,?,?,?,?)",
                (kind, name, eng, source, edition, body, is_machine_entry(e)),
            )
            entry_id = cur.lastrowid
            for alias in {name.lower(), eng.lower()}:
                if alias:
                    conn.execute(
                        "INSERT OR IGNORE INTO aliases (alias, entry_id) VALUES (?,?)",
                        (alias, entry_id),
                    )
            # 魔法变体本体：展开名（如「焰舌长剑」「焰舌巨剑」）作为别名指向本体，
            # 使精确搜索展开名也能命中「焰舌」本体（不生成独立变体条目）。
            for alias in e.get("_expand_aliases") or []:
                if isinstance(alias, str) and alias.strip():
                    conn.execute(
                        "INSERT OR IGNORE INTO aliases (alias, entry_id) VALUES (?,?)",
                        (alias.strip().lower(), entry_id),
                    )
            # 侧表过滤字段 + 特性标签
            if kind == "spell":
                comps = e.get("components") or {}
                cstr = "".join(
                    p for p, flag in (
                        ("V", comps.get("v")),
                        ("S", comps.get("s")),
                        ("M", comps.get("m")),
                    ) if flag
                )
                rng = e.get("range") or {}
                dist = rng.get("distance") or {}
                range_feet = (
                    dist.get("amount") if dist.get("type") == "feet" else None
                )
                range_type = dist.get("type") or None
                # v0.27.0：spells.summary（AI 一句话概要）+ spell_keyword 语义标签
                srec = spell_enrich.get((name, source, edition))
                conn.execute(
                    "INSERT INTO spells"
                    " (entry_id, level, school, ritual, concentration,"
                    "  components, range_feet, range_type, summary)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        entry_id,
                        e.get("level"),
                        e.get("school"),
                        1 if (e.get("meta") or {}).get("ritual") else 0,
                        1 if any(
                            d.get("concentration") for d in (e.get("duration") or [])
                        ) else 0,
                        cstr,
                        range_feet,
                        range_type,
                        (srec or {}).get("summary", ""),
                    ),
                )
                # v0.35.0：职业法术表（英文源查找表按 eng名+source 匹配）。
                if en_spell_classes:
                    en_cls = en_spell_classes.get((eng.lower(), source.lower()))
                    if en_cls:
                        for cname in en_cls:
                            pending_spell_classes.append((entry_id, cname))
                    else:
                        spell_class_miss.append(
                            f"{name} ({source}, {edition}, eng={eng})"
                        )
                tags = _spell_tags(e, srec)
            elif kind == "monster":
                conn.execute(
                    "INSERT INTO monsters (entry_id, cr, mtype, size) VALUES (?,?,?,?)",
                    (
                        entry_id,
                        _parse_cr(e.get("cr")),
                        _monster_type(e),
                        (e.get("size") or [""])[0] if isinstance(e.get("size"), list) else e.get("size"),
                    ),
                )
                tags = _monster_tags(e)
            elif kind == "item":
                v_cp, w_lb = _item_value_weight(e)
                conn.execute(
                    "INSERT INTO items"
                    " (entry_id, rarity, attunement, value_cp, weight_lb)"
                    " VALUES (?,?,?,?,?)",
                    (
                        entry_id,
                        e.get("rarity"),
                        1 if e.get("reqAttune") else 0,
                        v_cp,
                        w_lb,
                    ),
                )
                ic = _item_combat_cols(e)
                if ic:
                    conn.execute(
                        "INSERT INTO item_combat"
                        " (entry_id, ac, armor_type, strength, stealth,"
                        "  dmg1, properties, range_note)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (entry_id,) + ic,
                    )
                tags = _item_tags(e)
            elif kind == "race":
                # v0.34.0：races.summary（AI 一句话概要）+ race_keyword 语义标签
                rrec = race_enrich.get((name, source, edition))
                conn.execute(
                    "INSERT INTO races"
                    " (entry_id, speed_walk, speed_climb, speed_swim,"
                    "  speed_fly, speed_burrow, darkvision, summary)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (entry_id,) + _race_speed_cols(e)
                    + ((rrec or {}).get("summary", ""),),
                )
                ab_payload = _ability_payload(e)
                if ab_payload:
                    conn.execute(
                        "INSERT INTO race_ability (entry_id, payload) VALUES (?,?)",
                        (entry_id, ab_payload),
                    )
                tags = _race_tags(e)
                for kw in (rrec or {}).get("keywords") or []:
                    tags.append(("race_keyword", kw))
                    if kw not in race_vocab:
                        _race_kw_outside[kw] = _race_kw_outside.get(kw, 0) + 1
            elif kind == "background":
                # v0.34.0：backgrounds 侧表（AI 一句话概要）+ background_keyword 语义标签
                brec = background_enrich.get((name, source, edition))
                ab_payload = _ability_payload(e)
                if ab_payload:
                    conn.execute(
                        "INSERT INTO background_ability (entry_id, payload)"
                        " VALUES (?,?)",
                        (entry_id, ab_payload),
                    )
                if brec:
                    conn.execute(
                        "INSERT INTO backgrounds (entry_id, summary) VALUES (?,?)",
                        (entry_id, brec.get("summary", "")),
                    )
                tags = []
                for kw in (brec or {}).get("keywords") or []:
                    tags.append(("background_keyword", kw))
                    if kw not in background_vocab:
                        _background_kw_outside[kw] = (
                            _background_kw_outside.get(kw, 0) + 1
                        )
            elif kind == "feat":
                # v0.26.1 校验：带 category（2024 体系特征）的专长必须落 2024，
                # 防止新发布的 2024 书漏进 EDITION_2024_SOURCES 而误判 2014。
                if e.get("category") and edition != "2024":
                    _feat_category_wrong.add(f"{name}|{source}|{edition}")
                rec = feat_enrich.get((name, source, edition))
                tags = _feat_tags(e, rec)
                if rec and rec.get("summary"):
                    conn.execute(
                        "INSERT INTO feats (entry_id, summary) VALUES (?,?)",
                        (entry_id, rec["summary"]),
                    )
            else:
                tags = []
            for facet, value in tags:
                conn.execute(
                    "INSERT OR IGNORE INTO entry_tags (entry_id, facet, value)"
                    " VALUES (?,?,?)",
                    (entry_id, facet, value),
                )
            n += 1
        # 再版别名：旧名 → 再版条目（INSERT OR IGNORE，同名可能多目标）
        for old_name, (tname, tsrc) in reprint_redirect.items():
            target = conn.execute(
                "SELECT id FROM entries WHERE kind = ? AND name = ? AND source = ?",
                (kind, tname, tsrc),
            ).fetchone()
            if target:
                conn.execute(
                    "INSERT OR IGNORE INTO aliases (alias, entry_id) VALUES (?,?)",
                    (old_name, target[0]),
                )
        # 英文旧名别名：使英文旧版名（reprintedAs 前的 ENG_name）可精确命中新版条目
        for old_eng, (tname, tsrc) in eng_redirect.items():
            target = conn.execute(
                "SELECT id FROM entries WHERE kind = ? AND name = ? AND source = ?",
                (kind, tname, tsrc),
            ).fetchone()
            if target:
                conn.execute(
                    "INSERT OR IGNORE INTO aliases (alias, entry_id) VALUES (?,?)",
                    (old_eng, target[0]),
                )
        counts[kind] = n
        print(f"  {kind}: {n} 条")

    # --- 职业 / 子职 / 特性 ---
    class_data = _load_class_data(data_dir)
    n_class = 0
    class_entry_ids: dict[tuple[str, str], int] = {}  # (name, source) → entry_id
    subclass_entry_ids: dict[tuple[str, str], int] = {}  # (name, source) → entry_id
    for cls in class_data["class"]:
        name = (cls.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        source = str(cls.get("source") or "UNKNOWN")
        eng = str(cls.get("ENG_name") or "")
        edition = edition_of_source(source)
        cur = conn.execute(
            "INSERT OR REPLACE INTO entries"
            " (kind, name, eng_name, source, edition, body, is_machine)"
            " VALUES ('class',?,?,?,?,?,?)",
            (name, eng, source, edition, "", 0),
        )
        class_entry_ids[(name, source)] = cur.lastrowid
        for alias in {name.lower(), eng.lower()}:
            if alias:
                conn.execute(
                    "INSERT OR IGNORE INTO aliases (alias, entry_id) VALUES (?,?)",
                    (alias, cur.lastrowid),
                )
        n_class += 1
    for sub in class_data["subclass"]:
        name = (sub.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        source = str(sub.get("source") or "UNKNOWN")
        eng = str(sub.get("ENG_name") or "")
        cur = conn.execute(
            "INSERT OR REPLACE INTO entries"
            " (kind, name, eng_name, source, edition, body, is_machine)"
            " VALUES ('subclass',?,?,?,?,?,?)",
            (name, eng, source, edition_of_source(source), "", 0),
        )
        subclass_entry_ids[(name, source)] = cur.lastrowid
        for alias in {name.lower(), eng.lower()}:
            if alias:
                conn.execute(
                    "INSERT OR IGNORE INTO aliases (alias, entry_id) VALUES (?,?)",
                    (alias, cur.lastrowid),
                )

    # --- v0.35.0 职业法术表落库（职业条目已入 entries，解析英文职业名→中文） ---
    # 英文职业名 → 中文名（entries.kind='class' 的 name，同名多版本取第一个）。
    en_to_cn_class: dict[str, str] = {}
    for cls in class_data["class"]:
        eng_c = str(cls.get("ENG_name") or "").strip()
        cn_c = (cls.get("name") or "").strip()
        if eng_c and cn_c and eng_c.lower() not in en_to_cn_class:
            en_to_cn_class[eng_c.lower()] = cn_c
    unresolved_classes: set[str] = set()
    n_spell_class = 0
    for entry_id, en_c in pending_spell_classes:
        cn_c = en_to_cn_class.get(en_c.lower())
        if not cn_c:
            unresolved_classes.add(en_c)
            continue
        conn.execute(
            "INSERT OR IGNORE INTO spell_classes (entry_id, class_name)"
            " VALUES (?,?)",
            (entry_id, cn_c),
        )
        n_spell_class += 1
    counts["spellClass"] = n_spell_class
    if unresolved_classes:
        print(
            "  [warn] spell_classes 无法解析的职业名（英文源职业不在库内，"
            "已跳过）: " + ", ".join(sorted(unresolved_classes)[:20])
        )

    # --- v0.33.0 职业/子职富化（classes 侧表 + class_keyword/class_role/subclass_keyword） ---
    # 概要/关键字/定位来自 class_enrich 补丁（AI 逐条生成）；职业定位 role 仅职业有。
    class_vocab = {kw for words in CLASS_KEYWORD_TAGS.values() for kw in words}
    subclass_vocab = {kw for words in SUBCLASS_KEYWORD_TAGS.values() for kw in words}
    n_class_enrich = 0
    n_subclass_enrich = 0
    for cls in class_data["class"]:
        name = (cls.get("name") or "").strip()
        source = str(cls.get("source") or "UNKNOWN")
        eid = class_entry_ids.get((name, source))
        if eid is None:
            continue
        rec = class_enrich.get((name, source, edition_of_source(source)))
        if not rec:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO classes (entry_id, summary, role)"
            " VALUES (?,?,?)",
            (eid, rec.get("summary", ""), rec.get("role", "")),
        )
        for kw in rec.get("keywords") or []:
            conn.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, facet, value)"
                " VALUES (?,?,?)",
                (eid, "class_keyword", kw),
            )
            if kw not in class_vocab:
                _class_kw_outside[kw] = _class_kw_outside.get(kw, 0) + 1
        role = rec.get("role")
        if role:
            conn.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, facet, value)"
                " VALUES (?,?,?)",
                (eid, "class_role", role),
            )
        n_class_enrich += 1
    for sub in class_data["subclass"]:
        name = (sub.get("name") or "").strip()
        source = str(sub.get("source") or "UNKNOWN")
        eid = subclass_entry_ids.get((name, source))
        if eid is None:
            continue
        rec = class_enrich.get((name, source, edition_of_source(source)))
        if not rec:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO classes (entry_id, summary, role)"
            " VALUES (?,?,?)",
            (eid, rec.get("summary", ""), ""),
        )
        for kw in rec.get("keywords") or []:
            conn.execute(
                "INSERT OR IGNORE INTO entry_tags (entry_id, facet, value)"
                " VALUES (?,?,?)",
                (eid, "subclass_keyword", kw),
            )
            if kw not in subclass_vocab:
                _subclass_kw_outside[kw] = _subclass_kw_outside.get(kw, 0) + 1
        n_subclass_enrich += 1
    print(
        f"  class_enrich: {n_class_enrich} 条 / subclass_enrich:"
        f" {n_subclass_enrich} 条"
    )

    # --- v0.18 规则引擎侧表（class_combat / starting_equipment / subclass_caster） ---
    # 子职重复行过滤：同 (className, name, source) 存在 caster 为空与 1/3 两行，
    # 只收 caster 非空的声明（见源数据 class-*.json 结构）。
    subclass_caster_seen: set[tuple[str, str, str]] = set()
    for cls in class_data["class"]:
        name = (cls.get("name") or "").strip()
        source = str(cls.get("source") or "UNKNOWN")
        eid = class_entry_ids.get((name, source))
        if eid is None:
            continue
        hd = cls.get("hd") or {}
        faces = hd.get("faces") if isinstance(hd, dict) else None
        saves = cls.get("proficiency")
        saves_json = (
            json.dumps(saves, ensure_ascii=False)
            if isinstance(saves, list) else "[]"
        )
        caster = cls.get("casterProgression") or ""
        conn.execute(
            "INSERT INTO class_combat"
            " (entry_id, hd_faces, saves, caster, spell_ability)"
            " VALUES (?,?,?,?,?)",
            (eid, faces, saves_json, caster, cls.get("spellcastingAbility") or ""),
        )
        se = cls.get("startingEquipment")
        if isinstance(se, dict) and se:
            conn.execute(
                "INSERT INTO class_starting_equipment (entry_id, payload)"
                " VALUES (?,?)",
                (eid, json.dumps(se, ensure_ascii=False, separators=(",", ":"))),
            )
    for sub in class_data["subclass"]:
        caster = sub.get("casterProgression")
        if not caster:
            continue  # 重复声明行（caster 为空），跳过
        key = (
            str(sub.get("className") or "").strip(),
            (sub.get("name") or "").strip(),
            str(sub.get("source") or ""),
        )
        if not key[0] or not key[1] or key in subclass_caster_seen:
            continue
        subclass_caster_seen.add(key)
        conn.execute(
            "INSERT INTO subclass_caster"
            " (class_name, subclass_name, subclass_short, source, caster, spell_ability)"
            " VALUES (?,?,?,?,?,?)",
            (
                key[0],
                key[1],
                (sub.get("shortName") or "").strip(),
                key[2],
                caster,
                sub.get("spellcastingAbility") or "",
            ),
        )
    counts["class"] = n_class
    counts["subclass"] = len(class_data["subclass"])
    counts["classFeature"] = len(class_data["classFeature"])
    counts["subclassFeature"] = len(class_data["subclassFeature"])
    print(
        f"  class: {n_class} / subclass: {counts['subclass']}"
        f" / classFeature: {counts['classFeature']}"
        f" / subclassFeature: {counts['subclassFeature']}"
    )

    # --- 职业特性表（class_features） ---
    # 子职短名 → 显示名映射：5etools 的 subclassFeature 只带 subclassShortName
    # （如「塑能」），而 subclass 条目的 name 才是显示名（如「塑能学派」）。
    # 214/322 个子职短名与显示名不同，必须双存，否则按显示名查不到特性。
    subclass_display: dict[tuple[str, str, str], str] = {}
    for sub in class_data["subclass"]:
        cls_n = (sub.get("className") or "").strip()
        short = (sub.get("shortName") or "").strip()
        src = str(sub.get("source") or "")
        if cls_n and short:
            subclass_display[(cls_n, short, src)] = (sub.get("name") or short).strip()

    def _resolve_subclass(cls_n: str, short: str, src: str) -> tuple[str, str]:
        """返回 (显示名, 短名)：优先显示名，映射不到时退回短名。"""
        display = subclass_display.get((cls_n, short, src))
        if not display:
            display = subclass_display.get((cls_n, short, ""))
        if not display:
            display = short
        return display, short

    for feat in class_data["classFeature"]:
        name = (feat.get("name") or "").strip()
        cls_name = (feat.get("className") or "").strip()
        if not name or not cls_name:
            skipped += 1
            continue
        body = _flatten_entries(feat.get("entries"))
        if not body:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO class_features"
            " (class_name, subclass_name, subclass_short, source, level, name, summary, body)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cls_name, "", "", feat.get("source"), feat.get("level"), name, _first_sentence(body), body),
        )
    for feat in class_data["subclassFeature"]:
        name = (feat.get("name") or "").strip()
        cls_name = (feat.get("className") or "").strip()
        short = (feat.get("subclassShortName") or "").strip()
        if not name or not cls_name:
            skipped += 1
            continue
        body = _flatten_entries(feat.get("entries"))
        if not body:
            skipped += 1
            continue
        sub_name, sub_short = _resolve_subclass(
            cls_name, short, str(feat.get("subclassSource") or feat.get("source") or "")
        )
        conn.execute(
            "INSERT INTO class_features"
            " (class_name, subclass_name, subclass_short, source, level, name, summary, body)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (cls_name, sub_name, sub_short, feat.get("source"), feat.get("level"), name, _first_sentence(body), body),
        )

    # --- meta ---
    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data_version = f"{sha}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('data_version', ?)",
        (data_version,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('source_commit', ?)",
        (sha,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('build_time', ?)",
        (build_time,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (SCHEMA_VERSION,),
    )
    conn.commit()

    # --- 落盘（先写 tmp 再原子替换） ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="dnd_kb_", suffix=".db", dir=str(out_path.parent)
    )
    os.close(fd)
    try:
        bak = sqlite3.connect(tmp_path)
        conn.backup(bak)
        bak.close()
        os.replace(tmp_path, out_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    conn.close()

    # --- version.json ---
    version = {
        "data_version": data_version,
        "source_commit": sha,
        "build_time": build_time,
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        "skipped": skipped,
    }
    version_path = out_path.with_name("version.json")
    version_path.write_text(
        json.dumps(version, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # v0.35.0：英文源未命中法术报告（en_dir 提供时），不阻塞构建。
    if en_spell_classes and spell_class_miss:
        report_path = out_path.with_name("spell_classes_unmatched.json")
        report_path.write_text(
            json.dumps(
                {"total": len(spell_class_miss), "items": spell_class_miss},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"  [warn] spell_classes 未命中 {len(spell_class_miss)} 条法术"
            f"（见 {report_path.name}，前 10："
            + "、".join(spell_class_miss[:10])
            + "）"
        )
    # 未收录的「X伤害」上下文词告警（前 20 个，按出现次数排序）
    if _unmatched_damage_words:
        top = sorted(
            _unmatched_damage_words.items(), key=lambda kv: kv[1], reverse=True
        )[:20]
        print("[build_kb] 词表未覆盖的「X伤害」上下文词（可补别名）：")
        for w, c in top:
            print(f"  [warn] {w}伤害 x{c}")
    # 词表外专长关键字告警（AI 生成自由词，前 30 个，按出现次数排序）
    if _feat_kw_outside:
        top = sorted(
            _feat_kw_outside.items(), key=lambda kv: kv[1], reverse=True
        )[:30]
        print("[build_kb] 词表外专长关键字（自由词，可考虑补入 FEAT_KEYWORD_TAGS）：")
        for w, c in top:
            print(f"  [warn] {w} x{c}")
    # 带 category 却未落 2024 的专长告警（源列表漏判，需补 EDITION_2024_SOURCES）
    if _feat_category_wrong:
        print("[build_kb] 带 category 但未落 2024 的专长（需补 EDITION_2024_SOURCES）：")
        for w in sorted(_feat_category_wrong)[:20]:
            print(f"  [warn] {w}")
    # 词表外法术关键字告警（AI 生成自由词，前 30 个，按出现次数排序）
    if _spell_kw_outside:
        top = sorted(
            _spell_kw_outside.items(), key=lambda kv: kv[1], reverse=True
        )[:30]
        print("[build_kb] 词表外法术关键字（自由词，可考虑补入 SPELL_KEYWORD_TAGS）：")
        for w, c in top:
            print(f"  [warn] {w} x{c}")
    # 词表外职业关键字告警（v0.33.0，AI 生成自由词，前 30 个）
    if _class_kw_outside:
        top = sorted(
            _class_kw_outside.items(), key=lambda kv: kv[1], reverse=True
        )[:30]
        print("[build_kb] 词表外职业关键字（自由词，可考虑补入 CLASS_KEYWORD_TAGS）：")
        for w, c in top:
            print(f"  [warn] {w} x{c}")
    # 词表外子职关键字告警（v0.33.0，AI 生成自由词，前 30 个）
    if _subclass_kw_outside:
        top = sorted(
            _subclass_kw_outside.items(), key=lambda kv: kv[1], reverse=True
        )[:30]
        print("[build_kb] 词表外子职关键字（自由词，可考虑补入 SUBCLASS_KEYWORD_TAGS）：")
        for w, c in top:
            print(f"  [warn] {w} x{c}")
    # 词表外种族关键字告警（v0.34.0，AI 生成自由词，前 30 个）
    if _race_kw_outside:
        top = sorted(
            _race_kw_outside.items(), key=lambda kv: kv[1], reverse=True
        )[:30]
        print("[build_kb] 词表外种族关键字（自由词，可考虑补入 RACE_KEYWORD_TAGS）：")
        for w, c in top:
            print(f"  [warn] {w} x{c}")
    # 词表外背景关键字告警（v0.34.0，AI 生成自由词，前 30 个）
    if _background_kw_outside:
        top = sorted(
            _background_kw_outside.items(), key=lambda kv: kv[1], reverse=True
        )[:30]
        print("[build_kb] 词表外背景关键字（自由词，可考虑补入 BACKGROUND_KEYWORD_TAGS）：")
        for w, c in top:
            print(f"  [warn] {w} x{c}")
    print(f"[build_kb] 完成: {out_path} (skip {skipped})")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description="构建跑团助手 DND 知识库 SQLite")
    parser.add_argument("data_dir", help="5etools-cn data/ 目录路径")
    parser.add_argument(
        "--out",
        default=str(_PKG_DIR / "kb_data" / "dnd_kb.db"),
        help="输出 db 路径（默认插件 kb_data/dnd_kb.db）",
    )
    parser.add_argument("--commit", default=None, help="源仓库 commit（默认 git 探测）")
    parser.add_argument(
        "--en-spell-lookup",
        default=None,
        help="英文 5e.tools 生成的法术源查找表 gendata-spell-source-lookup.json"
        "（可选；提供后构建职业法术表 spell_classes）",
    )
    args = parser.parse_args()
    build(
        Path(args.data_dir),
        Path(args.out),
        commit=args.commit,
        en_lookup=args.en_spell_lookup,
    )


if __name__ == "__main__":
    main()

"""build_advisor.py — 构筑咨询（v0.35.0）纯函数层。

把「构筑目标/关键词」与「角色卡现状」确定性组装成候选档案（BuildDossier），
供 LLM 组织推荐话术。条目全部来自知识库反向检索（entry_tags / 侧表），
杜绝凭记忆编造条目名；专长前置条件只做「标注不过滤」（✅/❌/⚠️），
由 LLM 结合标注给出建议。无状态、不持久化（构筑方案是一次性建议）。

设计约束（与 docs/adr/0012 一致）：
- 幻觉控制只在输入侧：dossier 里的名字全部来自工具返回，docstring/守则
  明文禁止 LLM 凭记忆补充条目名；
- level_up 场景由 main.py 先读活跃角色卡，本模块只做纯计算；
- 版本过滤：new_build 按群规则 edition 过滤（""=双版本并存并标注）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .kb_enums import (
    _BACKGROUND_KEYWORD_ALIASES,
    _CLASS_KEYWORD_ALIASES,
    _CLASS_ROLE_ALIASES,
    _FEAT_ABILITY_ALIASES,
    _FEAT_KEYWORD_ALIASES,
    _RACE_KEYWORD_ALIASES,
    _SPELL_KEYWORD_ALIASES,
    _SUBCLASS_KEYWORD_ALIASES,
    ABILITY_CN,
    edition_of_source,
    resolve_ability,
    resolve_background_keyword,
    resolve_class_keyword,
    resolve_class_role,
    resolve_feat_keyword,
    resolve_race_keyword,
    resolve_spell_keyword,
    resolve_subclass_keyword,
)

# 全部标签别名（含属性/定位/各关键字词表）→ canonical，供 CJK 复合词抽取。
_ALL_ALIASES: dict[str, str] = {}
for _m in (
    _FEAT_ABILITY_ALIASES,
    _CLASS_ROLE_ALIASES,
    _FEAT_KEYWORD_ALIASES,
    _SPELL_KEYWORD_ALIASES,
    _CLASS_KEYWORD_ALIASES,
    _SUBCLASS_KEYWORD_ALIASES,
    _RACE_KEYWORD_ALIASES,
    _BACKGROUND_KEYWORD_ALIASES,
):
    for _k, _v in _m.items():
        _lk = str(_k).strip().lower()
        if _lk:
            # 长别名优先（复合词抽取取最长命中）
            _ALL_ALIASES[_lk] = _v
_ALIAS_KEYS_BY_LEN: list[str] = sorted(
    _ALL_ALIASES, key=len, reverse=True
)

# 目标词可命中的 facet → 组装家族（同一词可命中多个家族，如「坦克」）。
FAMILY_FACETS: dict[str, tuple[str, ...]] = {
    "class": ("class_keyword", "class_role"),
    "subclass": ("subclass_keyword",),
    "race": ("race_keyword",),
    "background": ("background_keyword",),
    "feat": ("feat_keyword",),
    "spell": ("spell_keyword",),
}
_ALL_FACETS: tuple[str, ...] = tuple(
    f for fs in FAMILY_FACETS.values() for f in fs
)
_FACET_TO_FAMILY: dict[str, str] = {
    f: fam for fam, fs in FAMILY_FACETS.items() for f in fs
}

# 每维度候选上限（token 控制，见 docs/adr/0012）。
LIMITS: dict[str, int] = {
    "race": 5,
    "class": 5,
    "subclass": 3,
    "background": 5,
    "feat": 8,
    "spell": 10,
}
# 2024 规则：传奇恩惠（feat_type=传奇恩惠）19 级以上才可选。
EPIC_BOON_LEVEL = 19
# 无目标词时各维度兜底条数（确定性，按名称排序取前 N）。
FALLBACK_LIMIT = 5

# 属性中文 → AbilityScores 属性名（character.py）。
_ABILITY_ATTR: dict[str, str] = {
    "力量": "strength", "敏捷": "dexterity", "体质": "constitution",
    "智力": "intelligence", "感知": "wisdom", "魅力": "charisma",
}

# 全施法者（caster=full）各等级最高可用法术环（5e 施法表，确定性硬表）。
_FULL_CASTER_LEVEL_CAP: tuple[int, ...] = (
    0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5,
    6, 6, 7, 7, 8, 8, 9, 9, 9, 9,
)


def _max_spell_level(caster: str, level: int) -> int:
    """职业在给定等级可用的最高法术环（按施法进度类型）。"""
    level = max(1, min(level, 20))
    if caster == "full":
        return _FULL_CASTER_LEVEL_CAP[level]
    if caster in ("1/2", "artificer"):
        # 半施法者（圣武士/游侠/奇械师）：1级1环，3级1环，5级2环，9级3环…
        return max(1, (level + 3) // 4)
    if caster == "1/3":
        # 三分之一施法（奥法骑士/诡术师）：3级1环，7级2环，13级3环，19级4环
        return max(1, (level + 5) // 6)
    if caster == "pact":
        # 魔契师：pact 魔能，最高 5 环
        return max(1, min((level + 1) // 2, 5))
    return 0


def split_terms(text: str) -> list[str]:
    """自由文本按分隔符切词（,，、;；空白），去空去重保序。"""
    out: list[str] = []
    for tok in re.split(r"[,，、;；\s]+", text or ""):
        tok = tok.strip()
        if tok and tok not in out:
            out.append(tok)
    return out


def normalize_term(term: str) -> str:
    """标签词别名归一：按各词表 resolver 顺序取首个命中，否则原样返回。"""
    t = (term or "").strip()
    if not t:
        return ""
    for resolver in (
        resolve_ability,
        resolve_class_role,
        resolve_class_keyword,
        resolve_subclass_keyword,
        resolve_race_keyword,
        resolve_background_keyword,
        resolve_feat_keyword,
        resolve_spell_keyword,
    ):
        hit = resolver(t)
        if hit:
            return hit
    return t


def _extract_tag_terms(text: str) -> list[str]:
    """从 CJK 复合词中抽取已知标签词（如「前排打手」→ 前排→坦克）。

    按别名长度从长到短做子串匹配（「重武器大师」应命中「重武器」而非「武器」），
    返回抽取到的 canonical 词列表（去重保序）。只用于整词未命中时的回退。
    """
    t = (text or "").strip().lower()
    if not t:
        return []
    out: list[str] = []
    for alias in _ALIAS_KEYS_BY_LEN:
        if not alias or alias not in t:
            continue
        canon = _ALL_ALIASES[alias]
        if canon not in out:
            out.append(canon)
    return out


def resolve_goal_tags(
    goal: str, keywords: str, kb: Any
) -> dict[str, dict[str, list[str]]]:
    """goal 自由文本 + keywords 显式标签 → {家族: {facet: [canonical 标签…]}}。

    goal 每个词：别名归一 → 查库确认存在于哪些 facet（value_facets），
    按 facet 归属家族+维度；整词未命中时做 CJK 复合词抽取（_extract_tag_terms）
    再查库；库内不存在的词直接丢弃（避免无效标签）。
    keywords 同规则（LLM 精准补充时同样先归一）。
    """
    families: dict[str, dict[str, list[str]]] = {}

    def _add(canon: str) -> None:
        for facet in kb.value_facets(canon, _ALL_FACETS):
            fam = _FACET_TO_FAMILY.get(facet)
            if not fam:
                continue
            bucket = families.setdefault(fam, {})
            if canon not in bucket.setdefault(facet, []):
                bucket[facet].append(canon)

    for term in split_terms(goal) + split_terms(keywords):
        norm = normalize_term(term)
        if norm and kb.value_facets(norm, _ALL_FACETS):
            _add(norm)
            continue
        # 整词未命中：CJK 复合词抽取（仅对中文/中英混合词有意义）
        if re.search(r"[\u4e00-\u9fff]", term):
            for canon in _extract_tag_terms(term):
                _add(canon)
    return families


def _query_union(
    kb: Any,
    kind: str,
    tags_by_facet: dict[str, list[str]],
    edition: str,
    limit: int,
    *,
    per_tag: int = 15,
    exclude_names: set[str] | None = None,
) -> list[Any]:
    """按 facet→标签 列表反查并合并去重，命中标签数多的排前。

    同一家族内多个标签取并集（OR），跨家族由调用方拆两次调用再合并。
    edition 非空时后过滤（与知识库「同名多版本并存」口径一致）。
    """
    counts: dict[str, int] = {}
    rows: dict[str, Any] = {}
    for facet, taglist in tags_by_facet.items():
        for tag in taglist:
            res = kb.filter(kind, limit=per_tag, tags=[(facet, tag)])
            for e in res.entries:
                if edition and e.edition != edition:
                    continue
                if exclude_names and e.name in exclude_names:
                    continue
                counts[e.name] = counts.get(e.name, 0) + 1
                rows.setdefault(e.name, e)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [rows[n] for n, _ in top]


def _feat_type_names(kb: Any, feat_type: str, edition: str) -> set[str]:
    """取指定专长类型（feat_type facet）的条目名集合（版本后过滤）。"""
    res = kb.filter("feat", limit=200, tags=[("feat_type", feat_type)])
    return {
        e.name for e in res.entries if not edition or e.edition == edition
    }


def _entry_brief(e: Any, family: str) -> dict[str, str]:
    """KbEntry → dossier 条目 {name, summary, version}（各家族概要字段不同）。"""
    summary = ""
    if family == "class":
        summary = e.class_summary or ""
        role = e.class_role or ""
        if role and summary:
            summary = f"[{role}] {summary}"
        elif role:
            summary = f"[{role}]"
    elif family == "subclass":
        summary = e.class_summary or ""
    elif family == "race":
        summary = e.race_summary or ""
    elif family == "background":
        summary = e.background_summary or ""
    elif family == "feat":
        summary = e.feat_summary or ""
    elif family == "spell":
        level = e.level if e.level is not None else "?"
        school = e.school or ""
        summary = f"{level}环·{school} {e.spell_summary or ''}".strip()
    return {
        "name": e.name,
        "summary": summary,
        "version": f"{e.source}·{e.edition}",
    }


def assemble_new_build(
    goal: str,
    keywords: str,
    kb: Any,
    edition: str = "",
    level: int = 0,
) -> dict[str, Any]:
    """从零构筑：按目标/关键词反查各维度候选档案（new_build）。

    - edition：""=双版本并存并标注；非空=只取该版本条目。
    - level：目标等级（0=未定）；<19 时排除「传奇恩惠」专长。
    """
    fam = resolve_goal_tags(goal, keywords, kb)
    dossier: dict[str, Any] = {
        "edition": edition or "双版本（已标注）",
        "level": level or "未定",
        "goal_tags": fam,
    }

    # -- 职业（class_keyword + class_role 并集）--
    cls_tags: dict[str, list[str]] = dict(fam.get("class", {}))
    if not any(cls_tags.values()):
        # 无目标词兜底：四大定位各取 2 条
        role_list: list[Any] = []
        for role in ("武者", "奥法", "神职", "专家"):
            res = kb.filter("class", limit=2, tags=[("class_role", role)])
            role_list.extend(
                e for e in res.entries if not edition or e.edition == edition
            )
        seen: set[str] = set()
        classes = [
            e for e in role_list
            if not (e.name in seen or seen.add(e.name))
        ][:FALLBACK_LIMIT]
    else:
        classes = _query_union(kb, "class", cls_tags, edition, LIMITS["class"])
    dossier["classes"] = [_entry_brief(e, "class") for e in classes]

    # -- 子职（仅对候选职业中的前 3 个；子职必须属于该职业）--
    # 子职标签反查结果跨职业（同一标签命中多职业子职），按职业的
    # class_features.subclass_candidates（权威子职名清单）收敛。
    sub_by_class: dict[str, list[dict[str, str]]] = {}
    sub_tags: dict[str, list[str]] = dict(fam.get("subclass", {}))
    if sub_tags.get("subclass_keyword"):
        subs = _query_union(
            kb, "subclass", sub_tags, edition, LIMITS["subclass"] * 3,
            per_tag=30,
        )
        for cls_entry in classes[:3]:
            cand = kb.class_features(cls_entry.name).subclass_candidates
            cand_set = set(cand)
            own = [s for s in subs if s.name in cand_set][:LIMITS["subclass"]]
            if own:
                sub_by_class[cls_entry.name] = [
                    _entry_brief(s, "subclass") for s in own
                ]
    dossier["subclasses"] = sub_by_class

    def _dedupe(entries: list[Any]) -> list[Any]:
        seen: set[str] = set()
        out: list[Any] = []
        for e in entries:
            if e.name in seen:
                continue
            seen.add(e.name)
            out.append(e)
        return out

    # -- 种族 / 背景（无目标词时按名称取前 N 兜底）--
    race_tags: dict[str, list[str]] = dict(fam.get("race", {}))
    if race_tags.get("race_keyword"):
        races = _query_union(kb, "race", race_tags, edition, LIMITS["race"])
    else:
        races = _dedupe(
            e for e in kb.filter("race", limit=50).entries
            if not edition or e.edition == edition
        )[:LIMITS["race"]]
    dossier["races"] = [_entry_brief(e, "race") for e in races]

    bg_tags: dict[str, list[str]] = dict(fam.get("background", {}))
    if bg_tags.get("background_keyword"):
        bgs = _query_union(
            kb, "background", bg_tags, edition, LIMITS["background"]
        )
    else:
        bgs = _dedupe(
            e for e in kb.filter("background", limit=50).entries
            if not edition or e.edition == edition
        )[:LIMITS["background"]]
    dossier["backgrounds"] = [_entry_brief(e, "background") for e in bgs]

    # -- 专长（排除未达 19 级的传奇恩惠）--
    exclude: set[str] = set()
    if level and level < EPIC_BOON_LEVEL:
        exclude |= _feat_type_names(kb, "传奇恩惠", edition)
    feat_tags: dict[str, list[str]] = dict(fam.get("feat", {}))
    if feat_tags.get("feat_keyword"):
        feats = _query_union(
            kb, "feat", feat_tags, edition, LIMITS["feat"],
            exclude_names=exclude or None,
        )
    else:
        feats = [
            e for e in kb.filter("feat", limit=FALLBACK_LIMIT).entries
            if (not edition or e.edition == edition)
            and e.name not in exclude
        ][:LIMITS["feat"]]
    dossier["feats"] = [_entry_brief(e, "feat") for e in feats]

    # -- 法术（仅当命中法术语义标签；环阶上限 = 目标等级或 5）--
    spell_tags = fam.get("spell", {}).get("spell_keyword", [])
    if spell_tags:
        cap = min(level, 9) if level else 5
        spells = kb.filter(
            "spell",
            limit=LIMITS["spell"],
            tags=[("spell_keyword", t) for t in spell_tags],
            level_max=cap,
        ).entries
        if edition:
            spells = [e for e in spells if e.edition == edition]
        dossier["spells"] = [_entry_brief(e, "spell") for e in spells]
    else:
        dossier["spells"] = []

    dossier["hint"] = (
        "以上条目均来自知识库；请基于这些条目组织推荐。"
        "玩家确认构筑后，可用 guide_chargen 的 start 预填职业/种族/背景开始车卡。"
    )
    return dossier


def _check_prereq_ability(
    value: str, sheet: Any
) -> str:
    """prereq_ability「力量 13」→ ✅满足 / ❌缺力量13。"""
    m = re.match(r"^\s*([\u4e00-\u9fff]+)\s*(\d+)\s*$", value)
    if not m:
        return f"⚠️前置无法解析（{value}）"
    ab_name, threshold = m.group(1), int(m.group(2))
    attr = _ABILITY_ATTR.get(ab_name)
    if not attr:
        return f"⚠️未知属性（{value}）"
    score = int(getattr(sheet.ability_scores, attr, 0) or 0)
    if score >= threshold:
        return f"✅{ab_name}{threshold}"
    return f"❌缺{ab_name}{threshold}"


def check_prereqs(
    facets: dict[str, list[str]], sheet: Any
) -> list[str]:
    """专长全部前置 facet → 标注列表（✅/❌/⚠️，不过滤）。

    规则：
    - prereq_ability：按卡面属性值校验；
    - prereq_race：卡面种族名包含关系匹配；「小型种族」无法校验（卡未记录体型）；
    - prereq_feat：卡面已有专长精确/去括号基础名匹配；
    - prereq_feature：卡面未记录特性，标注人工核对。
    """
    marks: list[str] = []
    for facet in (
        "prereq_ability", "prereq_race", "prereq_feat", "prereq_feature",
    ):
        for value in facets.get(facet, []):
            if facet == "prereq_ability":
                marks.append(_check_prereq_ability(value, sheet))
            elif facet == "prereq_race":
                race = (getattr(sheet, "race", "") or "").strip()
                if value == "小型种族":
                    marks.append("⚠️小型种族（卡未记录体型，请人工核对）")
                elif race and (value in race or race in value):
                    marks.append(f"✅种族「{value}」")
                else:
                    marks.append(f"❌需种族「{value}」")
            elif facet == "prereq_feat":
                feats = [str(f) for f in (getattr(sheet, "feats", None) or [])]
                base = re.split(r"[（(]", value, maxsplit=1)[0].strip()
                if value in feats or base in feats:
                    marks.append(f"✅专长「{base}」")
                else:
                    marks.append(f"❌需专长「{base}」")
            else:  # prereq_feature
                marks.append(f"⚠️需特性「{value}」（卡面未记录，请人工核对）")
    return marks


def _sheet_classes(sheet: Any) -> list[Any]:
    return list(getattr(sheet, "classes", None) or [])


def assemble_level_up(sheet: Any, kb: Any) -> dict[str, Any]:
    """升级建议：读卡面现状，输出特性时间线 + 专长候选（前置标注）+ 法术建议。

    - 特性时间线：每职业按「下一级」取本职+子职特性（等级+名称+一句话概要）；
    - 专长候选：按卡面职业能力标签 + 已满足前置的属性专长 + 19 级传奇恩惠，
      逐条标注 ✅/❌/⚠️（标注不过滤）；
    - 法术建议：主职（等级最高）施法职业表，环阶上限按下一级施法表计算。
    """
    classes = _sheet_classes(sheet)
    total_level = sum(
        int(getattr(c, "level", 0) or 0) for c in classes
    )
    dossier: dict[str, Any] = {
        "card": {
            "name": getattr(sheet, "name", ""),
            "classes": [
                {
                    "class": getattr(c, "class_name", ""),
                    "subclass": getattr(c, "subclass", "") or "",
                    "level": int(getattr(c, "level", 0) or 0),
                }
                for c in classes
            ],
            "total_level": total_level,
            "ability_scores": {
                k: int(getattr(sheet.ability_scores, k, 0) or 0)
                for k in (
                    "strength", "dexterity", "constitution",
                    "intelligence", "wisdom", "charisma",
                )
            },
        },
    }
    if not classes:
        return {"error": "角色卡没有职业条目，请先完善角色卡或重新车卡"}

    edition = getattr(sheet, "edition", "") or ""

    # -- 特性时间线：每职业下一级（按卡面版本过滤，避免 2014/2024 双行）--
    timeline: list[dict[str, Any]] = []
    for c in classes:
        cls_name = getattr(c, "class_name", "")
        nxt = int(getattr(c, "level", 0) or 0) + 1
        if not cls_name:
            continue
        sub = getattr(c, "subclass", "") or None
        res = kb.class_features(
            cls_name, subclass=sub, level_min=nxt, level_max=nxt
        )
        for row in res.base_rows + res.subclass_rows:
            if edition and edition_of_source(row.source) != edition:
                continue
            timeline.append({
                "class": cls_name,
                "level": row.level,
                "name": row.name,
                "summary": row.summary,
            })
    timeline.sort(key=lambda r: (r["class"], r["level"] or 0, r["name"]))
    dossier["class_features_timeline"] = timeline

    # -- 专长候选 --
    feat_candidates: dict[str, dict[str, Any]] = {}
    next_level = total_level + 1
    epic_boons = _feat_type_names(kb, "传奇恩惠", edition)
    # 1) 卡面职业能力标签 → 专长能力标签（两词表 canonical 高度重叠）
    class_tag_sets: list[str] = []
    for c in classes:
        tags = kb.entry_tags_of(
            getattr(c, "class_name", ""), "class",
            facets=("class_keyword",),
        ).get("class_keyword", [])
        class_tag_sets.extend(tags)
    # 2) 卡面已满足前置的属性专长（如力量≥13 → prereq_ability=力量 13）
    satisfied_prereq: list[str] = []
    for ab_cn, attr in _ABILITY_ATTR.items():
        score = int(getattr(sheet.ability_scores, attr, 0) or 0)
        if score >= 13:
            satisfied_prereq.append(f"{ab_cn} 13")
    # 3) 19 级后的传奇恩惠
    if next_level >= EPIC_BOON_LEVEL:
        feat_candidates.update({
            e.name: _entry_brief(e, "feat")
            for e in kb.filter(
                "feat", limit=20, tags=[("feat_type", "传奇恩惠")]
            ).entries
            if not edition or e.edition == edition
        })
    # 汇聚：职业标签反查 + 满足前置的属性专长（未达 19 级排除传奇恩惠）
    union_names: dict[str, int] = {}
    rows_map: dict[str, Any] = {}
    pool: list[Any] = []
    if class_tag_sets:
        for tag in class_tag_sets:
            res = kb.filter("feat", limit=10, tags=[("feat_keyword", tag)])
            pool.extend(res.entries)
    for tag in satisfied_prereq:
        res = kb.filter("feat", limit=10, tags=[("prereq_ability", tag)])
        pool.extend(res.entries)
    for e in pool:
        if edition and e.edition != edition:
            continue
        if e.name in epic_boons:
            continue
        union_names[e.name] = union_names.get(e.name, 0) + 1
        rows_map.setdefault(e.name, e)
    for name, cnt in sorted(
        union_names.items(), key=lambda kv: (-kv[1], kv[0])
    )[:LIMITS["feat"]]:
        feat_candidates.setdefault(name, _entry_brief(rows_map[name], "feat"))
    # 前置标注（不过滤）
    for name, brief in list(feat_candidates.items()):
        e = rows_map.get(name)
        if e is None or e.entry_id is None:
            brief["prereq_check"] = ["⚠️未取到条目，请人工核对"]
            continue
        marks = check_prereqs(
            kb.feat_prereq_facets(e.entry_id), sheet
        )
        brief["prereq_check"] = marks or ["（无前置）"]
    dossier["feat_candidates"] = list(feat_candidates.values())

    # -- 法术建议（主职=等级最高；按下一级施法表算环阶上限）--
    primary = max(
        classes, key=lambda c: int(getattr(c, "level", 0) or 0)
    )
    p_name = getattr(primary, "class_name", "")
    nxt_total = total_level + 1
    combat = kb.class_combat(p_name, edition) if p_name else None
    if combat and combat.is_caster:
        cap = _max_spell_level(combat.caster, nxt_total)
        res = kb.spells_by_class(
            p_name, edition=edition, level_max=cap,
            limit=LIMITS["spell"],
        )
        dossier["spells"] = [
            _entry_brief(e, "spell") for e in res.entries
        ]
        dossier["spell_note"] = (
            f"主职「{p_name}」职业法术表（≤{cap}环，等级 {nxt_total}）"
        )
    else:
        dossier["spells"] = []

    dossier["hint"] = (
        "以上条目均来自知识库与角色卡现状；推荐必须基于本档案，"
        "禁止凭记忆补充条目名。兼职升级时请向玩家确认升哪一职业。"
    )
    return dossier


def dossier_to_text(dossier: dict[str, Any]) -> str:
    """BuildDossier → 紧凑 JSON 文本（工具返回给 LLM）。"""
    return json.dumps(dossier, ensure_ascii=False, separators=(",", ":"))

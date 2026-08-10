"""kb_build_lib.py — 构建期与运行期共享的条目渲染/侧表提取纯函数。

从 scripts/build_kb.py 抽取（v0.36.0 私设 overlay）：
- 正文渲染链：_kind_body 及各 kind 的 _*_body 与全部 _flatten_*/_fmt_* 依赖；
- 机翻判定 is_machine_entry（无 translator 或非人工白名单 → 机翻）；
- 数值解析 _parse_cr / _monster_type / _fmt_ac / _fmt_hp；
- 侧表提取工具 _ability_payload / _item_combat_cols / _item_value_weight。

scripts/build_kb.py（构建期）与 homebrew.py（运行期私设 overlay）共用，
保证「5etools 条目 → 正文/侧表」输出完全一致，避免两套实现漂移。
"""

from __future__ import annotations

import json
import re

from .kb_enums import (
    CONDITION_CN,
    DAMAGE_TYPE_CN,
    ITEM_TYPE_CODE,
    SCHOOL_CN_REV,
    SIZE_CN_REV,
    format_rarity,
)
from .kb_tags import clean_5etools_tags



HUMAN_TRANSLATORS: frozenset[str] = frozenset({"不全书"})


def _flatten_table(ent: dict) -> str:
    caption = clean_5etools_tags(str(ent.get("caption") or ""))
    rows = ent.get("rows") or []
    col_labels = ent.get("colLabels") or []
    lines = []
    if caption:
        lines.append(caption)
    if col_labels:
        lines.append("、".join(clean_5etools_tags(str(c)) for c in col_labels))
    for row in rows:
        cells = [clean_5etools_tags(str(c)) for c in row]
        lines.append("、".join(cells))
    return "\n".join(l for l in lines if l)


def _flatten_entry(ent: object, depth: int = 0) -> str:
    if isinstance(ent, str):
        return clean_5etools_tags(ent)
    if not isinstance(ent, dict):
        return clean_5etools_tags(str(ent))
    etype = ent.get("type")
    if etype == "table":
        return _flatten_table(ent)
    if etype == "list":
        # 5etools 列表块：{"type": "list", "items": [str|dict, ...]}，逐项成行。
        items = ent.get("items")
        if not isinstance(items, list) or not items:
            return ""
        parts = []
        for item in items:
            block = _flatten_entry(item, depth + 1)
            if block:
                parts.append(block)
        return "\n".join(parts)
    if etype == "item":
        # 单项条目：{"type": "item", "name": ..., "entry": "..."}
        name = ent.get("name")
        entry = ent.get("entry")
        if isinstance(entry, str):
            body = clean_5etools_tags(entry)
            head = clean_5etools_tags(str(name)) if name else ""
            if head and body:
                return f"{head}：{body}"
            return head or body
        return ""
    name = ent.get("name")
    sub = _flatten_entries(ent.get("entries"), depth + 1)
    if not name:
        return sub
    head = clean_5etools_tags(str(name))
    if sub:
        return f"{head}：\n{sub}"
    return head


def _flatten_entries(entries: object, depth: int = 0) -> str:
    if not isinstance(entries, list) or not entries:
        return ""
    blocks = []
    for ent in entries:
        block = _flatten_entry(ent, depth)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _first_sentence(text: str, limit: int = 100) -> str:
    text = text.replace("\n", " ").strip()
    for sep in ("。", "；"):
        idx = text.find(sep)
        if 0 < idx <= limit:
            text = text[: idx + 1]
            break
    if len(text) > limit:
        text = text[:limit] + "…"
    return text.strip()


def _walk_texts(ent: object) -> list[str]:
    """递归收集 entries 树中的所有字符串叶（清洗前的原文）。"""
    texts: list[str] = []
    if isinstance(ent, str):
        texts.append(ent)
    elif isinstance(ent, list):
        for item in ent:
            texts.extend(_walk_texts(item))
    elif isinstance(ent, dict):
        for v in ent.values():
            texts.extend(_walk_texts(v))
    return texts


RE_DAMAGE_WORD = re.compile(
    "(" + "|".join(sorted(DAMAGE_TYPE_CN, key=len, reverse=True)) + ")伤害"
)


RE_ANY_DAMAGE = re.compile(r"(?:^|[^\u4e00-\u9fa5])([\u4e00-\u9fa5]{2})伤害")


_DAMAGE_WARN_STOPWORDS = frozenset(
    {
        "武器", "附加", "额外", "受到", "造成", "点", "再次", "每回合", "最大",
        "生命", "一个", "两个", "每个", "每次", "所有", "全部", "任何", "任意",
        "失去", "剩余", "一半", "部分",
    }
)


RE_CONDITION_TAG = re.compile(r"\{@condition ([^}|]+)")


_DEFENSE_HINTS = ("免疫", "抗性", "易伤", "免受", "抵抗", "减半", "一半")


_unmatched_damage_words: dict[str, int] = {}


def _extract_damage(texts: list[str]) -> list[str]:
    """从文本集合提取造成的伤害类型（canonical 中文，去重保序）。"""
    out: list[str] = []
    for t in texts:
        # 按句（含逗号）拆分：同一分句含「免疫/抗性/减半」等防御语义时跳过，
        # 避免把「火焰伤害抗性」「穿过水墙的火焰伤害减半」误判为「造成火焰伤害」。
        for clause in re.split(r"[。，；！？\n]", t):
            if any(h in clause for h in _DEFENSE_HINTS):
                continue
            for m in RE_DAMAGE_WORD.finditer(clause):
                canonical = DAMAGE_TYPE_CN.get(m.group(1))
                if canonical and canonical not in out:
                    out.append(canonical)
            # 兜底告警：收集词表未覆盖的「X伤害」上下文词，便于日后补别名。
            for m in RE_ANY_DAMAGE.finditer(clause):
                word = m.group(1)
                if word not in DAMAGE_TYPE_CN and word not in _DAMAGE_WARN_STOPWORDS:
                    _unmatched_damage_words[word] = (
                        _unmatched_damage_words.get(word, 0) + 1
                    )
    return out


def _extract_conditions(texts: list[str]) -> list[str]:
    """从文本集合提取 {@condition} 标签状态（canonical 中文，去重保序）。"""
    out: list[str] = []
    for t in texts:
        for m in RE_CONDITION_TAG.finditer(t):
            name = m.group(1).split("|")[0].strip()
            canonical = CONDITION_CN.get(name)
            if canonical and canonical not in out:
                out.append(canonical)
    return out


def _defense_list(items: object) -> list[str]:
    """伤害免疫/抗性/易伤、状态免疫的结构化中文列表（字符串/dict 变体）。"""
    if isinstance(items, str):
        return [items]
    if not isinstance(items, list) or not items:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in ("immune", "resist", "vulnerable"):
                v = item.get(key)
                if isinstance(v, list):
                    out.extend(str(x) for x in v)
                elif isinstance(v, str):
                    out.append(v)
    return out


_TIME_UNIT_CN = {
    "action": "动作",
    "bonus": "附赠动作",
    "bonus action": "附赠动作",
    "reaction": "反应",
    "minute": "分钟",
    "hour": "小时",
    "day": "天",
    "round": "轮",
    "special": "特殊",
}


def _spell_body(s: dict) -> str:
    parts = []
    meta = []
    meta.append(f"{s.get('level', '?')}环")
    school = s.get("school")
    if school:
        meta.append(f"学派{SCHOOL_CN_REV.get(school, school)}")
    time_info = s.get("time") or []
    if time_info:
        t = time_info[0]
        unit = _TIME_UNIT_CN.get(str(t.get("unit", "")), str(t.get("unit", "")))
        meta.append(f"施法时间{t.get('number', '')}{unit}".strip())
    rng = s.get("range") or {}
    if rng.get("distance"):
        d = rng["distance"]
        if d.get("type") == "feet":
            meta.append(f"距离{d.get('amount', '?')}尺")
        else:
            meta.append(f"距离{d.get('type', '?')}")
    comps = s.get("components") or {}
    cstr = "成分" + "".join(
        p for p, flag in (("言语", comps.get("v")), ("姿势", comps.get("s")), ("材料", comps.get("m"))) if flag
    )
    meta.append(cstr)
    dur = s.get("duration") or []
    if dur:
        d0 = dur[0]
        if d0.get("type") == "instant":
            meta.append("持续时间：立即")
        elif d0.get("type") == "timed" and d0.get("duration"):
            dd = d0["duration"]
            unit = _TIME_UNIT_CN.get(str(dd.get("type", "")), str(dd.get("type", "")))
            meta.append(f"持续时间：{dd.get('amount', '?')}{unit}")
        else:
            meta.append(f"持续时间：{d0.get('type', '?')}")
    if (s.get("meta") or {}).get("ritual"):
        meta.append("仪式")
    parts.append("【法术信息】" + "｜".join(meta))
    body = _flatten_entries(s.get("entries"))
    if body:
        parts.append(body)
    hl = s.get("entriesHigherLevel")
    if hl:
        hl_text = _flatten_entries(hl)
        if hl_text:
            # entriesHigherLevel 的块本身带「升环施法」标题（_flatten_entries 已渲染），
            # 这里不再重复加前缀。
            parts.append(hl_text)
    return "\n\n".join(parts)


_ABIL_ORDER = ["str", "dex", "con", "int", "wis", "cha"]


_ABIL_CN = {
    "str": "力量", "dex": "敏捷", "con": "体质",
    "int": "智力", "wis": "感知", "cha": "魅力",
}


_SKILL_CN = {
    "acrobatics": "特技", "animalHandling": "驯兽", "arcana": "奥秘",
    "athletics": "运动", "deception": "欺瞒", "history": "历史",
    "insight": "洞悉", "intimidation": "威吓", "investigation": "调查",
    "medicine": "医药", "nature": "自然", "perception": "察觉",
    "performance": "表演", "persuasion": "游说", "religion": "宗教",
    "sleightOfHand": "巧手", "stealth": "隐匿", "survival": "生存",
}


_SPEED_CN = {
    "walk": "步行", "fly": "飞行", "swim": "游泳",
    "climb": "攀爬", "burrow": "掘地",
}


def _fmt_speed(speed: object) -> str:
    if isinstance(speed, str):
        return speed
    if isinstance(speed, (int, float)):
        return f"{speed}尺"
    if isinstance(speed, dict):
        parts = []
        for k, v in speed.items():
            if k == "hover":
                if v:
                    parts.append("悬浮")
                continue
            lab = _SPEED_CN.get(k, str(k))
            if isinstance(v, (int, float)):
                parts.append(f"{lab}{v}尺")
            elif isinstance(v, dict):
                num = v.get("number")
                unit = v.get("unit")
                parts.append(f"{lab}{num}{unit or '尺'}" if num is not None else lab)
            elif isinstance(v, str):
                parts.append(f"{lab}{v}")
        return "、".join(p for p in parts if p)
    return ""


def _fmt_abil_scores(m: dict) -> str:
    parts = []
    for k in _ABIL_ORDER:
        v = m.get(k)
        if v is None:
            continue
        mod = (int(v) - 10) // 2
        mod_s = f"+{mod}" if mod >= 0 else str(mod)
        parts.append(f"{_ABIL_CN[k]}{v}({mod_s})")
    return "、".join(parts)


def _fmt_dict_bonuses(data: object, label_map: dict[str, str]) -> str:
    """豁免/技能加值 dict（如 {"con":"+10","arcana":"+19"}）→ 中文文本。"""
    if not isinstance(data, dict) or not data:
        return ""
    parts = []
    for k, v in data.items():
        cn = label_map.get(k, k)
        parts.append(f"{cn}{v}")
    return "、".join(parts)


def _fmt_damage_traits(items: object) -> str:
    """伤害免疫/抗性/易伤：字符串或带条件 dict 混合列表 → 中文文本。

    dict 形如 {"immune": ["钝击"], "note": "来自非魔法攻击"} 或
    {"resist": [...], "preNote": "非魔法的"}、{"special": "法术伤害"}。
    """
    if isinstance(items, str):
        return items
    if not isinstance(items, list) or not items:
        return ""
    parts = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        sub_list: list[str] = []
        for key in ("immune", "resist", "vulnerable"):
            v = item.get(key)
            if isinstance(v, list):
                sub_list.extend(str(x) for x in v)
            elif isinstance(v, str):
                sub_list.append(v)
        special = item.get("special")
        pre = item.get("preNote")
        note = item.get("note")
        if pre and sub_list:
            text = f"{pre}的{'、'.join(sub_list)}"
        elif sub_list:
            text = "、".join(sub_list)
        elif special:
            text = str(special)
        else:
            text = ""
        if note:
            text = f"{text}（{note}）" if text else str(note)
        if text:
            parts.append(text)
    return "、".join(parts)


def _fmt_str_list(items: object) -> str:
    if isinstance(items, str):
        return items
    if not isinstance(items, list):
        return ""
    return "、".join(str(x) for x in items if str(x).strip())


def _lvl_sort_key(lvl: str) -> tuple:
    if lvl == "0":
        return (0, lvl)
    if lvl.isdigit():
        return (int(lvl), lvl)
    return (99, lvl)


def _fmt_spellcasting(sc: dict) -> str:
    """结构化施法（怪物拥有哪些法术）→ 中文段落。"""
    lines = []
    name = sc.get("name")
    ability = sc.get("ability")
    if ability:
        abil_cn = _ABIL_CN.get(str(ability).lower(), str(ability))
        suffix = f"（施法属性：{abil_cn}）"
    else:
        suffix = ""
    if name:
        lines.append(f"{clean_5etools_tags(str(name))}：{suffix}")
    elif suffix:
        lines.append(f"施法：{suffix}")
    header = _flatten_entries(sc.get("headerEntries"))
    if header:
        lines.append(header)
    will = sc.get("will")
    if isinstance(will, list) and will:
        lines.append("随意施展：" + "、".join(
            clean_5etools_tags(str(s)) for s in will
        ))
    spells = sc.get("spells")
    if isinstance(spells, dict):
        for lvl in sorted(spells.keys(), key=_lvl_sort_key):
            group = spells[lvl]
            if not isinstance(group, dict):
                continue
            daily = group.get("daily")
            if isinstance(daily, dict):
                for count, dg in daily.items():
                    names = [
                        clean_5etools_tags(str(s))
                        for s in (dg.get("spells") or []) if s
                    ]
                    if names:
                        lines.append(f"每天{count}次：" + "、".join(names))
                continue
            names = [
                clean_5etools_tags(str(s))
                for s in (group.get("spells") or []) if s
            ]
            if not names:
                continue
            if lvl == "0":
                lines.append("戏法：" + "、".join(names))
            else:
                slots = group.get("slots")
                suffix = f"（{slots}个法术位）" if slots else ""
                lines.append(f"{lvl}环{suffix}：" + "、".join(names))
    footer = _flatten_entries(sc.get("footerEntries"))
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _monster_body(m: dict) -> str:
    parts = []
    meta = []
    size = m.get("size")
    if isinstance(size, list) and size:
        meta.append(f"体型{size[0]}")
    elif size:
        meta.append(f"体型{size}")
    for key, label in (("ac", "AC"), ("hp", "HP")):
        val = m.get(key)
        if val is None:
            continue
        text = _fmt_ac(val) if key == "ac" else _fmt_hp(val)
        if text:
            meta.append(f"{label}{text}")
    speed = m.get("speed")
    speed_text = _fmt_speed(speed)
    if speed_text:
        meta.append(f"速度{speed_text}")
    cr = m.get("cr")
    if isinstance(cr, dict):
        cr = cr.get("cr")
    if cr is not None:
        meta.append(f"挑战等级CR{cr}")
    if meta:
        parts.append("【数据】" + "｜".join(str(x) for x in meta))

    # 属性 / 豁免 / 技能
    abil = _fmt_abil_scores(m)
    if abil:
        parts.append("【属性】" + abil)
    save = _fmt_dict_bonuses(m.get("save"), _ABIL_CN)
    if save:
        parts.append("【豁免】" + save)
    skill = _fmt_dict_bonuses(m.get("skill"), _SKILL_CN)
    if skill:
        parts.append("【技能】" + skill)

    # 防御 / 状态 / 语言 / 感官 / 装备 / 环境
    immune = _fmt_damage_traits(m.get("immune"))
    if immune:
        parts.append("【免疫】" + immune)
    resist = _fmt_damage_traits(m.get("resist"))
    if resist:
        parts.append("【抗性】" + resist)
    vulnerable = _fmt_damage_traits(m.get("vulnerable"))
    if vulnerable:
        parts.append("【易伤】" + vulnerable)
    cond_immune = _fmt_str_list(m.get("conditionImmune"))
    if cond_immune:
        parts.append("【状态免疫】" + cond_immune)
    languages = _fmt_str_list(m.get("languages"))
    if languages:
        parts.append("【语言】" + languages)
    senses = _fmt_str_list(m.get("senses"))
    if m.get("passive") is not None:
        if senses:
            senses += f"、被动察觉{m.get('passive')}"
        else:
            senses = f"被动察觉{m.get('passive')}"
    if senses:
        parts.append("【感官】" + senses)
    gear = m.get("gear")
    if isinstance(gear, list) and gear:
        # gear 形如 ["法球|xphb"]，取「|」前名称
        names = [str(g).split("|", 1)[0].strip() for g in gear if str(g).strip()]
        if names:
            parts.append("【装备】" + "、".join(names))
    environment = _fmt_str_list(m.get("environment"))
    if environment:
        parts.append("【环境】" + environment)

    # 施法（结构化法术列表，1157 个怪物含此字段）
    spellcasting = m.get("spellcasting")
    if isinstance(spellcasting, list) and spellcasting:
        blocks = [_fmt_spellcasting(sc) for sc in spellcasting]
        parts.append("【施法】\n" + "\n\n".join(b for b in blocks if b))

    for key, label in (
        ("trait", "特性"),
        ("action", "动作"),
        ("bonus", "附赠动作"),
        ("reaction", "反应"),
        ("legendary", "传奇动作"),
        ("mythic", "神话动作"),
    ):
        items = m.get(key)
        if not isinstance(items, list) or not items:
            continue
        blocks = [_flatten_entry(i) for i in items]
        parts.append(f"【{label}】\n" + "\n\n".join(b for b in blocks if b))
    return "\n\n".join(parts)


def _item_body(it: dict) -> str:
    parts = []
    rar = it.get("rarity")
    meta = []
    if rar == "none":
        # 基础物品（items-base.json）：非魔法，直接显示不附「稀有度：」前缀
        meta.append("非魔法物品")
    elif rar:
        meta.append(f"稀有度：{format_rarity(rar)}")
    if it.get("reqAttune"):
        meta.append("需要同调")
    itype = it.get("type")
    if itype:
        # 类型码可能带来源后缀（如「M|XPHB」），取码后中文化
        code = str(itype).split("|", 1)[0].strip()
        meta.append(f"类型：{ITEM_TYPE_CODE.get(code, code)}")
    if meta:
        parts.append("【物品信息】" + "｜".join(meta))
    body = _flatten_entries(it.get("entries"))
    if body:
        parts.append(body)
    return "\n\n".join(parts)


_FEAT_ABILITY_CN = {
    "str": "力量", "dex": "敏捷", "con": "体质",
    "int": "智力", "wis": "感知", "cha": "魅力",
}


_FEAT_ARMOR_CN = {"light": "轻甲", "medium": "中甲", "heavy": "重甲", "shield": "盾牌"}


_FEAT_WEAPON_CN = {"simple": "简易武器", "martial": "军用武器"}


_FEAT_CATEGORY_CN = {"D": "龙纹"}


def _prereq_join(values: list[str]) -> str:
    """短项列举连接：1 项原样、2 项「A 或 B」、3+ 项「A、B 或 C」。"""
    if len(values) <= 1:
        return values[0] if values else ""
    if len(values) == 2:
        return f"{values[0]} 或 {values[1]}"
    return "、".join(values[:-1]) + " 或 " + values[-1]


def _prereq_item_cn(d: dict) -> str:
    """单个 prerequisite dict → 中文短语（内部多条件 AND，用「且」连接）。"""
    parts: list[str] = []
    camp = d.get("campaign")
    if camp:
        parts.append(_prereq_join([f"{c}战役" for c in camp]))
    lv = d.get("level")
    if isinstance(lv, int):
        parts.append(f"等级 {lv}+")
    elif isinstance(lv, dict):
        # 2024 嵌套形态：{"level": 1, "class": {"name": "术士", ...}}
        cls = lv.get("class")
        cls_name = cls.get("name") if isinstance(cls, dict) else ""
        parts.append(f"{cls_name or '任意职业'} {lv.get('level', '?')} 级")
    ab = d.get("ability")
    if ab:
        parts.append(_prereq_join([
            f"{_FEAT_ABILITY_CN.get(k, k)} {v}"
            for a in ab if isinstance(a, dict) for k, v in a.items()
        ]))
    prof = d.get("proficiency")
    if prof:
        subs: list[str] = []
        for p in prof:
            if not isinstance(p, dict):
                subs.append(str(p))
                continue
            for k, v in p.items():
                if k == "armor":
                    subs.append(f"{_FEAT_ARMOR_CN.get(v, v)}熟练")
                elif k == "weapon":
                    subs.append(f"{_FEAT_WEAPON_CN.get(v, v)}熟练")
                elif k == "weaponGroup":
                    subs.append(f"{_FEAT_WEAPON_CN.get(v, v)}组熟练")
                else:
                    subs.append(f"{k}:{v}")
        parts.append(_prereq_join(subs))
    ft = d.get("feat")
    if ft:
        names: list[str] = []
        for s in ft:
            seg = str(s).split("|")
            names.append(seg[2] if len(seg) >= 3 else seg[0])
        parts.append("前置专长：" + _prereq_join(names))
    feature = d.get("feature")
    if feature:
        parts.append("特性：" + _prereq_join([str(x) for x in feature]))
    races = d.get("race")
    if races:
        names = [r.get("name") for r in races if isinstance(r, dict) and r.get("name")]
        if names:
            parts.append("种族：" + _prereq_join(names))
    bgs = d.get("background")
    if bgs:
        names = [b.get("name") for b in bgs if isinstance(b, dict) and b.get("name")]
        if names:
            parts.append("背景：" + _prereq_join(names))
    if d.get("spellcasting") or d.get("spellcasting2020"):
        parts.append("施法能力")
    if d.get("spellcastingFeature"):
        parts.append("施法特性")
    fc = d.get("featCategory")
    if fc:
        parts.append("专长类别：" + _prereq_join([_FEAT_CATEGORY_CN.get(c, c) for c in fc]))
    ec = d.get("exclusiveFeatCategory")
    if ec:
        parts.append("互斥专长类别：" + _prereq_join(
            [_FEAT_CATEGORY_CN.get(c, c) for c in ec]
        ))
    other = d.get("other")
    if other:
        parts.append(str(other))
    os_ = d.get("otherSummary")
    if isinstance(os_, dict) and os_.get("entry"):
        parts.append(str(os_["entry"]))
    return " 且 ".join(parts)


def _feat_prereq_cn(prereq: list) -> str:
    """专长前置条件结构化数据 → 可读中文。

    - 数组元素间为 OR（满足任一即可），无公共等级时用「；或」连接；
    - 所有元素共享同一 level 时提为公共前缀（「等级 4+，且 A 或 B」）。
    """
    items = [d for d in prereq if isinstance(d, dict)]
    if not items:
        return ""
    lv = items[0].get("level")
    if isinstance(lv, int) and all(d.get("level") == lv for d in items):
        rest = [{k: v for k, v in d.items() if k != "level"} for d in items]
        diff = _prereq_join([_prereq_item_cn(r) for r in rest])
        return f"等级 {lv}+，且 {diff}" if diff else f"等级 {lv}+"
    return "；或 ".join(_prereq_item_cn(d) for d in items)


def _feat_body(f: dict) -> str:
    parts = []
    prereq = f.get("prerequisite")
    if prereq:
        parts.append(f"前置条件：{_feat_prereq_cn(prereq)}")
    body = _flatten_entries(f.get("entries"))
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def _background_body(bg: dict) -> str:
    return _flatten_entries(bg.get("entries"))


def _condition_body(c: dict) -> str:
    return _flatten_entries(c.get("entries"))


_RACE_SPEED_CN: dict[str, str] = {
    "walk": "步行", "climb": "攀爬", "swim": "游泳",
    "fly": "飞行", "burrow": "掘穴",
}


_DMG_WORDS = "|".join(
    sorted(DAMAGE_TYPE_CN, key=len, reverse=True)
)


RE_RACE_DEFENSE = re.compile(
    rf"对(?P<d1>[^，。；]{{1,8}}?伤害)(?:和(?P<d2>[^，。；]{{1,8}}?伤害))*(?:具有)?"
    rf"(?P<sem>抗性|免疫|易伤)"
)


RE_SPELL_TAG = re.compile(r"\{@spell ([^}|]+)")


def _collect_spell_names(node: object) -> set[str]:
    """递归收集 additionalSpells 结构化块中的法术名（| 去 source、# 去标注）。"""
    out: set[str] = set()
    if isinstance(node, list):
        for s in node:
            if isinstance(s, str):
                name = s.split("|", 1)[0].split("#", 1)[0].strip()
                if name:
                    out.add(name)
    elif isinstance(node, dict):
        for v in node.values():
            out |= _collect_spell_names(v)
    return out


def _race_innate_spells(r: dict) -> set[str]:
    """种族天生施法：2024 additionalSpells（innate+known）+ 2014 正文 {@spell} 标签。"""
    spells: set[str] = set()
    for blk in r.get("additionalSpells") or []:
        if not isinstance(blk, dict):
            continue
        for section in ("innate", "known"):
            spells |= _collect_spell_names(blk.get(section))
    txt = json.dumps(r.get("entries") or [], ensure_ascii=False)
    for m in RE_SPELL_TAG.finditer(txt):
        spells.add(m.group(1).strip())
    return spells


def _race_structured_defense(r: dict) -> list[tuple[str, str]]:
    """2024 结构化抗性/免疫/易伤（resist/immune/vulnerable），choose 变体展开候选。"""
    tags: list[tuple[str, str]] = []
    for field, facet in (
        ("resist", "dmg_resist"),
        ("immune", "dmg_immune"),
        ("vulnerable", "dmg_vuln"),
    ):
        val = r.get(field)
        if not isinstance(val, list):
            continue
        for item in val:
            if isinstance(item, str):
                tags.append((facet, item))
            elif isinstance(item, dict) and isinstance(item.get("choose"), dict):
                for cand in item["choose"].get("from") or []:
                    if isinstance(cand, str):
                        tags.append((facet, cand))
    return tags


def _race_defense_tags(r: dict) -> list[tuple[str, str]]:
    """种族抗性双通道：2024 结构化 + 2014 正文「对X伤害具有抗性/免疫/易伤」。"""
    tags = _race_structured_defense(r)
    txt = json.dumps(r.get("entries") or [], ensure_ascii=False)
    for m in RE_RACE_DEFENSE.finditer(txt):
        sem = m.group("sem")
        facet = {"抗性": "dmg_resist", "免疫": "dmg_immune", "易伤": "dmg_vuln"}[sem]
        for raw in (m.group("d1"), m.group("d2")):
            if not raw:
                continue  # d2 为可选组（无「和」连接时未捕获）
            dmg = raw.rstrip("伤害")
            if dmg and dmg in DAMAGE_TYPE_CN:
                tags.append((facet, DAMAGE_TYPE_CN[dmg]))
    return tags


def _race_speed_map(r: dict) -> dict[str, int]:
    """速度字段 → {英文键: 尺}。int=步行；dict 值 int 或 True(=步行速度)。"""
    spd = r.get("speed")
    if isinstance(spd, int):
        return {"walk": spd}
    if not isinstance(spd, dict):
        return {}
    walk = spd.get("walk")
    out: dict[str, int] = {}
    if isinstance(walk, int):
        out["walk"] = walk
    for key in ("climb", "swim", "fly", "burrow"):
        v = spd.get(key)
        if v is True:
            if isinstance(walk, int):
                out[key] = walk
        elif isinstance(v, int):
            out[key] = v
    return out


def _race_speed_cols(r: dict) -> tuple:
    """races 表 6 数值列（speed_walk..speed_burrow, darkvision）。"""
    spd = _race_speed_map(r)
    dv = r.get("darkvision")
    return (
        spd.get("walk"), spd.get("climb"), spd.get("swim"),
        spd.get("fly"), spd.get("burrow"),
        dv if isinstance(dv, int) else None,
    )


def _race_body(r: dict) -> str:
    parts = []
    meta = []
    size = r.get("size")
    if isinstance(size, list) and size:
        cn = [SIZE_CN_REV.get(s, s) for s in size]
        meta.append("体型：" + "或".join(cn))
    spd = _race_speed_map(r)
    if spd:
        meta.append(
            "速度：" + "、".join(
                f"{_RACE_SPEED_CN.get(k, k)}{v}尺" for k, v in spd.items()
            )
        )
    dv = r.get("darkvision")
    if isinstance(dv, int):
        meta.append(f"黑暗视觉：{dv}尺")
    cts = r.get("creatureTypes")
    if isinstance(cts, list) and cts:
        meta.append("生物类型：" + "、".join(str(c) for c in cts))
    else:
        meta.append("生物类型：类人生物")
    tags = _race_defense_tags(r)
    for facet, label in (
        ("dmg_resist", "抗性"), ("dmg_immune", "免疫"), ("dmg_vuln", "易伤"),
    ):
        dmg = sorted({v for f, v in tags if f == facet})
        if dmg:
            meta.append(f"{label}：" + "、".join(dmg))
    spells = _race_innate_spells(r)
    if spells:
        meta.append("天生施法：" + "、".join(sorted(spells)))
    if meta:
        parts.append("【种族信息】" + "｜".join(meta))
    body = _flatten_entries(r.get("entries"))
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def _kind_body(kind: str, entry: dict) -> str:
    renderer = {
        "spell": _spell_body,
        "monster": _monster_body,
        "item": _item_body,
        "feat": _feat_body,
        "background": _background_body,
        "condition": _condition_body,
        "race": _race_body,
    }[kind]
    return renderer(entry)


def is_machine_entry(entry: dict) -> int:
    """与 5etools-cn 渲染规则一致：无 translator 或非人工白名单 → 机翻。"""
    translator = entry.get("translator")
    return 0 if translator and translator in HUMAN_TRANSLATORS else 1


def _parse_cr(value: object) -> float | None:
    if isinstance(value, dict):
        value = value.get("cr")
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("", "unknown", "varies", "—"):
        return None
    if "/" in s:
        try:
            a, b = s.split("/", 1)
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _monster_type(m: dict) -> str:
    t = m.get("type")
    if isinstance(t, dict):
        return str(t.get("type") or "")
    return str(t or "")


def _fmt_ac(ac: object) -> str:
    """AC 字段可能是 int / [12] / [{"ac":19,"from":[...]}]，统一转文本。"""
    if isinstance(ac, (int, float, str)):
        return str(ac)
    if isinstance(ac, dict):
        return str(ac.get("ac") or "")
    if isinstance(ac, list) and ac:
        parts = []
        for item in ac:
            if isinstance(item, dict):
                v = item.get("ac")
                src = item.get("from")
                text = str(v) if v is not None else ""
                if isinstance(src, list) and src:
                    text += f"({'、'.join(str(x) for x in src)})"
                parts.append(text)
            else:
                parts.append(str(item))
        return "、".join(p for p in parts if p)
    return ""


def _fmt_hp(hp: object) -> str:
    """HP 字段是 {"average":68,"formula":"8d10+24"} 之类的 dict，统一转文本。"""
    if isinstance(hp, (int, float, str)):
        return str(hp)
    if isinstance(hp, dict):
        avg = hp.get("average")
        formula = hp.get("formula")
        if avg is not None and formula:
            return f"{avg}（{formula}）"
        if avg is not None:
            return str(avg)
        if formula:
            return str(formula)
    return ""


def _ability_payload(entry: dict) -> str | None:
    """ability 数组（原始 JSON）→ 文本；无结构化 ability 返回 None（不插行）。

    结构（已验证）：
    - 2014 种族：[{ "str": 2 }, { "cha": 2, "choose": {"from": [...], "count": 2} }]
      —— 固定加值与 choose 合并于同一 dict；TCE 定制血统用 amount 而非 count。
    - 2024 背景：[{ "choose": {"weighted": {"from": [...], "weights": [2, 1]}} }, ...]
      —— 多个方案并存，玩家二选一。
    """
    ab = entry.get("ability")
    if not isinstance(ab, list) or not ab:
        return None
    return json.dumps(ab, ensure_ascii=False, separators=(",", ":"))


def _item_combat_cols(
    entry: dict,
) -> tuple[int | None, str, int | None, int | None, str, str, str] | None:
    """护甲/武器战斗字段 → (ac, armor_type, strength, stealth, dmg1, properties, range)。

    仅对携带战斗字段的条目返回；纯装饰/魔法物件（无 ac 且无 dmg1）返回 None 不插行。
    type/property 为码表含 "|source" 后缀（如 "LA|XPHB"、"F|XPHB"），取 "|" 前主码。
    """
    ac = entry.get("ac")
    if isinstance(ac, dict):
        ac = ac.get("ac")  # 条件性 AC（如半身板甲）取基础数值，条件文本不入表
    armor_type = str(entry.get("type") or "").partition("|")[0]
    strength = entry.get("strength")
    if isinstance(strength, str) and strength.isdigit():
        strength = int(strength)
    stealth = 1 if entry.get("stealth") else None
    dmg1 = str(entry.get("dmg1") or "")
    props = entry.get("property")
    if isinstance(props, list):
        prop_codes = [str(p).partition("|")[0] for p in props if isinstance(p, str)]
    elif isinstance(props, str):
        prop_codes = [props.partition("|")[0]]
    else:
        prop_codes = []
    range_note = entry.get("range")
    if isinstance(range_note, dict):
        parts = []
        if range_note.get("normal"):
            parts.append(f"近{range_note['normal']}")
        if range_note.get("long"):
            parts.append(f"远{range_note['long']}")
        range_note = "/".join(parts)
    if ac is None and not dmg1:
        return None
    return (
        int(ac) if ac is not None else None,
        armor_type,
        int(strength) if strength is not None else None,
        stealth,
        dmg1,
        ",".join(prop_codes),
        str(range_note or ""),
    )


def _item_value_weight(entry: dict) -> tuple[int | None, float | None]:
    """物品价值/重量 → (value_cp, weight_lb)。

    5etools 源数据 value=铜币整数（1金币=10银币=100铜币）、weight=磅（可含小数）。
    缺失/非法值返回 None；value 容忍字符串数字（个别条目带引号）。
    """
    value_cp: int | None = None
    raw_v = entry.get("value")
    if isinstance(raw_v, str):
        raw_v = raw_v.strip().replace(",", "")
    if isinstance(raw_v, (int, float)) and not isinstance(raw_v, bool):
        v = int(raw_v)
        value_cp = v if v >= 0 else None
    elif isinstance(raw_v, str) and raw_v.lstrip("+-").isdigit():
        value_cp = max(int(raw_v), 0)
    weight_lb: float | None = None
    raw_w = entry.get("weight")
    if isinstance(raw_w, str):
        raw_w = raw_w.strip()
    if isinstance(raw_w, (int, float)) and not isinstance(raw_w, bool):
        w = float(raw_w)
        weight_lb = w if w >= 0 else None
    elif isinstance(raw_w, str) and raw_w.replace(".", "", 1).lstrip("+-").isdigit():
        weight_lb = max(float(raw_w), 0.0)
    return value_cp, weight_lb

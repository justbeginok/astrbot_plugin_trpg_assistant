"""文本角色卡导入解析器（v0.19.0）。

纯函数模块（不依赖 AstrBot），把玩家贴出的「文本角色卡」宽松解析为
CharacterSheet，供 /卡 导入、/车卡 导入 命令落库。设计目标：
- 兼容插件自身 format_sheet() 的输出（round-trip）；
- 兼容 LLM 生成的宽松 key:value / key value 格式；
- 「宁缺毋滥」：识别不到的字段给默认值并记 notes，唯一硬失败是
  有效字段 < 2（防止把无关文本误落库成空卡）。

战斗字段（HP/AC/法术位/攻击）一律忽略不解析：format_sheet 把
base+bonus 渲染成合计值无法无损拆回，且规则引擎 recalc_base 会按
职业与装备确定性重算，读文本反而引入陈旧值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .character import (
    ABILITY_ALIAS,
    ABILITY_CN,
    ABILITY_NAMES,
    SKILL_ALIAS,
    AbilityScores,
    CharacterSheet,
    ClassLevel,
    EquipmentSlots,
    normalize_edition,
    parse_classes_text,
    parse_spells_text,
)

# 名字行：📜 **阿尔文**（2014） / 📜 阿尔文（2014） / **阿尔文** / 宽松键 名字/姓名/名称/角色名
# v0.42.0：兼容无 ** 的纯文本头（format_sheet 已去 markdown，round-trip 可解析）。
_HEADER_RE = re.compile(
    r"^\s*(?:(?:📜?\s*\*\*([^*]+)\*\*)|(?:📜\s*([^*：（）()]+?)))"
    r"\s*(?:[（(]([^）)]*)[）)])?\s*$"
)
_NAME_KEY_RE = re.compile(
    r"^(?:人物姓名|名字|姓名|名称|角色名|角色卡名|name|character)\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
)

# 版本行：版本：2024 / edition: 5.5e
_VERSION_KEY_RE = re.compile(
    r"^(?:版本|edition|rulebook)\s*[:：]?\s*(.+)$", re.IGNORECASE
)

# 属性段：力量 15（+2）/ 力量15 / 力量: 15 / str 15 / 力15 / 敏14（+1）
_ABIL_SEG_RE = re.compile(
    r"^\s*([力敏体智感魅]|力量|敏捷|体质|智力|感知|魅力|str|dex|con|int|wis|cha)"
    r"\s*[:：=]?\s*(\d{1,3})\s*(?:[（(]\s*[+-]?\d+\s*[）)])?\s*$",
    re.IGNORECASE,
)
_ABILITY_LINE_RE = re.compile(r"^属性值\s*[:：]?\s*(.*)$", re.IGNORECASE)

# 职业段：战士（勇士） 3 / 法师 2 / 战士 / 战士 3
# v0.30.0：(?!特性) 排除「职业特性：…」（由知识库自动带出，不落卡）
# v0.41.0：段解析复用 character.parse_classes_text（单一事实来源）
_CLASS_LINE_RE = re.compile(
    r"^(?:职业|class|classes)(?!特性)\s*[:：]?\s*(.+)$", re.IGNORECASE
)

# 熟练行：熟练：豁免 力量、体质　技能 运动、体操 / 豁免熟练：力量 / 技能：运动
# 工具/武器/防具行同构（v0.21）；「万事通 +N」尾部信息无类别头，解析时静默忽略
# v0.30.0：兼容「熟练项：」键（玩家纯文本卡常见写法）；(?!加值) 排除「熟练加值：」行
_PROF_LINE_RE = re.compile(r"^(?:熟练(?!加值)|熟练项)\s*[:：]?\s*(.*)$")
_PROF_HEAD_RE = re.compile(
    r"^(豁免|技能|工具|武器|防具|save|skill|tool|weapon|armor)(?:熟练)?\s*[:：]?\s*",
    re.IGNORECASE,
)
# 无锚版本：仅供 finditer 切熟练块（^ 锚会让 finditer 只命中首处）。
# 前导断言要求类别头前是行首或分隔符，避免误匹配「简易武器」「盗贼工具」等词内子串。
_PROF_HEAD_FIND_RE = re.compile(
    r"(?:(?<=^)|(?<=[　、,，;；\s]))(豁免|技能|工具|武器|防具|save|skill|tool|weapon|armor)"
    r"(?:熟练)?\s*[:：]?\s*",
    re.IGNORECASE,
)

# 专精行（v0.21）：专精：隐匿、察觉（★技能双倍熟练）
_EXPERTISE_LINE_RE = re.compile(r"^专精\s*[:：]?\s*(.*)$")
# 专长行（v0.21）：专长：巨武器大师、幸运
_FEATS_LINE_RE = re.compile(r"^专长\s*[:：]?\s*(.*)$")
# 语言行（v0.28.0）：语言：通用语、精灵语（独立行，多门按顿号/逗号/空格分隔）
_LANG_LINE_RE = re.compile(r"^语言\s*[:：]?\s*(.*)$")

# 装备：装备：主手 长剑　副手 木盾 / 主手：长剑 / 护甲 皮甲
_EQUIP_LINE_RE = re.compile(r"^装备\s*[:：]?\s*(.*)$")
_EQUIP_SEG_RE = re.compile(
    r"^(主手|副手|护甲|main|off|armor)\s*[:：]?\s*(.+)$", re.IGNORECASE
)

# 生平：生平：… / 背景故事：X / backstory: X / 人物描述：…（v0.30.0）
_BACKSTORY_RE = re.compile(
    r"^(?:背景故事|生平|简介|人物描述|人物简介|backstory|story)\s*[:：]?\s*(.*)$",
    re.IGNORECASE,
)

# 子职/等级独立行（v0.30.0）：作用于最近一个职业条目
# 「职业：术士 / 子职：狂野术法 / 等级：4」独立行写法
_SUBCLASS_LINE_RE = re.compile(
    r"^(?:子职|subclass)\s*[:：]?\s*(.+)$", re.IGNORECASE
)
_LEVEL_LINE_RE = re.compile(
    r"^(?:等级|级别|level)\s*[:：]?\s*(\d{1,2})\s*$", re.IGNORECASE
)

# 人物基础信息行（v0.30.0）：信仰/年龄/性别/身高/体重（自由文本）
_INFO_KEY_RE = re.compile(
    r"^(信仰|deity|god|年龄|age|性别|gender|sex|身高|height|体重|weight)\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
)

# format_sheet 人物复合行（v0.30.0）：「人物：性别 女　年龄 14　信仰 无」，
# 按段切后每段走 _INFO_KEY_RE（round-trip 兼容）
_PERSON_LINE_RE = re.compile(
    r"^(?:人物|person|profile)\s*[:：]?\s*(.*)$", re.IGNORECASE
)

# 资源行（v0.30.0）：短休已用生命骰 0/4 / 激励 1/1（取分子）
_RESOURCE_LINE_RE = re.compile(
    r"^(短休已用生命骰|已用生命骰|短休生命骰|激励|inspiration|hit\s*dice\s*used)\s*[:：]?\s*"
    r"(\d+)\s*[/／]\s*(\d+)\s*$",
    re.IGNORECASE,
)

# 先攻行（v0.30.0）：先攻：+3 / 先攻 3（导入时忽略，由规则引擎按敏捷修正重算，
# 房规额外加成用 /卡 设 先攻 <加值> 补充；此处仅识别防误吞）
_INIT_LINE_RE = re.compile(
    r"^(先攻|initiative|init)\s*[:：]?\s*[+-]?\d{1,3}\s*$", re.IGNORECASE
)

# 生命骰行（v0.30.0）：生命骰：D6 / hit dice: d8（面值由职业派生，仅识别忽略）
_HD_LINE_RE = re.compile(
    r"^(生命骰|hit\s*dice|hd)\s*[:：]?\s*D?\d{1,2}\s*$", re.IGNORECASE
)

# 已知法术行（v0.30.0）：两种形态
# ① 整段：「已知法术：戏法：A，B　一环：C，D」（正文可空，后面跟独立环阶行）
_SPELLS_BLOCK_RE = re.compile(
    r"^(?:已知法术|法术列表|法术|spells|spell\s*list)\s*[:：]?\s*(.*)$",
    re.IGNORECASE,
)
# ② 独立环阶行：「戏法：A，B」「一环：C」「1环：D」「cantrip: E」
_SPELL_GROUP_LINE_RE = re.compile(
    r"^(戏法|cantrip|[0-9]{1,2}\s*环?|[一二三四五六七八九]\s*环)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)

# 熟练占位词（v0.30.0）：工具/武器/防具熟练为自由文本，「护甲：无」等占位
# 词不应入库（豁免/技能走词表天然被忽略，此处只过滤自由文本类别）。
_PLACEHOLDER_WORDS: frozenset[str] = frozenset({
    "无", "无熟练", "无防具", "无武器", "无工具", "—", "-", "－",
})

# 种族/背景/阵营：种族 半精灵　背景 士兵　阵营 守序善良 / 种族：半精灵 / race: X
# v0.30.0：(?!特性) 排除「种族特性：…」（由知识库自动带出，不落卡）
_DETAIL_SEG_RE = re.compile(
    r"^(种族(?!特性)|背景|阵营|race|background|alignment|species|origin)\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
)

# 战斗字段行（忽略识别）：HP 12 / AC 15 / 法术位 1环:2 / 攻击 长剑:5
_COMBAT_LINE_RE = re.compile(
    r"^(hp|ac|法术位|攻击|spell\s*slots|attack)\b", re.IGNORECASE
)

# 段分隔符：全角空格 / 顿号 / 逗号（半角空格保留为段内键值分隔）
_SEG_SPLIT_RE = re.compile(r"[　、,，;；]+")
# 段内再拆（宽松格式可能用半角空格分隔多组键值）
_WS_SPLIT_RE = re.compile(r"\s+")


def _detect_edition(text: str, header_edition: str | None) -> str:
    """版本识别优先级：名字行括号 → 版本键行 → 全文关键词 → 默认 2014。

    归一规则复用 character.normalize_edition（v0.41.0）。
    """
    if header_edition:
        e = normalize_edition(header_edition)
        if e:
            return e
    for line in text.splitlines():
        m = _VERSION_KEY_RE.match(line.strip())
        if m:
            e = normalize_edition(m.group(1))
            if e:
                return e
    if re.search(r"5\.5e|5\.5|5r", text, re.IGNORECASE) or re.search(
        r"\b2024\b", text
    ):
        return "2024"
    if re.search(r"\b2014\b", text):
        return "2014"
    if re.search(r"5e|5\.0", text, re.IGNORECASE):
        return "2014"
    return "2014"


def _parse_ability_segments(text: str) -> list[tuple[str, int]]:
    """把一段文本解析为 [(属性缩写, 数值)] 列表；失败返回空列表。

    兼容「力量 15（+2）」「力量15」「str 15」「力量 15 敏捷 14」等。
    分段优先级：整段 → 全角/顿号/逗号切段 → 段内半角空白再拆。
    """
    out: list[tuple[str, int]] = []
    seg = text.strip()
    if not seg:
        return out
    m = _ABIL_SEG_RE.match(seg)
    if m:
        out.append((ABILITY_ALIAS[m.group(1).lower()], int(m.group(2))))
        return out
    # 按全角空格/顿号/逗号切段，保留「力量 15（+2）」为完整段
    for part in _SEG_SPLIT_RE.split(seg):
        p = part.strip()
        if not p:
            continue
        m = _ABIL_SEG_RE.match(p)
        if m:
            out.append((ABILITY_ALIAS[m.group(1).lower()], int(m.group(2))))
            continue
        # 段内还有半角空格分隔的多组（宽松混排「力量 15 敏捷 14」）
        for sub in _WS_SPLIT_RE.split(p):
            m = _ABIL_SEG_RE.match(sub)
            if m:
                out.append((ABILITY_ALIAS[m.group(1).lower()], int(m.group(2))))
    return out


@dataclass
class ImportResult:
    """文本卡解析结果。"""

    sheet: CharacterSheet
    notes: list[str] = field(default_factory=list)


def parse_card_text(text: str) -> ImportResult:
    """把文本角色卡宽松解析为 ImportResult；无法识别时抛 ValueError。"""
    if not text or not text.strip():
        raise ValueError("卡文本为空。")
    text = text.strip()
    lines = text.splitlines()

    # ---- 名字与版本（先扫全文本提取 header） ----
    name: str | None = None
    header_edition: str | None = None
    for line in lines:
        m = _HEADER_RE.match(line.strip())
        if m:
            name = (m.group(1) or m.group(2) or "").strip()
            header_edition = m.group(3).strip() if m.group(3) else None
            break
    edition = _detect_edition(text, header_edition)

    scores: dict[str, int] = {}
    classes: list[ClassLevel] = []
    race = background = alignment = backstory = ""
    deity = age = gender = height = weight = ""
    hit_dice_used = 0
    inspiration = 0
    spells: dict[str, list[str]] = {}
    saves: set[str] = set()
    skills: set[str] = set()
    expertise: set[str] = set()
    feats: list[str] = []
    tools: set[str] = set()
    weapons: set[str] = set()
    armors: set[str] = set()
    languages: set[str] = set()
    eq_main = eq_off = eq_armor = ""
    notes: list[str] = []
    combat_seen = False
    seen: set[str] = set()  # 已识别字段类型计数用

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # 1) 名字键行（header 已处理，此处补宽松键）
        if name is None:
            m = _NAME_KEY_RE.match(line)
            if m:
                name = m.group(1).strip()
                seen.add("name")
                continue

        # 2) 生平（背景故事 优先于 背景 键，防误吞）
        m = _BACKSTORY_RE.match(line)
        if m:
            backstory = m.group(1).strip()
            seen.add("backstory")
            continue

        # 3) 职业行
        m = _CLASS_LINE_RE.match(line)
        if m:
            classes.extend(parse_classes_text(m.group(1)))
            seen.add("class")
            continue

        # 3.5) 子职/等级独立行（v0.30.0：作用于最近一个职业条目）
        m = _SUBCLASS_LINE_RE.match(line)
        if m:
            if classes:
                classes[-1].subclass = m.group(1).strip()
                seen.add("class")
            continue
        m = _LEVEL_LINE_RE.match(line)
        if m:
            if classes:
                try:
                    classes[-1].level = max(1, min(20, int(m.group(1))))
                except ValueError:
                    pass
                seen.add("class")
            continue

        # 4) 属性行（属性值 前缀，或整行就是一个属性段）
        m = _ABILITY_LINE_RE.match(line)
        if m:
            body = m.group(1)
            for ab, val in _parse_ability_segments(body):
                _record_ability(scores, notes, ab, val)
            seen.add("ability")
            continue
        if _parse_ability_segments(line):
            for ab, val in _parse_ability_segments(line):
                _record_ability(scores, notes, ab, val)
            seen.add("ability")
            continue

        # 5) 熟练行
        m = _PROF_LINE_RE.match(line)
        if m:
            for block in _split_prof_blocks(m.group(1)):
                _parse_prof_seg(block, saves, skills, tools, weapons, armors)
            seen.add("prof")
            continue
        if _PROF_HEAD_RE.match(line):
            for block in _split_prof_blocks(line):
                _parse_prof_seg(block, saves, skills, tools, weapons, armors)
            seen.add("prof")
            continue

        # 5.5) 专精 / 专长行（v0.21）
        m = _EXPERTISE_LINE_RE.match(line)
        if m:
            for seg in _SEG_SPLIT_RE.split(m.group(1)):
                item = re.sub(r"[（(].*?[）)]", "", seg).strip().rstrip("★")
                if not item:
                    continue
                sk = SKILL_ALIAS.get(item.lower())
                if sk:
                    expertise.add(sk)
            seen.add("prof")
            continue
        m = _FEATS_LINE_RE.match(line)
        if m:
            for item in re.split(r"[、,，;；]+", m.group(1)):
                item = item.strip()
                if item and item not in _PLACEHOLDER_WORDS and item not in feats:
                    feats.append(item)
            seen.add("feats")
            continue

        # 5.6) 语言行（v0.28.0，独立行，多门语言）
        m = _LANG_LINE_RE.match(line)
        if m:
            for item in re.split(r"[、,，;；\s]+", m.group(1)):
                item = item.strip()
                if item and item not in _PLACEHOLDER_WORDS:
                    languages.add(item)
            seen.add("languages")
            continue

        # 5.7) 已知法术行（v0.30.0：整段 + 独立环阶行两种形态）
        m = _SPELLS_BLOCK_RE.match(line)
        if m:
            spells.update(parse_spells_text(m.group(1)))
            seen.add("spells")
            continue
        m = _SPELL_GROUP_LINE_RE.match(line)
        if m:
            spells.update(parse_spells_text(line))
            seen.add("spells")
            continue

        # 6) 装备行
        m = _EQUIP_LINE_RE.match(line)
        if m:
            for seg in _SEG_SPLIT_RE.split(m.group(1)):
                em, ef, ea = _parse_equip_seg(seg)
                if em:
                    eq_main = em
                if ef:
                    eq_off = ef
                if ea:
                    eq_armor = ea
            seen.add("equip")
            continue
        m = _EQUIP_SEG_RE.match(line)
        if m:
            em, ef, ea = _parse_equip_seg(line)
            if em:
                eq_main = em
            if ef:
                eq_off = ef
            if ea:
                eq_armor = ea
            seen.add("equip")
            continue

        # 7) 种族/背景/阵营 detail 行（可能一行多段，按段切）
        if _DETAIL_SEG_RE.match(line.split("　")[0]) or _DETAIL_SEG_RE.match(line):
            matched_detail = False
            for seg in _SEG_SPLIT_RE.split(line):
                dm = _DETAIL_SEG_RE.match(seg.strip())
                if not dm:
                    continue
                matched_detail = True
                key = dm.group(1).lower()
                val = dm.group(2).strip()
                if not val:
                    continue
                if key in ("种族", "race", "species", "origin"):
                    race = val
                    seen.add("race")
                elif key in ("背景", "background"):
                    background = val
                    seen.add("background")
                elif key in ("阵营", "alignment"):
                    alignment = val
                    seen.add("alignment")
            if matched_detail:
                continue

        # 8) 人物基础信息 / 资源 / 先攻 / 生命骰（v0.30.0）
        m = _PERSON_LINE_RE.match(line)
        if m:
            for seg in _SEG_SPLIT_RE.split(m.group(1)):
                dm = _INFO_KEY_RE.match(seg.strip())
                if not dm:
                    continue
                key = dm.group(1).lower()
                val = dm.group(2).strip()
                if not val:
                    continue
                if key in ("信仰", "deity", "god"):
                    deity = val
                elif key in ("年龄", "age"):
                    age = val
                elif key in ("性别", "gender", "sex"):
                    gender = val
                elif key in ("身高", "height"):
                    height = val
                elif key in ("体重", "weight"):
                    weight = val
            seen.add("info")
            continue
        m = _INFO_KEY_RE.match(line)
        if m:
            key = m.group(1).lower()
            val = m.group(2).strip()
            if not val:
                continue
            if key in ("信仰", "deity", "god"):
                deity = val
            elif key in ("年龄", "age"):
                age = val
            elif key in ("性别", "gender", "sex"):
                gender = val
            elif key in ("身高", "height"):
                height = val
            elif key in ("体重", "weight"):
                weight = val
            seen.add("info")
            continue
        m = _RESOURCE_LINE_RE.match(line)
        if m:
            key = m.group(1).lower()
            num = int(m.group(2))
            if key in ("激励", "inspiration"):
                inspiration = 1 if num > 0 else 0
            else:
                hit_dice_used = max(0, min(20, num))
            seen.add("resource")
            continue
        if _INIT_LINE_RE.match(line) or _HD_LINE_RE.match(line):
            # 先攻/生命骰由规则引擎按职业与敏捷修正重算，导入不落值
            continue

        # 9) 战斗字段行：忽略（记一次 note）
        if _COMBAT_LINE_RE.match(line):
            combat_seen = True
            continue

        # 10) 未知行：静默忽略（宁缺毋滥）

    # ---- 组装 CharacterSheet ----
    ability_scores = AbilityScores(
        strength=scores.get("str", 10),
        dexterity=scores.get("dex", 10),
        constitution=scores.get("con", 10),
        intelligence=scores.get("int", 10),
        wisdom=scores.get("wis", 10),
        charisma=scores.get("cha", 10),
    )
    sheet = CharacterSheet(
        name=name or "未知冒险者",
        edition=edition,
        classes=classes,
        race=race,
        background=background,
        alignment=alignment,
        ability_scores=ability_scores,
        skill_proficiencies=skills,
        save_proficiencies=saves,
        skill_expertise=expertise,
        feats=feats,
        tool_proficiencies=tools,
        weapon_proficiencies=weapons,
        armor_proficiencies=armors,
        languages=languages,
        deity=deity,
        age=age,
        gender=gender,
        height=height,
        weight=weight,
        hit_dice_used=hit_dice_used,
        inspiration=inspiration,
        spells=spells,
        equipment=EquipmentSlots(main_hand=eq_main, off_hand=eq_off, armor=eq_armor),
        backstory=backstory,
    )

    # ---- 最低有效字段判定 ----
    effective = 0
    if name and name != "未知冒险者":
        effective += 1
    if classes:
        effective += 1
    if scores:
        effective += 1
    if race:
        effective += 1
    if background:
        effective += 1
    if alignment:
        effective += 1
    if saves or skills or expertise or tools or weapons or armors or feats or languages:
        effective += 1
    if eq_main or eq_off or eq_armor:
        effective += 1
    if backstory:
        effective += 1
    if deity or age or gender or height or weight:
        effective += 1  # v0.30.0：人物基础信息
    if spells:
        effective += 1  # v0.30.0：已知法术
    if hit_dice_used or inspiration:
        effective += 1  # v0.30.0：资源字段
    if effective < 2:
        raise ValueError(
            "未识别到足够的角色卡内容（至少需要卡名+职业、卡名+属性等两个字段）。"
            "支持格式示例：\n"
            "📜 阿尔文（2014）\n"
            "职业：战士（勇士） 3\n"
            "种族 半精灵　背景 士兵\n"
            "属性值：力量 15　敏捷 14　体质 13　智力 12　感知 10　魅力 8\n"
            "或宽松格式：名字：阿尔文 / 职业：战士 / 力量 15"
        )

    # ---- 补记 note ----
    if combat_seen:
        notes.append("战斗字段（HP/AC/法术位/攻击）已忽略，将由规则引擎按职业与装备重算。")
    if not name:
        notes.append("未识别到卡名，已使用「未知冒险者」，可用 /卡 改名 修改。")
    if not classes:
        notes.append("未识别到职业，战斗字段可能无法自动计算。")
    if not scores:
        notes.append("未识别到属性值，已全部默认 10。")

    return ImportResult(sheet=sheet, notes=notes)


def _record_ability(scores: dict[str, int], notes: list[str], ab: str, val: int) -> None:
    """记录属性值；越界 clamp 并记 note。"""
    if val < 1 or val > 30:
        clamped = max(1, min(30, val))
        notes.append(f"属性 {ABILITY_CN[ab]} 超出 1-30，已截断 {val}→{clamped}。")
        val = clamped
    scores[ab] = val


def _split_prof_blocks(text: str) -> list[str]:
    """把熟练行正文按「类别头」切成块：每块以 豁免/技能/工具/武器/防具 开头。

    兼容 format_sheet 输出「技能 察觉、隐匿★　武器 简易武器、长剑」——若按
    全角空格盲切，「长剑」会脱离「武器」前缀；按类别头定位切块则每块自带头。
    """
    positions = [(m.start(), m.end()) for m in _PROF_HEAD_FIND_RE.finditer(text)]
    if not positions:
        return [text.strip()] if text.strip() else []
    blocks: list[str] = []
    for i, (start, _end) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        block = text[start:end].strip()
        if block:
            blocks.append(block)
    return blocks


def _parse_prof_seg(
    seg: str,
    saves: set[str],
    skills: set[str],
    tools: set[str],
    weapons: set[str],
    armors: set[str],
) -> None:
    """解析一个熟练块（如「豁免 力量、体质」/「技能 运动」/「武器 简易武器、长剑」）。"""
    seg = seg.strip()
    if not seg:
        return
    m = _PROF_HEAD_RE.match(seg)
    if m:
        head = m.group(1).lower()
        if head in ("豁免", "save"):
            kind = "save"
        elif head in ("工具", "tool"):
            kind = "tool"
        elif head in ("武器", "weapon"):
            kind = "weapon"
        elif head in ("防具", "armor"):
            kind = "armor"
        else:
            kind = "skill"
        items = seg[m.end():]
    else:
        kind = ""
        items = seg
    for item in re.split(r"[、,，;；\s]+", items):
        item = item.strip().rstrip("★")
        if not item or item in _PLACEHOLDER_WORDS:
            continue
        if kind == "save":
            ab = ABILITY_ALIAS.get(item.lower())
            if ab:
                saves.add(ab)
        elif kind == "skill":
            sk = SKILL_ALIAS.get(item.lower())
            if sk:
                skills.add(sk)
        elif kind == "tool":
            if item == "万事通" or item.startswith("+"):
                continue  # format_sheet 尾部信息「万事通 +N」无类别头，忽略
            tools.add(item)
        elif kind == "weapon":
            if item == "万事通" or item.startswith("+"):
                continue
            weapons.add(item)
        elif kind == "armor":
            if item == "万事通" or item.startswith("+"):
                continue
            armors.add(item)
        else:
            # 无前缀：先试属性（豁免），再试技能；其余忽略（如「万事通 +N」）
            ab = ABILITY_ALIAS.get(item.lower())
            if ab:
                saves.add(ab)
                continue
            sk = SKILL_ALIAS.get(item.lower())
            if sk:
                skills.add(sk)


def _parse_equip_seg(seg: str) -> tuple[str, str, str]:
    """解析一个装备段，返回 (主手, 副手, 护甲) 更新值；未命中返回空元组。

    v0.30.0：占位词（无/—/- 等，如「护甲：无」）视为未设置，防止把
    熟练段写法误写入装备槽。
    """
    m = _EQUIP_SEG_RE.match(seg.strip())
    if not m:
        return ("", "", "")
    key = m.group(1).lower()
    val = m.group(2).strip()
    if not val or val in _PLACEHOLDER_WORDS:
        return ("", "", "")
    if key in ("主手", "main"):
        return (val, "", "")
    if key in ("副手", "off"):
        return ("", val, "")
    if key in ("护甲", "armor"):
        return ("", "", val)
    return ("", "", "")

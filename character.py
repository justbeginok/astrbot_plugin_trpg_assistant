"""
character.py — 角色卡（Character Sheet）模块。

提供 DnD 5e 角色卡的数据模型与 KV 持久化管理：

- 数据：六维属性值（Ability Scores）、职业（支持兼职列表）、种族、背景、阵营、
  熟练技能/豁免、战斗字段（HP/AC/法术位/攻击加值，双层 base+bonus）、装备槽位、
  生平自由文本、命名掷骰。
- 归属：多卡模型——一个玩家（会话来源 + 发送者 ID）可有多张命名卡，一张活跃。
- KV key 布局（AstrBot KV 无枚举能力，卡名索引必须显式维护）：
  - character:{origin}:{sender}:{卡名}   卡本体
  - character:index:{origin}:{sender}    卡名索引 {"names": [...]}
  - character:active:{origin}:{sender}   活跃卡指针 {"name": 卡名}
- 术语：六维称「属性值」（Ability Scores），与知识库的「特性反查」严格区分；
  「属性筛选」已被知识库占用，勿复用。

v0.17 约定：战斗字段（hp_max/ac/spell_slots/attack_bonuses）为双层模型
LayeredStat{base, bonus}，本版本 base 恒为 0、手动值写入 bonus；
v0.18 规则引擎上线后 base 由引擎重算，bonus 保留房规调整。
插件自动计算的仅：属性修正、熟练加值、技能/豁免总修正、被动感知。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Star

# ---------------------------------------------------------------------------
# 常量与词表
# ---------------------------------------------------------------------------

# KV 存储 key 前缀（PluginKVStoreMixIn 已按 plugin_id 隔离命名空间，
# 此前缀仅用于本插件内部区分功能）。
_KV_PREFIX_CARD = "character:"  # character:{origin}:{sender}:{卡名}
_KV_PREFIX_INDEX = "character:index:"  # character:index:{origin}:{sender}
_KV_PREFIX_ACTIVE = "character:active:"  # character:active:{origin}:{sender}

# 卡名最大长度（超出截断并追加省略号）。
_CARD_NAME_MAX = 20
# 自由文本字段（生平/阵营等）最大长度。
_TEXT_MAX = 400
# 需要从文本中剔除的控制字符正则（与 inventory.py 一致）。
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 六维属性：英文缩写 → 中文名（显示与别名解析共用）。
ABILITY_NAMES = ("str", "dex", "con", "int", "wis", "cha")
ABILITY_CN: dict[str, str] = {
    "str": "力量",
    "dex": "敏捷",
    "con": "体质",
    "int": "智力",
    "wis": "感知",
    "cha": "魅力",
}
# 中文名 → 缩写（供「力量15」显式映射解析）。
ABILITY_CN_REV: dict[str, str] = {v: k for k, v in ABILITY_CN.items()}

# 属性别名表（全小写）→ 属性缩写，供 /r 联动与命令解析。
ABILITY_ALIAS: dict[str, str] = {
    "str": "str", "力量": "str", "力": "str", "strength": "str",
    "dex": "dex", "敏捷": "dex", "敏": "dex", "dexterity": "dex",
    "con": "con", "体质": "con", "体": "con", "constitution": "con",
    "int": "int", "智力": "int", "智": "int", "intelligence": "int",
    "wis": "wis", "感知": "wis", "感": "wis", "wisdom": "wis",
    "cha": "cha", "魅力": "cha", "魅": "cha", "charisma": "cha",
}

SKILLS: dict[str, str] = {
    # 技能名: 关联属性
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

# 技能中文名 → SKILLS key（+ 英文名小写），供 /r 联动与命令解析。
SKILL_CN: dict[str, str] = {
    "体操": "acrobatics",
    "驯兽": "animal_handling",
    "奥秘": "arcana",
    "运动": "athletics",
    "欺瞒": "deception",
    "历史": "history",
    "洞悉": "insight",
    "威吓": "intimidation",
    "调查": "investigation",
    "医药": "medicine",
    "自然": "nature",
    "察觉": "perception",
    "表演": "performance",
    "说服": "persuasion",
    "宗教": "religion",
    "巧手": "sleight_of_hand",
    "隐匿": "stealth",
    "求生": "survival",
}
SKILL_ALIAS: dict[str, str] = {**SKILL_CN}
SKILL_ALIAS.update({k: k for k in SKILLS})  # 英文技能名小写直达
# 民间/3R 常用译名别名（显示仍用官方名「说服/体操」，仅解析与 /r 联动识别）
SKILL_ALIAS.update({"游说": "persuasion", "特技": "acrobatics"})

# 技能 key → 中文名（/r 联动标签显示用）。
SKILL_CN_REV: dict[str, str] = {v: k for k, v in SKILL_CN.items()}

# 攻击检定别名表（全小写）→ 触发角色卡攻击掷骰联动（v0.22）。
# 与 ABILITY_ALIAS/SKILL_ALIAS 无冲突；实际武器名/「列表」/DC 由调用方解释。
_ATTACK_ALIAS: frozenset[str] = frozenset({"攻击", "attack", "atk"})

# 已知法术环阶 key 归一（v0.30.0）：中文/英文/数字 → 规范 key（"戏法"|"1".."9"）。
_SPELL_RING_CN_NUM: dict[str, str] = {
    "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
    "六": "6", "七": "7", "八": "8", "九": "9",
}


def _norm_spell_ring(key: str) -> str | None:
    """环阶 key 归一：戏法/0环/cantrip/一环/1环/1 → "戏法"|"1".."9"；无法识别返回 None。"""
    k = (key or "").strip().lower()
    if k in ("戏法", "cantrip", "0", "0环"):
        return "戏法"
    m = re.match(r"^(\d{1,2})\s*环?$", k)
    if m:
        n = int(m.group(1))
        return "戏法" if n == 0 else (str(n) if 1 <= n <= 9 else None)
    m = re.match(r"^([一二三四五六七八九])\s*环$", k)
    if m:
        return _SPELL_RING_CN_NUM[m.group(1)]
    return None


def _spell_ring_label(ring: str) -> str:
    """环阶 key → 显示标签（戏法 / N环）。"""
    return "戏法" if ring == "戏法" else f"{ring}环"


def _spell_ring_sort_key(ring: str) -> tuple[int, str]:
    """环阶排序键：戏法置顶，其余按环数升序。"""
    return (0, ring) if ring == "戏法" else (1, ring.zfill(2))


def _sanitize_spell_name(name: object, max_len: int = 60) -> str:
    """法术名清洗：保留「名（来源）」等括号标注，剔除控制字符并截断。"""
    return _sanitize_text(name, max_len)


# 已知法术分组行：戏法：A，B / 一环：C，D / 1环：E / cantrip: F
_SPELLS_GROUP_RE = re.compile(
    r"^(戏法|cantrip|[0-9]{1,2}\s*环?|[一二三四五六七八九]\s*环)\s*[:：]\s*(.+)$",
    re.IGNORECASE,
)


def parse_spells_text(text: str) -> dict[str, list[str]]:
    """把已知法术文本解析为 {环阶: [法术名]}（v0.30.0）。

    支持两种形态：
    - 多行：每行一组「戏法：A，B」「一环：C，D」（玩家常见格式）；
    - 单行多组：组间用全角空格/分号/换行分隔「戏法：A，B　一环：C，D」。
    法术名保留括号标注（如「塔莎狂笑术（妖精触碰）」），环阶 key 归一
    （戏法/0环/cantrip/一环/1环/1 → "戏法"|"1".."9"），组内去重限条数。
    """
    out: dict[str, list[str]] = {}
    for group in re.split(r"[;；\n　]+", str(text or "")):
        group = _CONTROL_CHARS_RE.sub(" ", group).strip()
        if not group:
            continue
        m = _SPELLS_GROUP_RE.match(group)
        if not m:
            continue
        ring = _norm_spell_ring(m.group(1))
        if ring is None:
            continue
        bucket = out.setdefault(ring, [])
        for name in re.split(r"[、,，]+", m.group(2)):
            t = _sanitize_spell_name(name)
            if t and t not in bucket:
                bucket.append(t)
        out[ring] = bucket[:50]
    return dict(sorted(out.items(), key=lambda kv: _spell_ring_sort_key(kv[0])))

# 万事通（Jack of All Trades）职业名匹配集：class_name 小写归一后比对。
_BARD_NAMES: frozenset[str] = frozenset({"吟游诗人", "bard"})

# 职业段正则（v0.41.0，与 card_import 文本导入共用）：
# 「战士（勇士） 3」「法师 2」「战士」「战士 3」；子职可缺、等级可缺（默认 1）。
_CLASS_SEG_RE = re.compile(
    r"^\s*(?P<cls>[^（(]+?)\s*(?:[（(](?P<sub>[^）)]*)[）)])?\s*(?P<lvl>\d+)?\s*$"
)


def normalize_edition(raw: str) -> str:
    """把版本输入归一为 2014 / 2024；无法识别返回空串（v0.41.0）。

    与 card_import 文本导入的版本识别共用同一规则。
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "2024" in s or s in ("5.5e", "5.5", "5r"):
        return "2024"
    if "2014" in s or s in ("5e", "5.0"):
        return "2014"
    return ""


def parse_classes_text(text: str) -> list[ClassLevel]:
    """把「战士 3 + 法师（塑能） 2」/「法师 1」解析为兼职列表（v0.41.0）。

    分段分隔支持半角/全角加号（+＋）；每段形如「职业名（子职） 等级」，
    等级可省略（默认 1）。无法识别的分段直接丢弃；全空返回空列表。
    card_import 文本导入的职业行复用此函数（单一事实来源）。
    """
    out: list[ClassLevel] = []
    for part in re.split(r"\s*[+＋]\s*", (text or "").strip()):
        m = _CLASS_SEG_RE.match(part)
        if not m or not m.group("cls"):
            continue
        lvl_raw = m.group("lvl")
        out.append(
            ClassLevel(
                class_name=m.group("cls").strip(),
                subclass=(m.group("sub") or "").strip(),
                level=int(lvl_raw) if lvl_raw else 1,
            )
        )
    return out


def _sanitize_card_name(text: str) -> str:
    """剔除控制字符并截断至 _CARD_NAME_MAX 字符，防止伪造多行输出。"""
    cleaned = _CONTROL_CHARS_RE.sub(" ", str(text or "")).strip()
    if len(cleaned) > _CARD_NAME_MAX:
        cleaned = cleaned[:_CARD_NAME_MAX] + "…"
    return cleaned


def _sanitize_text(text: str, max_len: int = _TEXT_MAX) -> str:
    """剔除自由文本中的控制字符（保留换行为空格）并截断。"""
    cleaned = _CONTROL_CHARS_RE.sub(" ", str(text or "")).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "…"
    return cleaned


def _to_int(value: object, default: int, min_val: int, max_val: int) -> int:
    """安全转换整数并夹取到 [min_val, max_val]；失败返回 default。"""
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(min_val, min(max_val, result))


def _to_str(value: object) -> str:
    return "" if value is None else str(value)


def resolve_roll_alias(
    text: str, named_rolls: dict[str, str] | None = None
) -> tuple[str, str, str] | None:
    """识别角色卡联动掷骰：仅当首 token 整词命中属性/技能/「X豁免」/攻击别名时返回
    (kind, key, rest)。

    - kind ∈ {"ability", "skill", "save", "attack", "named"}；
      - "ability"：key 为属性缩写（str/dex/con/int/wis/cha）；
      - "skill"：key 为技能 key（如 "perception"）；
      - "save"：key 为属性缩写；
      - "attack"：key 恒为空，武器名/「列表」/DC 由调用方按角色卡解析；
      - "named"（v0.32.0）：key 为命名掷骰名称（named_rolls 传入时生效）。
    - rest 为首 token 之后剩余的文本（如武器名、DC「15」或「列表」）。
    - 不做任何 IO；无命中返回 None（调用方走原掷骰逻辑）。

    设计约束：只在整 token 命中时触发，`d20感知15` 等紧凑写法不受影响。
    v0.32.0：传入 named_rolls 时，首 token 整词命中命名掷骰键（大小写归一）
    **优先于内建别名**——命名掷骰是玩家显式登记的自定义快捷方式，应覆盖
    默认行为；不传 named_rolls 时行为与旧版完全一致。
    """
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(None, 1)
    first = parts[0].strip().lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    # 命名掷骰优先（v0.32.0）：玩家显式登记的自定义表达式覆盖内建联动
    if named_rolls:
        norm = {k.strip().lower(): k for k in named_rolls if k and k.strip()}
        orig = norm.get(first)
        if orig is not None:
            return ("named", orig, rest)

    # 「X豁免」形式（中文）：如「敏捷豁免」「力量豁免」
    if first.endswith("豁免") and first[: -len("豁免")] in ABILITY_ALIAS:
        return ("save", ABILITY_ALIAS[first[: -len("豁免")]], rest)

    if first in ABILITY_ALIAS:
        key = ABILITY_ALIAS[first]
        rest_lower = rest.lower()
        if rest_lower.startswith("save"):
            remainder = rest[len("save") :].strip()
            return ("save", key, remainder)
        if rest.startswith("豁免"):
            remainder = rest[len("豁免") :].strip()
            return ("save", key, remainder)
        return ("ability", key, rest)

    # 攻击检定：/r 攻击、/r 攻击 长剑、/r 攻击 列表、/r 攻击 15
    if first in _ATTACK_ALIAS:
        return ("attack", "", rest)

    if first in SKILL_ALIAS:
        return ("skill", SKILL_ALIAS[first], rest)

    return None


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class AbilityScores:
    """DnD 六项核心属性值。"""

    strength: int = 10
    dexterity: int = 10
    constitution: int = 10
    intelligence: int = 10
    wisdom: int = 10
    charisma: int = 10

    def __post_init__(self) -> None:
        """Clamp ability scores to the legal DnD range [1, 30]."""

        def _clamp(v: object) -> int:
            try:
                return max(1, min(30, int(v)))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return 10

        self.strength = _clamp(self.strength)
        self.dexterity = _clamp(self.dexterity)
        self.constitution = _clamp(self.constitution)
        self.intelligence = _clamp(self.intelligence)
        self.wisdom = _clamp(self.wisdom)
        self.charisma = _clamp(self.charisma)

    def get(self, ability: str) -> int:
        """根据属性缩写（str/dex/con/int/wis/cha）返回对应属性值。"""
        mapping = {
            "str": self.strength,
            "dex": self.dexterity,
            "con": self.constitution,
            "int": self.intelligence,
            "wis": self.wisdom,
            "cha": self.charisma,
        }
        key = ability.lower()
        if key not in mapping:
            raise ValueError(f"未知属性: {ability!r}")
        return mapping[key]

    def set(self, ability: str, value: int) -> None:
        """按属性缩写设置属性值（自动夹取 1-30）。"""
        mapping = {
            "str": "strength",
            "dex": "dexterity",
            "con": "constitution",
            "int": "intelligence",
            "wis": "wisdom",
            "cha": "charisma",
        }
        key = ability.lower()
        if key not in mapping:
            raise ValueError(f"未知属性: {ability!r}")
        setattr(self, mapping[key], max(1, min(30, int(value))))

    @staticmethod
    def modifier(score: int) -> int:
        """DnD 标准属性修正值公式：floor((score - 10) / 2)。"""
        return (score - 10) // 2

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "strength": self.strength,
            "dexterity": self.dexterity,
            "constitution": self.constitution,
            "intelligence": self.intelligence,
            "wisdom": self.wisdom,
            "charisma": self.charisma,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AbilityScores:
        if not isinstance(data, dict):
            return cls()
        return cls(
            strength=_to_int(data.get("strength"), 10, 1, 30),
            dexterity=_to_int(data.get("dexterity"), 10, 1, 30),
            constitution=_to_int(data.get("constitution"), 10, 1, 30),
            intelligence=_to_int(data.get("intelligence"), 10, 1, 30),
            wisdom=_to_int(data.get("wisdom"), 10, 1, 30),
            charisma=_to_int(data.get("charisma"), 10, 1, 30),
        )


@dataclass
class ClassLevel:
    """兼职列表中的一项：职业 + 可选子职 + 该职业等级。"""

    class_name: str = ""
    subclass: str = ""
    level: int = 1

    def __post_init__(self) -> None:
        self.class_name = _sanitize_text(self.class_name, 40)
        self.subclass = _sanitize_text(self.subclass, 40)
        self.level = _to_int(self.level, 1, 1, 20)

    def to_dict(self) -> dict:
        return {
            "class_name": self.class_name,
            "subclass": self.subclass,
            "level": self.level,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClassLevel:
        if not isinstance(data, dict):
            return cls()
        return cls(
            class_name=_to_str(data.get("class_name")),
            subclass=_to_str(data.get("subclass")),
            level=_to_int(data.get("level"), 1, 1, 20),
        )


@dataclass
class LayeredStat:
    """战斗字段双层模型：base（v0.18 规则引擎算）+ bonus（手动房规调整）。

    显示值 = base + bonus。v0.17 base 恒为 0，手动值写入 bonus；
    规则引擎重算 base 时 bonus 原样保留，房规特许永不与自动重算冲突。
    """

    base: int = 0
    bonus: int = 0

    @property
    def total(self) -> int:
        return self.base + self.bonus

    def to_dict(self) -> dict:
        return {"base": self.base, "bonus": self.bonus}

    @classmethod
    def from_dict(cls, data: dict) -> LayeredStat:
        if not isinstance(data, dict):
            return cls()
        return cls(
            base=_to_int(data.get("base"), 0, -1000, 1000),
            bonus=_to_int(data.get("bonus"), 0, -1000, 1000),
        )


@dataclass
class EquipmentSlots:
    """装备槽位：记录「穿着/持有」的物品名，与个人背包条目名对应。

    双层模型：背包管理「拥有什么」，槽位管理「当前用什么」。
    v0.17 仅记录不联动；v0.18 规则引擎据此计算 AC/攻击加值。
    """

    main_hand: str = ""
    off_hand: str = ""
    armor: str = ""

    def __post_init__(self) -> None:
        self.main_hand = _sanitize_text(self.main_hand, 40)
        self.off_hand = _sanitize_text(self.off_hand, 40)
        self.armor = _sanitize_text(self.armor, 40)

    def to_dict(self) -> dict:
        return {
            "main_hand": self.main_hand,
            "off_hand": self.off_hand,
            "armor": self.armor,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EquipmentSlots:
        if not isinstance(data, dict):
            return cls()
        return cls(
            main_hand=_to_str(data.get("main_hand")),
            off_hand=_to_str(data.get("off_hand")),
            armor=_to_str(data.get("armor")),
        )


@dataclass
class CharacterSheet:
    """DnD 5e 角色卡。所有字段带默认值，from_dict 容忍脏数据。"""

    name: str = "未知冒险者"
    edition: str = "2014"  # "2014" | "2024"，非法值回退 "2014"
    classes: list[ClassLevel] = field(default_factory=list)  # 兼职列表
    race: str = ""
    background: str = ""
    alignment: str = ""
    ability_scores: AbilityScores = field(default_factory=AbilityScores)
    skill_proficiencies: set[str] = field(default_factory=set)
    save_proficiencies: set[str] = field(default_factory=set)
    # v0.21 新增：技能专精（双倍熟练）、专长与工具/武器/防具熟练
    skill_expertise: set[str] = field(default_factory=set)
    feats: list[str] = field(default_factory=list)
    tool_proficiencies: set[str] = field(default_factory=set)
    weapon_proficiencies: set[str] = field(default_factory=set)
    armor_proficiencies: set[str] = field(default_factory=set)
    hp_max: LayeredStat = field(default_factory=LayeredStat)
    ac: LayeredStat = field(default_factory=LayeredStat)
    # v0.23.0 新增：步行速度（尺/回合），双层模型——base 由规则引擎按种族重算
    speed: LayeredStat = field(default_factory=LayeredStat)
    spell_slots: dict[str, LayeredStat] = field(default_factory=dict)
    # 命名攻击加值：{"长剑": LayeredStat}（v0.18 起按装备槽自动生成）
    attack_bonuses: dict[str, LayeredStat] = field(default_factory=dict)
    equipment: EquipmentSlots = field(default_factory=EquipmentSlots)
    backstory: str = ""  # 生平自由文本（XGE 三段引导产物的最终文本）
    # v0.28.0 新增：语言（多门，自由文本无词表校验，仅清洗+去重+限条数）
    languages: set[str] = field(default_factory=set)
    # v0.30.0 新增：人物基础信息（自由文本，缺失即空零迁移）
    deity: str = ""     # 信仰
    age: str = ""       # 年龄
    gender: str = ""    # 性别
    height: str = ""    # 身高
    weight: str = ""    # 体重
    # v0.30.0 新增：资源与先攻
    hit_dice_used: int = 0  # 短休已用生命骰（0..总等级，显示时再 clamp）
    inspiration: int = 0    # 激励（0/1）
    initiative: LayeredStat = field(default_factory=LayeredStat)  # 先攻：base=敏捷修正（引擎），bonus=房规
    # v0.30.0 新增：已知法术（环阶 key → 法术名列表；key ∈ "戏法"|"1".."9"）
    spells: dict[str, list[str]] = field(default_factory=dict)
    named_rolls: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _sanitize_card_name(self.name) or "未知冒险者"
        if self.edition not in ("2014", "2024"):
            self.edition = "2014"
        if not isinstance(self.classes, list):
            self.classes = []
        self.classes = [
            c if isinstance(c, ClassLevel) else ClassLevel.from_dict({})
            for c in self.classes
            if isinstance(c, ClassLevel) or isinstance(c, dict)
        ]
        self.race = _sanitize_text(self.race, 40)
        self.background = _sanitize_text(self.background, 40)
        self.alignment = _sanitize_text(self.alignment, 20)
        self.backstory = _sanitize_text(self.backstory)
        # 熟练集：只保留词表内合法项，小写归一。
        self.skill_proficiencies = {
            s.lower() for s in (self.skill_proficiencies or ())
            if s.lower() in SKILLS
        }
        self.save_proficiencies = {
            s.lower() for s in (self.save_proficiencies or ())
            if s.lower() in ABILITY_NAMES
        }
        # 专精集：同技能熟练的词表过滤
        self.skill_expertise = {
            s.lower() for s in (self.skill_expertise or ())
            if isinstance(s, str) and s.lower() in SKILLS
        }
        # 专长/工具/武器/防具：自由文本，无权威词表，仅清洗+去重+限条数
        if not isinstance(self.feats, list):
            self.feats = list(self.feats) if isinstance(self.feats, (set, tuple)) else []
        self.feats = list(dict.fromkeys(
            t for t in (_sanitize_text(x, 40) for x in self.feats if isinstance(x, str)) if t
        ))[:30]
        for attr in ("tool_proficiencies", "weapon_proficiencies", "armor_proficiencies", "languages"):
            raw = getattr(self, attr)
            if not isinstance(raw, (set, list, tuple)):
                raw = ()
            cleaned = {
                t for t in (_sanitize_text(x, 40) for x in raw if isinstance(x, str)) if t
            }
            setattr(self, attr, set(sorted(cleaned)[:30]))
        if not isinstance(self.spell_slots, dict):
            self.spell_slots = {}
        self.spell_slots = {
            str(k): (v if isinstance(v, LayeredStat) else LayeredStat.from_dict(v))
            for k, v in self.spell_slots.items()
            if isinstance(v, (dict, LayeredStat))
        }
        if not isinstance(self.attack_bonuses, dict):
            self.attack_bonuses = {}
        self.attack_bonuses = {
            _sanitize_text(str(k), 40): (
                v if isinstance(v, LayeredStat) else LayeredStat.from_dict(v)
            )
            for k, v in self.attack_bonuses.items()
            if isinstance(v, (dict, LayeredStat))
        }
        # v0.30.0：人物基础信息 / 资源 / 先攻 / 已知法术
        self.deity = _sanitize_text(self.deity, 40)
        self.age = _sanitize_text(self.age, 20)
        self.gender = _sanitize_text(self.gender, 20)
        self.height = _sanitize_text(self.height, 20)
        self.weight = _sanitize_text(self.weight, 20)
        self.hit_dice_used = _to_int(self.hit_dice_used, 0, 0, 20)
        self.inspiration = _to_int(self.inspiration, 0, 0, 1)
        if not isinstance(self.initiative, LayeredStat):
            self.initiative = LayeredStat.from_dict(self.initiative)
        if not isinstance(self.spells, dict):
            self.spells = {}
        self.spells = self._clean_spells(self.spells)
        if not isinstance(self.named_rolls, dict):
            self.named_rolls = {}
        self.named_rolls = {
            _sanitize_text(str(k), 20): _sanitize_text(str(v), 100)
            for k, v in self.named_rolls.items()
        }

    @staticmethod
    def _clean_spells(data: dict) -> dict[str, list[str]]:
        """已知法术容器清洗：环阶 key 归一，法术名清洗去重限条数（每环 50）。"""
        out: dict[str, list[str]] = {}
        for k, v in (data or {}).items():
            ring = _norm_spell_ring(str(k))
            if ring is None or not isinstance(v, (list, tuple, set)):
                continue
            bucket: list[str] = []
            for name in v:
                t = _sanitize_spell_name(name)
                if t and t not in bucket:
                    bucket.append(t)
            if bucket:
                out[ring] = bucket[:50]
        return dict(sorted(out.items(), key=lambda kv: _spell_ring_sort_key(kv[0])))

    def add_spell(self, ring: str, name: str) -> bool:
        """加入一个已知法术（v0.31.0）；环阶无法识别或名称为空返回 False。"""
        r = _norm_spell_ring(ring)
        n = _sanitize_spell_name(name)
        if r is None or not n:
            return False
        bucket = self.spells.setdefault(r, [])
        if n not in bucket:
            bucket.append(n)
            self.spells = dict(
                sorted(self.spells.items(), key=lambda kv: _spell_ring_sort_key(kv[0]))
            )
        return True

    def remove_spell(self, ring: str, name: str) -> bool:
        """删除一个已知法术（v0.31.0）；环阶/名称不存在返回 False，环空自动移除。"""
        r = _norm_spell_ring(ring)
        if r is None or r not in self.spells:
            return False
        n = _sanitize_spell_name(name)
        bucket = self.spells[r]
        if n not in bucket:
            return False
        bucket.remove(n)
        if not bucket:
            del self.spells[r]
        return True

    # ------------------------------------------------------------------
    # 派生属性（v0.17 唯一自动计算项）
    # ------------------------------------------------------------------

    @property
    def level(self) -> int:
        """总等级 = 兼职列表等级之和；空列表回退 1。"""
        if not self.classes:
            return 1
        return sum(c.level for c in self.classes)

    @property
    def proficiency_bonus(self) -> int:
        """按总等级计算的 5e 标准熟练加值。"""
        return 2 + (self.level - 1) // 4

    def get_ability_modifier(self, ability: str) -> int:
        """返回给定属性缩写的修正值。"""
        score = self.ability_scores.get(ability)
        return AbilityScores.modifier(score)

    def jack_of_all_trades_bonus(self) -> int:
        """万事通（吟游诗人 2 级特性）：卡上有「吟游诗人」等级>=2 → ⌊熟练/2⌋，否则 0。

        判定硬编码职业名（中文「吟游诗人」或英文「bard」），不查知识库。
        """
        for c in self.classes:
            if c.class_name.strip().lower() in _BARD_NAMES and c.level >= 2:
                return self.proficiency_bonus // 2
        return 0

    def skill_check(self, skill: str) -> tuple[int, list[str]]:
        """技能检定 → (总修正, 标签列表)。标签供 /r 展示。

        熟练档位互斥，取最高：专精(2×熟练) > 熟练(1×熟练) > 万事通(⌊熟练/2⌋)。
        """
        skill = skill.lower()
        if skill not in SKILLS:
            raise ValueError(f"未知技能: {skill!r}")
        mod = self.get_ability_modifier(SKILLS[skill])
        tags: list[str] = []
        if skill in self.skill_expertise:
            mod += self.proficiency_bonus * 2
            tags.append(f"专精+{self.proficiency_bonus * 2}")
        elif skill in self.skill_proficiencies:
            mod += self.proficiency_bonus
            tags.append(f"熟练+{self.proficiency_bonus}")
        else:
            joat = self.jack_of_all_trades_bonus()
            if joat:
                mod += joat
                tags.append(f"万事通+{joat}")
        return mod, tags

    def ability_check(self, ability: str) -> tuple[int, list[str]]:
        """纯属性检定 → (总修正, 标签列表)。万事通生效时追加 ⌊熟练/2⌋。

        豁免检定不走此函数（get_save_modifier 不变），故豁免天然不吃万事通。
        """
        ability = ability.lower()
        if ability not in ABILITY_NAMES:
            raise ValueError(f"未知属性: {ability!r}")
        mod = self.get_ability_modifier(ability)
        tags: list[str] = []
        joat = self.jack_of_all_trades_bonus()
        if joat:
            mod += joat
            tags.append(f"万事通+{joat}")
        return mod, tags

    def get_skill_modifier(self, skill: str) -> int:
        """技能检定总修正：属性修正 + 熟练档位加值（专精/熟练/万事通）。"""
        return self.skill_check(skill)[0]

    def get_save_modifier(self, ability: str) -> int:
        """豁免总修正：属性修正 + 熟练加值（若豁免熟练）。"""
        ability = ability.lower()
        if ability not in ABILITY_NAMES:
            raise ValueError(f"未知属性: {ability!r}")
        mod = self.get_ability_modifier(ability)
        if ability in self.save_proficiencies:
            mod += self.proficiency_bonus
        return mod

    # ------------------------------------------------------------------
    # 攻击检定（v0.22）：attack_bonuses 由规则引擎/手动维护，此处只读不重算
    # ------------------------------------------------------------------

    def list_attacks(self) -> list[tuple[str, int]]:
        """全部攻击选项 [(名称, 总加值)]，按名称排序；空字典 → []。"""
        return sorted((n, s.total) for n, s in self.attack_bonuses.items())

    def main_hand_attack(self) -> tuple[str, int] | None:
        """主手武器攻击 (名称, 总加值)；主手为空或攻击表查不到 → None。

        主手名与攻击表键不一致（如攻击表生成「+1 巨锤」而主手写「巨锤」）时，
        降级用 resolve_attack 包含匹配兜底。
        """
        name = self.equipment.main_hand.strip()
        if not name:
            return None
        stat = self.attack_bonuses.get(name)
        if stat is not None:
            return (name, stat.total)
        return self.resolve_attack(name)

    def resolve_attack(self, query: str) -> tuple[str, int] | None:
        """按名字匹配攻击选项：精确 → 前缀（取最短名）→ 包含；无命中 → None。

        供「/r 攻击 <武器名>」与相近候选提示使用。
        """
        q = query.strip()
        if not q:
            return None
        for n, s in self.attack_bonuses.items():
            if n == q:
                return (n, s.total)
        pref = sorted(
            ((n, s.total) for n, s in self.attack_bonuses.items() if n.startswith(q)),
            key=lambda t: len(t[0]),
        )
        if pref:
            return pref[0]
        for n, s in self.attack_bonuses.items():
            if q in n:
                return (n, s.total)
        return None

    @property
    def passive_perception(self) -> int:
        """被动感知 = 10 + 察觉技能总修正（含专精/万事通联动）。"""
        return 10 + self.skill_check("perception")[0]

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "edition": self.edition,
            "classes": [c.to_dict() for c in self.classes],
            "race": self.race,
            "background": self.background,
            "alignment": self.alignment,
            "ability_scores": self.ability_scores.to_dict(),
            "skill_proficiencies": sorted(self.skill_proficiencies),
            "save_proficiencies": sorted(self.save_proficiencies),
            "skill_expertise": sorted(self.skill_expertise),
            "feats": list(self.feats),
            "tool_proficiencies": sorted(self.tool_proficiencies),
            "weapon_proficiencies": sorted(self.weapon_proficiencies),
            "armor_proficiencies": sorted(self.armor_proficiencies),
            "hp_max": self.hp_max.to_dict(),
            "ac": self.ac.to_dict(),
            "speed": self.speed.to_dict(),
            "spell_slots": {k: v.to_dict() for k, v in self.spell_slots.items()},
            "attack_bonuses": {
                k: v.to_dict() for k, v in self.attack_bonuses.items()
            },
            "equipment": self.equipment.to_dict(),
            "backstory": self.backstory,
            "languages": sorted(self.languages),
            "deity": self.deity,
            "age": self.age,
            "gender": self.gender,
            "height": self.height,
            "weight": self.weight,
            "hit_dice_used": self.hit_dice_used,
            "inspiration": self.inspiration,
            "initiative": self.initiative.to_dict(),
            "spells": {k: list(v) for k, v in self.spells.items()},
            "named_rolls": dict(self.named_rolls),
        }

    @classmethod
    def from_dict(cls, data: dict) -> CharacterSheet:
        """从 KV 存储读取的字典中还原角色卡，容忍缺失字段与脏数据。

        非 dict 输入直接返回默认卡；classes 中非 dict 子条目跳过；
        LayeredStat / EquipmentSlots 的 from_dict 各自容错。
        """
        if not isinstance(data, dict):
            return cls()
        sheet = cls(
            name=_to_str(data.get("name")) or "未知冒险者",
            edition=_to_str(data.get("edition")),
            classes=[
                ClassLevel.from_dict(c)
                for c in data.get("classes", [])
                if isinstance(c, dict)
            ],
            race=_to_str(data.get("race")),
            background=_to_str(data.get("background")),
            alignment=_to_str(data.get("alignment")),
            ability_scores=AbilityScores.from_dict(data.get("ability_scores")),
            skill_proficiencies=set(data.get("skill_proficiencies") or ()),
            save_proficiencies=set(data.get("save_proficiencies") or ()),
            skill_expertise=set(data.get("skill_expertise") or ()) if not isinstance(data.get("skill_expertise"), str) else set(),
            feats=list(data.get("feats")) if isinstance(data.get("feats"), (list, tuple)) else [],
            tool_proficiencies=set(data.get("tool_proficiencies") or ()) if not isinstance(data.get("tool_proficiencies"), str) else set(),
            weapon_proficiencies=set(data.get("weapon_proficiencies") or ()) if not isinstance(data.get("weapon_proficiencies"), str) else set(),
            armor_proficiencies=set(data.get("armor_proficiencies") or ()) if not isinstance(data.get("armor_proficiencies"), str) else set(),
            hp_max=LayeredStat.from_dict(data.get("hp_max")),
            ac=LayeredStat.from_dict(data.get("ac")),
            speed=LayeredStat.from_dict(data.get("speed")),
            spell_slots=data.get("spell_slots") or {},
            attack_bonuses=data.get("attack_bonuses") or {},
            equipment=EquipmentSlots.from_dict(data.get("equipment")),
            backstory=_to_str(data.get("backstory")),
            languages=set(data.get("languages") or ()) if not isinstance(data.get("languages"), str) else set(),
            deity=_to_str(data.get("deity")),
            age=_to_str(data.get("age")),
            gender=_to_str(data.get("gender")),
            height=_to_str(data.get("height")),
            weight=_to_str(data.get("weight")),
            hit_dice_used=data.get("hit_dice_used"),
            inspiration=data.get("inspiration"),
            initiative=LayeredStat.from_dict(data.get("initiative")),
            spells=data.get("spells") or {},
            named_rolls=data.get("named_rolls") or {},
        )
        return sheet


# ---------------------------------------------------------------------------
# 角色卡管理器
# ---------------------------------------------------------------------------


class CharacterManager:
    """基于 AstrBot KV 存储的角色卡管理器（多卡 + 活跃指针 + 显式索引）。

    本模块不做权限判断（删卡等由命令层控制）。写操作全部在单把
    管理器级锁内读-改-写；只读接口不加锁（同 InventoryManager）。
    """

    def __init__(self, star: Star) -> None:
        self._star = star
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 内部 KV 读写
    # ------------------------------------------------------------------

    @staticmethod
    def _card_key(origin: str, sender_id: str, name: str) -> str:
        return f"{_KV_PREFIX_CARD}{origin}:{sender_id}:{name}"

    @staticmethod
    def _index_key(origin: str, sender_id: str) -> str:
        return f"{_KV_PREFIX_INDEX}{origin}:{sender_id}"

    @staticmethod
    def _active_key(origin: str, sender_id: str) -> str:
        return f"{_KV_PREFIX_ACTIVE}{origin}:{sender_id}"

    async def _load_card(self, key: str) -> CharacterSheet | None:
        try:
            raw = await self._star.get_kv_data(key, None)
            if isinstance(raw, dict):
                return CharacterSheet.from_dict(raw)
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取角色卡失败: {e}")
        except Exception as e:  # noqa: BLE001 — KV 内容不可信，全部兜底
            logger.warning(f"[trpg_assistant] 读取角色卡时发生未预期异常: {e}")
        return None

    async def _save_card(self, key: str, sheet: CharacterSheet) -> None:
        try:
            await self._star.put_kv_data(key, sheet.to_dict())
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入角色卡失败: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 写入角色卡时发生未预期异常: {e}")

    async def _load_index(self, key: str) -> list[str]:
        try:
            raw = await self._star.get_kv_data(key, None)
            names = (raw or {}).get("names", []) if isinstance(raw, dict) else []
            return [n for n in names if isinstance(n, str) and n]
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取卡名索引失败: {e}")
        except Exception:  # noqa: BLE001
            pass
        return []

    async def _save_index(self, key: str, names: list[str]) -> None:
        try:
            await self._star.put_kv_data(key, {"names": names})
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入卡名索引失败: {e}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 写入卡名索引时发生未预期异常: {e}")

    async def _load_active(self, key: str) -> str | None:
        try:
            raw = await self._star.get_kv_data(key, None)
            if isinstance(raw, dict):
                name = raw.get("name")
                return _sanitize_card_name(name) if isinstance(name, str) and name else None
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取活跃卡失败: {e}")
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _save_active(self, key: str, name: str | None) -> None:
        try:
            if name is None:
                await self._star.delete_kv_data(key)
            else:
                await self._star.put_kv_data(key, {"name": name})
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入活跃卡失败: {e}")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _sender_of(event: AstrMessageEvent, sender_id: str | None = None) -> str:
        return (
            sender_id if sender_id is not None else str(event.get_sender_id())
        )

    # ------------------------------------------------------------------
    # 只读接口（不加锁）
    # ------------------------------------------------------------------

    async def get_card(
        self,
        event: AstrMessageEvent,
        name: str | None = None,
        sender_id: str | None = None,
    ) -> CharacterSheet | None:
        """读取角色卡；name 为空时读活跃卡。卡或活跃指针不存在返回 None。"""
        sid = self._sender_of(event, sender_id)
        if name is None:
            name = await self.get_active_name(event, sid)
            if name is None:
                return None
        return await self._load_card(self._card_key(event.unified_msg_origin, sid, name))

    async def list_cards(
        self, event: AstrMessageEvent, sender_id: str | None = None
    ) -> list[str]:
        """返回指定玩家（默认发送者）的卡名列表（按创建顺序）。"""
        sid = self._sender_of(event, sender_id)
        return await self._load_index(self._index_key(event.unified_msg_origin, sid))

    async def get_active_name(
        self, event: AstrMessageEvent, sender_id: str | None = None
    ) -> str | None:
        """返回活跃卡名；未设置返回 None。"""
        sid = self._sender_of(event, sender_id)
        return await self._load_active(self._active_key(event.unified_msg_origin, sid))

    # ------------------------------------------------------------------
    # 写接口（全部在 self._lock 内读-改-写）
    # ------------------------------------------------------------------

    async def set_active(
        self, event: AstrMessageEvent, name: str, sender_id: str | None = None
    ) -> bool:
        """将某张卡设为活跃；卡不存在返回 False。"""
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        clean = _sanitize_card_name(name)
        if not clean:
            return False
        async with self._lock:
            index = await self._load_index(self._index_key(origin, sid))
            if clean not in index:
                return False
            await self._save_active(self._active_key(origin, sid), clean)
            return True

    async def save_card(
        self,
        event: AstrMessageEvent,
        sheet: CharacterSheet,
        sender_id: str | None = None,
    ) -> str | None:
        """保存角色卡（新建/覆盖）。返回错误原因串，None 表示成功。

        新卡自动追加到索引；若该玩家尚无活跃卡则自动置为活跃。
        覆盖已有同名卡时索引不变、活跃指针不变。
        """
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        clean = _sanitize_card_name(sheet.name)
        if not clean:
            return "卡名不能为空。"
        async with self._lock:
            index = await self._load_index(self._index_key(origin, sid))
            is_new = clean not in index
            if is_new:
                index.append(clean)
                await self._save_index(self._index_key(origin, sid), index)
            sheet.name = clean
            await self._save_card(self._card_key(origin, sid, clean), sheet)
            if is_new:
                active = await self._load_active(self._active_key(origin, sid))
                if active is None:
                    await self._save_active(self._active_key(origin, sid), clean)
            return None

    async def delete_card(
        self, event: AstrMessageEvent, name: str, sender_id: str | None = None
    ) -> bool:
        """删除角色卡（同步维护索引与活跃指针）。卡不存在返回 False。"""
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        clean = _sanitize_card_name(name)
        async with self._lock:
            index = await self._load_index(self._index_key(origin, sid))
            if clean not in index:
                return False
            index.remove(clean)
            await self._save_index(self._index_key(origin, sid), index)
            await self._star.delete_kv_data(self._card_key(origin, sid, clean))
            active = await self._load_active(self._active_key(origin, sid))
            if active == clean:
                # 活跃卡被删：回退到索引第一张或清空
                if index:
                    await self._save_active(self._active_key(origin, sid), index[0])
                else:
                    await self._save_active(self._active_key(origin, sid), None)
            return True

    async def rename_card(
        self,
        event: AstrMessageEvent,
        old_name: str,
        new_name: str,
        sender_id: str | None = None,
    ) -> tuple[bool, str]:
        """重命名角色卡。返回 (是否成功, 失败原因/成功提示文案)。"""
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        old = _sanitize_card_name(old_name)
        new = _sanitize_card_name(new_name)
        if not old:
            return False, "卡名为空。"
        if not new:
            return False, "新卡名不能为空。"
        if old == new:
            return False, "新旧卡名相同。"
        async with self._lock:
            index = await self._load_index(self._index_key(origin, sid))
            if old not in index:
                return False, f"没有名为「{old}」的卡。"
            if new in index:
                return False, f"已存在名为「{new}」的卡，换个名字吧。"
            card = await self._load_card(self._card_key(origin, sid, old))
            if card is None:
                return False, f"没有名为「{old}」的卡。"
            card.name = new
            index[index.index(old)] = new
            await self._save_index(self._index_key(origin, sid), index)
            await self._star.delete_kv_data(self._card_key(origin, sid, old))
            await self._save_card(self._card_key(origin, sid, new), card)
            active = await self._load_active(self._active_key(origin, sid))
            if active == old:
                await self._save_active(self._active_key(origin, sid), new)
            return True, f"已将「{old}」改名为「{new}」。"

    async def update_fields(
        self,
        event: AstrMessageEvent,
        name: str | None,
        fields: dict,
        sender_id: str | None = None,
    ) -> tuple[CharacterSheet | None, list[str]]:
        """按字段白名单更新角色卡。返回 (更新后的卡, 已应用字段名列表)。

        支持字段（name 为空时作用于活跃卡）：
          str/dex/con/int/wis/cha（力量/敏捷/体质/智力/感知/魅力）
                          → 六维属性值直接设置（clamp 1-30，v0.41.0；
                             派生修正/先攻/HP/AC 等由命令层触发引擎重算）
          hp / ac / speed   → LayeredStat.bonus（整数值，v0.17 base 恒 0）
          slot1..slot9       → spell_slots[k].bonus
          attack             → 值形如「名称=加值」，写入 attack_bonuses；
                              「名称=-」删除该攻击条目（v0.31.0）
          main_hand/off_hand/armor → 装备槽文本（"-" 清除）
          background/race    → 背景/种族（短文本，≤40 字；v0.41.0 起支持 race）
          classes            → 职业整体替换，值形如「战士 3 + 法师（塑能） 2」
                              （"-" 清空职业，v0.41.0）
          edition            → 规则版本 2014/2024（5e/5.5e 归一，v0.41.0）
          backstory/alignment       → 自由文本
          skills / saves     → 值形如「察觉,隐匿」（逗号/空格分隔），整体覆盖熟练集
          expertise          → 同 skills，整体覆盖技能专精集（双倍熟练）
          feats              → 值形如「巨武器大师,幸运」，整体覆盖专长列表
          tools/weapons/armors → 值形如「简易武器,长剑」，整体覆盖工具/武器/防具熟练集
          languages            → 值形如「通用语,精灵语」（逗号/空格/顿号分隔），整体覆盖语言集
          deity/age/gender/height/weight → 人物基础信息（信仰/年龄/性别/身高/体重，自由文本）
          hit_dice_used        → 短休已用生命骰数（0-20）
          inspiration          → 激励（0 或 1）
          initiative           → 先攻房规额外加值（写 bonus；base 由规则引擎按敏捷修正重算）
          spells               → 已知法术，值形如「戏法:火焰箭,光亮术　1环:法师护甲,护盾术」
          named_roll         → 值形如「名称=表达式」，写入命名掷骰（/r 联动用）；
                              「名称=-」删除（v0.32.0）
        未知字段被忽略并记录。
        """
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        clean = _sanitize_card_name(name) if name else None
        async with self._lock:
            if clean is None:
                clean = await self._load_active(self._active_key(origin, sid))
                if clean is None:
                    return None, []
            card = await self._load_card(self._card_key(origin, sid, clean))
            if card is None:
                return None, []
            applied: list[str] = []
            for key, raw in fields.items():
                k = (key or "").strip().lower()
                if k in ABILITY_NAMES:
                    # v0.41.0：六维属性单独设置（力量/敏捷/体质/智力/感知/魅力），
                    # 直接覆盖属性值并 clamp 1-30；派生修正随显示即时更新，
                    # 战斗字段 base（先攻/HP/AC/攻击加值）由命令层随后触发重算。
                    current = card.ability_scores.get(k)
                    card.ability_scores.set(k, _to_int(raw, current, 1, 30))
                    applied.append(k)
                elif k in ("hp", "ac", "speed"):
                    stat = {
                        "hp": card.hp_max,
                        "ac": card.ac,
                        "speed": card.speed,
                    }[k]
                    stat.bonus = _to_int(raw, stat.bonus, -1000, 1000)
                    applied.append(k)
                elif k.startswith("slot") and k[4:].isdigit():
                    ring = k[4:]
                    stat = card.spell_slots.setdefault(ring, LayeredStat())
                    stat.bonus = _to_int(raw, stat.bonus, -1000, 1000)
                    applied.append(k)
                elif k == "attack":
                    # v0.31.0：值形如「名称=- / 名称=删」→ 删除该攻击条目；
                    # 否则「名称=加值」新增/覆盖（bonus 层）。
                    text = _sanitize_text(_to_str(raw), 100)
                    if "=" in text:
                        wname, _, wval = text.partition("=")
                        wname = wname.strip()
                        if wval.strip() in ("-", "－", "删", "删除"):
                            removed = card.attack_bonuses.pop(wname, None)
                            if removed is not None:
                                applied.append("attack")
                        else:
                            stat = card.attack_bonuses.setdefault(
                                wname, LayeredStat()
                            )
                            stat.bonus = _to_int(wval, stat.bonus, -1000, 1000)
                            applied.append("attack")
                    else:
                        card.attack_bonuses.setdefault(text, LayeredStat())
                        applied.append("attack")
                elif k in ("main_hand", "off_hand", "armor"):
                    if _to_str(raw).strip() == "-":
                        value = ""
                    else:
                        value = _sanitize_text(_to_str(raw), 40)
                    setattr(card.equipment, k, value)
                    applied.append(k)
                elif k in ("background", "backstory", "alignment", "race"):
                    # v0.41.0：race 与 background 同为短文本（≤40 字）
                    if k in ("background", "race"):
                        setattr(card, k, _sanitize_text(_to_str(raw), 40))
                    else:
                        setattr(card, k, _sanitize_text(_to_str(raw)))
                    applied.append(k)
                elif k == "classes":
                    # v0.41.0：职业整体替换，值形如「战士 3 + 法师（塑能） 2」；
                    # 「- / 无 / 删」清空职业（卡回退无职业态，等级 1）。
                    text = _to_str(raw).strip()
                    if text in ("-", "－", "无", "删", "删除"):
                        card.classes = []
                        applied.append(k)
                    else:
                        parsed = parse_classes_text(text)
                        if parsed:
                            card.classes = parsed
                            applied.append(k)
                elif k == "edition":
                    # v0.41.0：规则版本归一（2014/2024，兼容 5e/5.5e）；无法识别不应用
                    norm = normalize_edition(_to_str(raw))
                    if norm:
                        card.edition = norm
                        applied.append(k)
                elif k == "skills":
                    newset: set[str] = set()
                    for tok in re.split(r"[,\s，、]+", _to_str(raw)):
                        canon = SKILL_ALIAS.get(tok.strip().lower())
                        if canon and canon in SKILLS:
                            newset.add(canon)
                    card.skill_proficiencies = newset
                    applied.append("skills")
                elif k == "saves":
                    newset = set()
                    for tok in re.split(r"[,\s，、]+", _to_str(raw)):
                        ab = ABILITY_ALIAS.get(tok.strip().lower())
                        if ab:
                            newset.add(ab)
                    card.save_proficiencies = newset
                    applied.append("saves")
                elif k == "expertise":
                    newset = set()
                    for tok in re.split(r"[,\s，、]+", _to_str(raw)):
                        canon = SKILL_ALIAS.get(tok.strip().lower())
                        if canon and canon in SKILLS:
                            newset.add(canon)
                    card.skill_expertise = newset
                    applied.append("expertise")
                elif k == "feats":
                    items = [
                        _sanitize_text(t, 40)
                        for t in re.split(r"[,，、]+", _to_str(raw))
                    ]
                    card.feats = list(dict.fromkeys(t for t in items if t))
                    applied.append("feats")
                elif k in ("tools", "weapons", "armors"):
                    attr = {
                        "tools": "tool_proficiencies",
                        "weapons": "weapon_proficiencies",
                        "armors": "armor_proficiencies",
                    }[k]
                    items = [
                        _sanitize_text(t, 40)
                        for t in re.split(r"[,，、]+", _to_str(raw))
                    ]
                    setattr(card, attr, {t for t in items if t})
                    applied.append(k)
                elif k == "languages":
                    items = [
                        _sanitize_text(t, 40)
                        for t in re.split(r"[,，、\s]+", _to_str(raw))
                    ]
                    card.languages = {t for t in items if t}
                    applied.append("languages")
                elif k in ("deity", "age", "gender", "height", "weight"):
                    max_len = 40 if k == "deity" else 20
                    setattr(card, k, _sanitize_text(_to_str(raw), max_len))
                    applied.append(k)
                elif k == "hit_dice_used":
                    card.hit_dice_used = _to_int(raw, card.hit_dice_used, 0, 20)
                    applied.append(k)
                elif k == "inspiration":
                    card.inspiration = _to_int(raw, card.inspiration, 0, 1)
                    applied.append(k)
                elif k == "initiative":
                    # 先攻：房规额外加值写入 bonus（base 由规则引擎按敏捷修正重算）
                    card.initiative.bonus = _to_int(raw, card.initiative.bonus, -100, 100)
                    applied.append(k)
                elif k == "spells":
                    # 值形如「戏法:火焰箭,光亮术　1环:法师护甲,护盾术」，整体覆盖
                    card.spells = parse_spells_text(_to_str(raw))
                    applied.append(k)
                elif k == "named_roll":
                    # v0.32.0：「名称=- / 删」→ 删除该命名掷骰；否则「名称=表达式」新增/覆盖
                    text = _sanitize_text(_to_str(raw), 100)
                    if "=" in text:
                        nname, _, nexpr = text.partition("=")
                        nname = nname.strip()
                        if nexpr.strip() in ("-", "－", "删", "删除"):
                            removed = card.named_rolls.pop(nname, None)
                            if removed is not None:
                                applied.append("named_roll")
                        else:
                            card.named_rolls[nname] = nexpr.strip()
                            applied.append("named_roll")
            if applied:
                await self._save_card(self._card_key(origin, sid, clean), card)
            return card, applied

    async def level_up(
        self,
        event: AstrMessageEvent,
        name: str | None,
        class_name: str = "",
        sender_id: str | None = None,
        recalc_fn: Callable[[CharacterSheet], object] | None = None,
    ) -> tuple[CharacterSheet | None, object | None, str | None]:
        """指定职业 +1 级（缺省主职业；未有的职业追加为兼职），总等级 cap 20。

        recalc_fn 由命令层注入规则引擎重算（character.py 不反向依赖引擎）；
        重算失败不阻断升级。返回 (卡, 重算报告, 错误串)——失败时 (None, None, 原因)。
        """
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        clean = _sanitize_card_name(name) if name else None
        target = (class_name or "").strip()
        async with self._lock:
            if clean is None:
                clean = await self._load_active(self._active_key(origin, sid))
                if clean is None:
                    return None, None, "还没有角色卡，先车卡吧。"
            card = await self._load_card(self._card_key(origin, sid, clean))
            if card is None:
                return None, None, f"找不到角色卡「{clean}」。"
            if not card.classes:
                return None, None, "该卡还没有职业，无法升级。"
            if card.level >= 20:
                return None, None, "总等级已达上限 20，无法再升级。"
            cl = None
            if target:
                cl = next((c for c in card.classes if c.class_name == target), None)
            if target and cl is None:
                card.classes.append(ClassLevel(class_name=target, level=1))
                changed = target
            else:
                if cl is None:
                    cl = card.classes[0]
                if cl.level >= 20:
                    return None, None, f"「{cl.class_name}」已达 20 级，无法再升。"
                cl.level += 1
                changed = cl.class_name
            report = None
            if recalc_fn is not None:
                try:
                    report = recalc_fn(card)
                except Exception:  # noqa: BLE001 — 重算失败不阻断升级
                    report = None
            await self._save_card(self._card_key(origin, sid, clean), card)
            return card, report, None

    async def level_down(
        self,
        event: AstrMessageEvent,
        name: str | None,
        class_name: str = "",
        sender_id: str | None = None,
        recalc_fn: Callable[[CharacterSheet], object] | None = None,
    ) -> tuple[CharacterSheet | None, object | None, str | None]:
        """指定职业 -1 级（缺省主职业），单职业等级下限 1（v0.24.0）。

        recalc_fn 由命令层注入规则引擎重算（character.py 不反向依赖引擎）；
        重算失败不阻断降级。返回 (卡, 重算报告, 错误串)——失败时
        (None, None, 原因)。
        """
        sid = self._sender_of(event, sender_id)
        origin = event.unified_msg_origin
        clean = _sanitize_card_name(name) if name else None
        target = (class_name or "").strip()
        async with self._lock:
            if clean is None:
                clean = await self._load_active(self._active_key(origin, sid))
                if clean is None:
                    return None, None, "还没有角色卡，先车卡吧。"
            card = await self._load_card(self._card_key(origin, sid, clean))
            if card is None:
                return None, None, f"找不到角色卡「{clean}」。"
            if not card.classes:
                return None, None, "该卡还没有职业，无法降级。"
            cl = None
            if target:
                cl = next((c for c in card.classes if c.class_name == target), None)
            else:
                cl = card.classes[0]
            if cl is None:
                return None, None, f"卡上没有「{target}」这个职业。"
            if cl.level <= 1:
                return None, None, f"「{cl.class_name}」已是 1 级，无法再降。"
            cl.level -= 1
            report = None
            if recalc_fn is not None:
                try:
                    report = recalc_fn(card)
                except Exception:  # noqa: BLE001 — 重算失败不阻断降级
                    report = None
            await self._save_card(self._card_key(origin, sid, clean), card)
            return card, report, None

    # ------------------------------------------------------------------
    # 格式化辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_stat(stat: LayeredStat) -> str:
        """双层战斗字段的显示文本；bonus 为 0 时只显示 base。"""
        if stat.bonus == 0:
            return str(stat.base)
        return f"{stat.base}+{stat.bonus}" if stat.base else f"{stat.bonus}"

    @staticmethod
    def _derive_hit_dice(sheet: CharacterSheet, kb: object | None) -> str:
        """派生生命骰显示文本：各职业面值去重（D6/D8）+×总等级（v0.30.0）。

        面值来自知识库 class_combat.hd_faces（规则引擎同源）；kb 不可用或
        取不到返回空串（调用方省略该段，不影响卡面其余内容）。
        """
        if kb is None or not sheet.classes:
            return ""
        faces: list[int] = []
        for cl in sheet.classes:
            try:
                row = kb.class_combat(cl.class_name, sheet.edition)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — kb 查询失败不影响卡面
                row = None
            if row is not None and row.hd_faces and row.hd_faces not in faces:
                faces.append(row.hd_faces)
        if not faces:
            return ""
        return "D" + "/D".join(str(f) for f in faces) + f"×{sheet.level}"

    @staticmethod
    def format_sheet(sheet: CharacterSheet, kb: object | None = None) -> str:
        """将角色卡渲染为分节文本（摘要卡：长字段折叠，全文走 /卡 详情）。

        行序（v0.23.0 惯例）：名字 → HP/AC/速度/被动察觉 → 职业 →
        种族/背景/阵营 → 属性值 → 熟练/专精/专长 → 职业特性/种族特性
        （各折叠 8 条）→ 法术位/攻击 → 装备 → 生平摘要（50 字）。

        kb 可选：传入知识库管理器时追加「职业特性」「种族特性」段（只列
        名字）；kb 查询异常静默跳过，不影响主流程。
        """
        lines: list[str] = [f"📜 {sheet.name}（{sheet.edition}）"]
        # 战斗核心（v0.23.0 惯例：名字下先 HP/AC/速度/被动察觉，v0.30.0 加先攻）
        fight_core: list[str] = [f"HP {CharacterManager._fmt_stat(sheet.hp_max)}"]
        fight_core.append(f"AC {CharacterManager._fmt_stat(sheet.ac)}")
        fight_core.append(f"速度 {CharacterManager._fmt_stat(sheet.speed)}尺")
        fight_core.append(f"被动察觉 {sheet.passive_perception}")
        fight_core.append(f"先攻 {sheet.initiative.total:+d}")
        lines.append("　".join(fight_core))
        if sheet.classes:
            parts = []
            for c in sheet.classes:
                label = c.class_name + (f"（{c.subclass}）" if c.subclass else "")
                parts.append(f"{label} {c.level}")
            lines.append(f"职业：{' + '.join(parts)}")
        else:
            lines.append("职业：未设定")
        detail: list[str] = []
        if sheet.race:
            detail.append(f"种族 {sheet.race}")
        if sheet.background:
            detail.append(f"背景 {sheet.background}")
        if sheet.alignment:
            detail.append(f"阵营 {sheet.alignment}")
        if detail:
            lines.append("　".join(detail))
        # 人物信息（v0.30.0：性别/年龄/身高/体重/信仰，有值才显示）
        info_parts: list[str] = []
        if sheet.gender:
            info_parts.append(f"性别 {sheet.gender}")
        if sheet.age:
            info_parts.append(f"年龄 {sheet.age}")
        if sheet.height:
            info_parts.append(f"身高 {sheet.height}")
        if sheet.weight:
            info_parts.append(f"体重 {sheet.weight}")
        if sheet.deity:
            info_parts.append(f"信仰 {sheet.deity}")
        if info_parts:
            lines.append("人物：" + "　".join(info_parts))
        # 属性值表
        ab_lines = []
        for ab in ABILITY_NAMES:
            score = sheet.ability_scores.get(ab)
            mod = AbilityScores.modifier(score)
            ab_lines.append(f"{ABILITY_CN[ab]} {score}（{mod:+d}）")
        lines.append("属性值：" + "　".join(ab_lines))
        # 熟练
        prof_parts: list[str] = []
        if sheet.save_proficiencies:
            prof_parts.append(
                "豁免 " + "、".join(ABILITY_CN[a] for a in sorted(sheet.save_proficiencies))
            )
        if sheet.skill_proficiencies or sheet.skill_expertise:
            skill_names = []
            for s in sorted(sheet.skill_proficiencies | sheet.skill_expertise):
                name = SKILL_CN_REV.get(s, s)
                if s in sheet.skill_expertise:
                    name += "★"
                skill_names.append(name)
            prof_parts.append("技能 " + "、".join(skill_names))
        if sheet.tool_proficiencies:
            prof_parts.append("工具 " + "、".join(sorted(sheet.tool_proficiencies)))
        if sheet.weapon_proficiencies:
            prof_parts.append("武器 " + "、".join(sorted(sheet.weapon_proficiencies)))
        if sheet.armor_proficiencies:
            prof_parts.append("防具 " + "、".join(sorted(sheet.armor_proficiencies)))
        joat = sheet.jack_of_all_trades_bonus()
        if joat:
            prof_parts.append(f"万事通 +{joat}")
        if prof_parts:
            lines.append("熟练：" + "　".join(prof_parts))
        if sheet.languages:
            lines.append("语言：" + "、".join(sorted(sheet.languages)))
        if sheet.skill_expertise:
            lines.append(
                "专精：" + "、".join(
                    SKILL_CN_REV.get(s, s) for s in sorted(sheet.skill_expertise)
                ) + "（★技能双倍熟练）"
            )
        if sheet.feats:
            if len(sheet.feats) > 6:
                lines.append(
                    "专长：" + "、".join(sheet.feats[:6]) + f" …等 {len(sheet.feats)} 项"
                )
            else:
                lines.append("专长：" + "、".join(sheet.feats))
        # 职业特性（只列名字，按卡上职业等级过滤，截断 20 条）
        if kb is not None and sheet.classes:
            try:
                feature_names: list[str] = []
                seen: set[str] = set()
                for c in sheet.classes:
                    rows: list[tuple[int, str]] = []
                    result = kb.class_features(c.class_name, c.subclass or None)  # type: ignore[attr-defined]
                    for row in list(result.base_rows) + list(result.subclass_rows):
                        if row.level <= c.level:
                            rows.append((row.level, row.name))
                    for _, fname in sorted(rows, key=lambda t: (t[0], t[1])):
                        if fname not in seen:
                            seen.add(fname)
                            feature_names.append(fname)
                if feature_names:
                    shown = feature_names[:8]
                    suffix = (
                        f" …等 {len(feature_names)} 项"
                        if len(feature_names) > 8
                        else ""
                    )
                    lines.append("职业特性：" + "、".join(shown) + suffix)
            except Exception:  # noqa: BLE001 — kb 不可用不影响卡片展示
                pass
        # 种族特性（v0.23.0：kb 按种族提取特性名，折叠 8 条）
        if kb is not None and sheet.race:
            try:
                race_feats = kb.race_features(sheet.race, sheet.edition)  # type: ignore[attr-defined]
                if race_feats:
                    shown = race_feats[:8]
                    suffix = (
                        f" …等 {len(race_feats)} 项" if len(race_feats) > 8 else ""
                    )
                    lines.append("种族特性：" + "、".join(shown) + suffix)
            except Exception:  # noqa: BLE001 — kb 不可用不影响卡片展示
                pass
        # 战斗扩展（法术位/攻击；HP/AC/速度已前置）
        fight: list[str] = []
        if sheet.spell_slots:
            pact_lv = sheet.spell_slots.get("pact_level")
            slot_parts = []
            for k, v in sorted(sheet.spell_slots.items()):
                if k == "pact":
                    if pact_lv and pact_lv.total:
                        slot_parts.append(
                            f"短休位{CharacterManager._fmt_stat(v)}×{pact_lv.total}环"
                        )
                    else:
                        slot_parts.append(f"短休位{CharacterManager._fmt_stat(v)}")
                elif k == "pact_level":
                    continue
                else:
                    slot_parts.append(f"{k}环:{CharacterManager._fmt_stat(v)}")
            fight.append(f"法术位 {'　'.join(slot_parts)}")
        if sheet.attack_bonuses:
            atk_items = list(sheet.attack_bonuses.items())
            if len(atk_items) > 8:
                atks = "　".join(
                    f"{n}:{CharacterManager._fmt_stat(v)}" for n, v in atk_items[:8]
                ) + f" …等 {len(atk_items)} 项"
            else:
                atks = "　".join(
                    f"{n}:{CharacterManager._fmt_stat(v)}" for n, v in atk_items
                )
            fight.append(f"攻击 {atks}")
        if fight:
            lines.append("　".join(fight))
        # 资源（v0.30.0：生命骰派生显示 + 短休已用 + 激励）
        res_parts: list[str] = []
        hd_text = CharacterManager._derive_hit_dice(sheet, kb)
        if hd_text:
            used = min(sheet.hit_dice_used, sheet.level)
            if used:
                res_parts.append(f"生命骰 {hd_text}（短休已用 {used}/{sheet.level}）")
            else:
                res_parts.append(f"生命骰 {hd_text}")
        if sheet.inspiration:
            res_parts.append(f"激励 {sheet.inspiration}/1")
        if res_parts:
            lines.append("资源：" + "　".join(res_parts))
        # 已知法术（v0.30.0：折叠为环阶统计，全文走 /卡 详情 法术）
        if sheet.spells:
            spell_parts: list[str] = []
            for ring, names in sheet.spells.items():
                spell_parts.append(f"{_spell_ring_label(ring)} {len(names)} 个")
            lines.append("已知法术：" + "　".join(spell_parts))
        # 装备槽
        eq: list[str] = []
        if sheet.equipment.main_hand:
            eq.append(f"主手 {sheet.equipment.main_hand}")
        if sheet.equipment.off_hand:
            eq.append(f"副手 {sheet.equipment.off_hand}")
        if sheet.equipment.armor:
            eq.append(f"护甲 {sheet.equipment.armor}")
        if eq:
            lines.append("装备：" + "　".join(eq))
        # 生平摘要
        if sheet.backstory:
            summary = sheet.backstory.replace("\n", " ")
            lines.append(
                f"生平：{summary[:50]}{'…' if len(summary) > 50 else ''}"
            )
        return "\n".join(lines)

    @staticmethod
    def format_card_list(names: list[str], active: str | None) -> str:
        """将卡名列表渲染为文本，标注活跃卡。"""
        if not names:
            return "还没有角色卡。"
        lines: list[str] = [f"共 {len(names)} 张角色卡："]
        for idx, name in enumerate(names, 1):
            mark = "⭐ " if name == active else ""
            lines.append(f"{idx}. {mark}{name}")
        return "\n".join(lines)

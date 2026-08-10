"""
chargen.py — 开卡规则（Chargen Rule）与车卡引导状态机。

开卡规则是群级配置：DM/管理员设置（版本、属性生成方式、子职时机、起始等级），
全团玩家车卡时统一遵守。属性生成方式为参数化模板：

- 购点法（point_buy）：点数池 + 属性下限/上限，成本表固定（8=0 … 15=9）；
- 掷骰法（roll）：骰式 + 次数，由插件代骰（v0.17 通过注入的 roll_fn 执行，
  保证经过插件骰子限制并写入投掷历史）；
- 标准数组（standard_array）：预置 6 个值自由分配。

内置别名注册表 ABILITY_GEN_ALIASES：27buy / 32buy / dnd5 / 标准数组，
后续版本可继续追加条目。

引导状态机（混合式）：插件维护车卡草稿（KV），LLM 通过 llm_tool 读写推进。
工具返回驱动：每次调用返回【进度】【校验】【下一问】三段式文本，LLM 只做
话术包装、一次只问一个问题。校验硬拒绝：不合规不推进，特许路径 = DM 改群规则。

版本路径：
- 2014：CONFIRM → RACE → CLASS → BACKGROUND → [ABILITY_METHOD] → ABILITY_ASSIGN
  → [ABILITY_BONUS] → ALIGNMENT …
- 2024：CONFIRM → CLASS → ORIGIN_BG → ORIGIN_SPECIES → [ABILITY_METHOD] → ABILITY_ASSIGN
  → ABILITY_BONUS → ALIGNMENT …
（ABILITY_METHOD 仅当群规则为掷骰法时插入，由插件代骰；
ABILITY_BONUS 仅当种族/背景含 choose 加值（半精灵式/2024 背景）时进入，
v0.18 起加值叠加全自动，落库时由规则引擎一并重算战斗字段。）

依赖方向：chargen → character 单向；不 import main（掷骰经注入回调）。
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from astrbot.api import logger

from .character import (
    ABILITY_CN,
    ABILITY_CN_REV,
    ABILITY_NAMES,
    AbilityScores,
    CharacterManager,
    CharacterSheet,
    ClassLevel,
    LayeredStat,
    _sanitize_card_name,
    _sanitize_text,
)
from .kb import ChooseSpec  # 仅类型注解与方案描述（不触发 db 连接）

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Star

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

_KV_PREFIX_RULE = "chargen_rule:"  # chargen_rule:{origin}
_KV_PREFIX_DRAFT = "character:draft:"  # character:draft:{origin}:{sender}

# 购点成本表（2014/2024 一致）：属性值 → 花费点数。
POINT_BUY_COST: dict[int, int] = {
    8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9,
}

# 属性分配解析：中文单字 → 属性缩写（顺序无关）。
_ABILITY_CN_SINGLE: dict[str, str] = {
    "力": "str", "敏": "dex", "体": "con", "智": "int", "感": "wis", "魅": "cha",
}
_RE_EXPLICIT = re.compile(r"^([力敏体智感魅]|力量|敏捷|体质|智力|感知|魅力)(\d{1,2})$")

# XGE「这是你的人生」三段引导 prompt（纯引导文本，不含随机表）。
XGE_ORIGIN_PROMPT = (
    "请按 XGE「这是你的人生」的「出身」环节提问（一次只问一个问题）："
    "双亲的身份与职业、出生地、兄弟姐妹、家庭状况、童年的家与记忆。"
    "玩家自由作答，你负责把答案整理进生平。"
)
XGE_DECISION_PROMPT = (
    "请按「个人决定」环节提问（一次只问一个问题）：是什么理由让这个角色"
    "选择了当前职业与背景？可以结合 TA 选的种族/职业给一两个例子启发。"
)
XGE_EVENT_PROMPT = (
    "请按「人生经历」环节提问（一次只问一个问题）：成年后经历过的关键事件——"
    "值得铭记的遭遇、交下的敌人（宿敌）或朋友、改变人生的转折。"
    "不需要掷随机表，凭玩家的想象自由发挥即可。"
)

# 引导状态常量（字符串即 KV 存储值）。
S_IDLE = "IDLE"
S_CONFIRM = "CONFIRM"
S_RACE = "RACE"
S_CLASS = "CLASS"
S_BACKGROUND = "BACKGROUND"
S_ORIGIN_BG = "ORIGIN_BG"
S_ORIGIN_SPECIES = "ORIGIN_SPECIES"
S_ABILITY_METHOD = "ABILITY_METHOD"
S_ABILITY_ASSIGN = "ABILITY_ASSIGN"
S_ABILITY_BONUS = "ABILITY_BONUS"  # v0.18：种族/背景 choose 加值选择（无 choose 时自动跳过）
S_ALIGNMENT = "ALIGNMENT"
S_BACKSTORY_ORIGIN = "BACKSTORY_ORIGIN"
S_BACKSTORY_DECISION = "BACKSTORY_DECISION"
S_BACKSTORY_EVENT = "BACKSTORY_EVENT"
S_NAME = "NAME"
S_DONE = "DONE"

# 取消指令别名（advance 输入命中即中止引导）。
_CANCEL_ALIASES = {"取消", "cancel", "abort", "停止"}

# v0.35.0 预填提示用语：KB kind → 用户可见中文名。
_KIND_CN: dict[str, str] = {
    "race": "种族", "class": "职业", "background": "背景",
}

# /车卡规则 设置项中文名（按长度降序：parse_rule_edit 紧凑写法前缀拆分用，
# 防「属性生成X」被「属性」抢先拆）。
_RULE_CN_KEYS = ("起始金币", "起始等级", "子职时机", "属性生成", "属性", "版本", "重置")


# ---------------------------------------------------------------------------
# 开卡规则数据模型
# ---------------------------------------------------------------------------


@dataclass
class AbilityGenMethod:
    """属性生成方式（参数化模板）。"""

    kind: str = "point_buy"  # "point_buy" | "roll" | "standard_array"
    pool: int = 27  # point_buy：点数池
    min_score: int = 8  # point_buy：属性下限
    max_score: int = 15  # point_buy：属性上限
    expr: str = "4d6kh3"  # roll：骰式（经 dice_parser 预校验）
    count: int = 6  # roll：掷骰次数
    array: list[int] = field(default_factory=lambda: [15, 14, 13, 12, 10, 8])

    def __post_init__(self) -> None:
        if self.kind not in ("point_buy", "roll", "standard_array"):
            self.kind = "point_buy"
        self.pool = max(1, min(1000, int(self.pool)))
        self.min_score = max(1, min(30, int(self.min_score)))
        self.max_score = max(1, min(30, int(self.max_score)))
        if self.max_score < self.min_score:
            self.min_score, self.max_score = self.max_score, self.min_score
        self.expr = _sanitize_text(self.expr, 40) or "4d6kh3"
        self.count = max(1, min(20, int(self.count)))
        if not isinstance(self.array, list) or not self.array:
            self.array = [15, 14, 13, 12, 10, 8]

    def describe(self) -> str:
        """属性生成方式的中文描述。"""
        if self.kind == "point_buy":
            return f"购点法（{self.pool} 点，{self.min_score}-{self.max_score}）"
        if self.kind == "roll":
            return f"掷骰法（{self.expr}×{self.count}，插件代骰）"
        return "标准数组（" + "、".join(str(v) for v in self.array[:6]) + "）"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "pool": self.pool,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "expr": self.expr,
            "count": self.count,
            "array": list(self.array[:6]),
        }

    @classmethod
    def from_dict(cls, data: dict) -> AbilityGenMethod:
        if not isinstance(data, dict):
            return cls()
        try:
            array = [int(v) for v in data.get("array", [])][:6]
        except (TypeError, ValueError):
            array = []
        return cls(
            kind=str(data.get("kind", "point_buy")),
            pool=int(data.get("pool", 27) or 27),
            min_score=int(data.get("min_score", 8) or 8),
            max_score=int(data.get("max_score", 15) or 15),
            expr=str(data.get("expr", "4d6kh3") or "4d6kh3"),
            count=int(data.get("count", 6) or 6),
            array=array,
        )


@dataclass
class ChargenRule:
    """群级开卡规则。"""

    edition: str = "2014"  # "2014" | "2024"
    ability: AbilityGenMethod = field(default_factory=AbilityGenMethod)
    subclass_at_creation: str = "auto"  # "auto"（按规则等级）| "on"（开卡即选）| "off"（不选）
    starting_level: int = 1  # 起始等级 1-20
    starting_gold: str = "auto"  # 起始金币（金币单位）：「auto」按职业默认代骰 |
    # 纯数字 = 全团固定金币数（如 150）；骰式 = DM 自定义随机财富（如 5d4×10）

    def __post_init__(self) -> None:
        if self.edition not in ("2014", "2024"):
            self.edition = "2014"
        if self.subclass_at_creation not in ("auto", "on", "off"):
            self.subclass_at_creation = "auto"
        self.starting_level = max(1, min(20, int(self.starting_level)))
        self.starting_gold = _normalize_gold_rule(self.starting_gold)

    def format(self) -> str:
        """规则的中文展示文本。"""
        sub_map = {"auto": "按规则等级", "on": "开卡时确定", "off": "车卡时不选"}
        return (
            f"开卡规则（群级）：版本 {self.edition} ｜ "
            f"属性生成：{self.ability.describe()} ｜ "
            f"子职：{sub_map[self.subclass_at_creation]} ｜ "
            f"起始等级 {self.starting_level} ｜ "
            f"起始金币：{format_gold_rule(self.starting_gold)}"
        )

    def to_dict(self) -> dict:
        return {
            "edition": self.edition,
            "ability": self.ability.to_dict(),
            "subclass_at_creation": self.subclass_at_creation,
            "starting_level": self.starting_level,
            "starting_gold": self.starting_gold,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChargenRule:
        if not isinstance(data, dict):
            return cls()
        return cls(
            edition=str(data.get("edition", "2014")),
            ability=AbilityGenMethod.from_dict(data.get("ability")),
            subclass_at_creation=str(data.get("subclass_at_creation", "auto")),
            starting_level=int(data.get("starting_level", 1) or 1),
            starting_gold=str(data.get("starting_gold", "auto") or "auto"),
        )


def _normalize_gold_rule(value: object) -> str:
    """起始金币规则规范化：合法值「auto / 纯数字（金币）/ 骰式」，否则回退 auto。"""
    text = str(value or "").strip().lower()
    if not text or text in ("auto", "自动"):
        return "auto"
    if text.isdigit():
        return str(max(1, min(int(text), 99999)))
    # 骰式：含骰子标记即视为自定义骰式（表达式合法性由命令层 validate_expr 把关）
    if re.search(r"[dD]", text):
        return text
    return "auto"


def format_gold_rule(gold_rule: str) -> str:
    """起始金币规则的中文展示。"""
    rule = (gold_rule or "auto").strip().lower()
    if rule == "auto":
        return "按职业（自动）"
    if rule.isdigit():
        return f"固定 {int(rule)} 金币"
    return f"骰式 {rule}（金币）"


def _split_gold_expr(expr: str) -> tuple[str, int]:
    """拆分「5d4 × 10」→ (骰式 5d4, 乘数 10)；无乘数时乘数为 1。"""
    text = (expr or "").strip().replace("×", "x").replace("X", "x")
    m = re.match(r"(.+?)\s*x\s*(\d+)$", text)
    if m:
        return m.group(1).strip(), max(1, int(m.group(2)))
    return text, 1


# 内置属性生成方式别名注册表（值是 AbilityGenMethod 工厂，可扩展）。
ABILITY_GEN_ALIASES: dict[str, AbilityGenMethod] = {
    "27buy": AbilityGenMethod(kind="point_buy", pool=27, min_score=8, max_score=15),
    "32buy": AbilityGenMethod(kind="point_buy", pool=32, min_score=8, max_score=15),
    "dnd5": AbilityGenMethod(kind="roll", expr="4d6kh3", count=6),
    "标准数组": AbilityGenMethod(kind="standard_array"),
    "standard": AbilityGenMethod(kind="standard_array"),
}


def parse_rule_edit(
    rule: ChargenRule, tokens: list[str], validate_expr: Callable[[str], bool] | None = None
) -> tuple[ChargenRule | None, str]:
    """按 /车卡规则 语法修改规则。

    tokens 来自命令层分词。返回 (新规则或 None, 消息文本)：
    - 修改成功 → (新规则, 规则展示文本)；
    - 失败 → (None, 错误文案)。

    容错：支持紧凑写法「版本2024」「子职时机开」「起始等级3」「属性购点」——
    设置项与值之间无空格时按设置项前缀拆分（仅拆一层）。
    """
    if not tokens:
        return None, rule.format()

    sub = tokens[0].strip().lower()
    rest = tokens[1:]

    # 紧凑写法容错：tokens[0] 整体不是设置项时，按设置项前缀拆分
    # （「版本2024」→ 版本 + 2024；中文设置项按长度降序，防「属性生成X」
    # 被「属性」抢先拆；值内的深层紧凑如「属性购点池=32」不处理）。
    if sub not in (
        "版本", "edition", "ruleversion",
        "属性", "ability", "属性生成",
        "子职时机", "subclass",
        "起始等级", "startlevel", "level",
        "起始金币", "startgold", "gold",
        "重置", "reset", "default",
    ):
        for key in _RULE_CN_KEYS:
            if tokens[0].startswith(key):
                remainder = tokens[0][len(key):]
                if remainder:
                    sub = key
                    rest = [remainder, *rest]
                    break

    if sub in ("版本", "edition", "ruleversion"):
        if not rest:
            return None, "用法：车卡规则 版本 2014|2024"
        val = rest[0].strip().upper()
        if val not in ("2014", "2024"):
            return None, f"无效的版本 '{rest[0]}'，仅支持 2014 或 2024。"
        rule.edition = val
        return rule, rule.format()

    if sub in ("属性", "ability", "属性生成"):
        if not rest:
            return None, "用法：车卡规则 属性 <别名|购点|掷骰>（27buy/32buy/dnd5/标准数组）"
        style = rest[0].strip().lower()
        if style in ABILITY_GEN_ALIASES:
            rule.ability = ABILITY_GEN_ALIASES[style]
            return rule, rule.format()
        if style in ("购点", "pointbuy", "point_buy"):
            return _parse_custom_point_buy(rule, rest[1:])
        if style in ("掷骰", "roll", "rollscores"):
            return _parse_custom_roll(rule, rest[1:], validate_expr)
        return None, (
            f"未知的属性生成方式 '{rest[0]}'。可用：27buy / 32buy / dnd5 / 标准数组，"
            "或自定义：购点 池=点数 [下限=] [上限=]、掷骰 <骰式> [次数]。"
        )

    if sub in ("子职时机", "subclass"):
        if not rest:
            return None, "用法：车卡规则 子职时机 开|关|按规则"
        val = rest[0].strip().lower()
        mapping = {"开": "on", "on": "on", "关": "off", "off": "off", "按规则": "auto", "auto": "auto"}
        if val not in mapping:
            return None, "无效的子职时机，仅支持：开 / 关 / 按规则。"
        rule.subclass_at_creation = mapping[val]
        return rule, rule.format()

    if sub in ("起始等级", "startlevel", "level"):
        if not rest:
            return None, "用法：车卡规则 起始等级 1-20"
        try:
            level = int(rest[0])
        except ValueError:
            return None, f"无效的起始等级 '{rest[0]}'，应为 1-20 的整数。"
        if not 1 <= level <= 20:
            return None, f"无效的起始等级 '{rest[0]}'，应为 1-20 的整数。"
        rule.starting_level = level
        return rule, rule.format()

    if sub in ("起始金币", "startgold", "gold"):
        if not rest:
            return None, "用法：车卡规则 起始金币 <自动|金额|骰式>（如 150 / 5d4×10）"
        val = rest[0].strip().lower()
        if val in ("自动", "auto"):
            rule.starting_gold = "auto"
            return rule, rule.format()
        if val.isdigit():
            rule.starting_gold = str(max(1, min(int(val), 99999)))
            return rule, rule.format()
        # 骰式：必须含骰子标记；主表达式（乘数前）用掷骰校验器把关
        if re.search(r"[dD]", val):
            dice, _mult = _split_gold_expr(val)
            if validate_expr is None or validate_expr(dice):
                rule.starting_gold = _normalize_gold_rule(val)
                return rule, rule.format()
        return None, (
            f"无效的起始金币 '{rest[0]}'。可用：自动（按职业代骰）、纯数字金额"
            "（金币），或骰式（如 5d4×10）。"
        )

    if sub in ("重置", "reset", "default"):
        return ChargenRule(), ChargenRule().format()

    return None, (
        f"未知的设置项 '{tokens[0]}'。可用：版本 / 属性 / 子职时机 / 起始等级 / 起始金币 / 重置。"
    )


def _parse_custom_point_buy(rule: ChargenRule, kv_tokens: list[str]) -> tuple[ChargenRule | None, str]:
    """解析「属性 购点 池=32 [下限=8] [上限=15]」。"""
    method = AbilityGenMethod(kind="point_buy")
    seen_pool = False
    for tok in kv_tokens:
        key, sep, raw = tok.partition("=")
        if not sep or key.strip().lower() not in ("池", "pool", "下限", "min", "上限", "max"):
            return None, f"无法识别的购点参数 '{tok}'（应为 池=、下限=、上限=）。"
        try:
            num = int(raw)
        except ValueError:
            return None, f"无效的数值 '{raw}'。"
        k = key.strip().lower()
        if k in ("池", "pool"):
            if not 1 <= num <= 1000:
                return None, "点数池应在 1-1000 之间。"
            method.pool = num
            seen_pool = True
        elif k in ("下限", "min"):
            method.min_score = max(1, min(30, num))
        else:
            method.max_score = max(1, min(30, num))
    if not seen_pool:
        return None, "请指定点数池，例如：属性 购点 池=32。"
    if method.max_score < method.min_score:
        return None, "属性上限不能小于下限。"
    rule.ability = method
    return rule, rule.format()


def _parse_custom_roll(
    rule: ChargenRule, rest: list[str], validate_expr: Callable[[str], bool] | None
) -> tuple[ChargenRule | None, str]:
    """解析「属性 掷骰 <骰式> [次数]」。骰式先经校验回调预校验。"""
    if not rest:
        return None, "用法：属性 掷骰 <骰式> [次数]，如：属性 掷骰 4d6kh3 6"
    expr = rest[0].strip()
    if validate_expr is not None and not validate_expr(expr):
        return None, f"骰式 '{expr}' 无法解析，请检查语法（如 4d6kh3、5d6dl1）。"
    count = 6
    if len(rest) > 1:
        try:
            count = int(rest[1])
        except ValueError:
            return None, f"无效的次数 '{rest[1]}'，应为 1-20 的整数。"
        if not 1 <= count <= 20:
            return None, f"无效的次数 '{rest[1]}'，应为 1-20 的整数。"
    rule.ability = AbilityGenMethod(kind="roll", expr=expr, count=count)
    return rule, rule.format()


# ---------------------------------------------------------------------------
# 属性分配校验器（纯函数）
# ---------------------------------------------------------------------------


def _scores_to_dict(scores: list[int]) -> dict[str, int] | None:
    """六维有序列表 → {缩写: 值}；长度不为 6 或含非数字返回 None。"""
    if len(scores) != 6:
        return None
    try:
        values = [int(v) for v in scores]
    except (TypeError, ValueError):
        return None
    return dict(zip(ABILITY_NAMES, values))


def validate_point_buy(scores: list[int], method: AbilityGenMethod) -> str | None:
    """购点法校验：长度 6、每项在 [min, max]、总花费不超过点数池。

    返回错误文案，通过返回 None。
    """
    mapping = _scores_to_dict(scores)
    if mapping is None:
        return "需要恰好 6 个属性值（力量/敏捷/体质/智力/感知/魅力）。"
    costs = 0
    for ab, value in mapping.items():
        cost = POINT_BUY_COST.get(value)
        if cost is None:
            return (
                f"{ABILITY_CN[ab]} {value} 超出购点范围 "
                f"{method.min_score}-{method.max_score}（成本表仅覆盖 8-15）。"
            )
        costs += cost
    if costs > method.pool:
        return (
            f"购点共花费 {costs} 点，超过群规则上限 {method.pool} 点，"
            "不合规无法保存。可重新分配，或请 DM 用「/车卡规则 属性」调整。"
        )
    return None


def validate_standard_array(scores: list[int], method: AbilityGenMethod) -> str | None:
    """标准数组校验：六个数必须与预设数组多重集一致。"""
    if len(scores) != 6:
        return "需要恰好 6 个属性值（力量/敏捷/体质/智力/感知/魅力）。"
    expected = Counter(method.array[:6])
    actual = Counter(int(v) for v in scores)
    if actual != expected:
        return (
            "标准数组要求六个数为 " + "、".join(str(v) for v in method.array[:6]) +
            "（各用一次，自由分配）。你给的是 " + "、".join(str(v) for v in scores) + "。"
        )
    return None


def validate_rolled_assign(scores: list[int], rolled_pool: list[int]) -> str | None:
    """掷骰法分配校验：只能原样使用插件代骰出的池子，禁止自报数字。"""
    if len(scores) != 6:
        return "需要恰好 6 个属性值（力量/敏捷/体质/智力/感知/魅力）。"
    if Counter(int(v) for v in scores) != Counter(rolled_pool):
        return (
            "掷骰法必须原样分配插件代骰出的六个数值："
            + "、".join(str(v) for v in sorted(rolled_pool, reverse=True))
            + "（各用一次）。不能自报或修改数字。"
        )
    return None


def parse_ability_input(text: str, assign: str = "") -> tuple[list[int] | None, str]:
    """解析属性分配输入。

    支持两种格式（顺序无关）：
      1. 6 个裸数字：「15 14 13 12 10 8」→ 按 力/敏/体/智/感/魅 顺序；
      2. 显式映射：「力15 敏14 体13 智12 感10 魅8」（assign 参数优先）。
    返回 (六维有序列表或 None, 错误文案)。
    """
    src = (assign or "").strip() or (text or "").strip()
    if not src:
        return None, "请提供 6 个属性值，例如：15 14 13 12 10 8。"

    # 显式映射：含中文属性单字/全名前缀的 token
    mapping: dict[str, int] = {}
    order: list[str] = []
    bare: list[int] = []
    for token in src.replace("，", " ").replace(",", " ").split():
        m = _RE_EXPLICIT.match(token.strip())
        if m:
            ab = _ABILITY_CN_SINGLE.get(m.group(1)) or ABILITY_CN_REV.get(m.group(1))
            if ab is None or ab in mapping:
                return None, f"属性「{token}」重复或无法识别。"
            mapping[ab] = int(m.group(2))
            order.append(ab)
        else:
            try:
                bare.append(int(token))
            except ValueError:
                return None, f"无法识别的输入「{token}」，应为 6 个数字或「力15 敏14…」形式。"

    if order:
        if len(mapping) != 6:
            missing = "、".join(ABILITY_CN[a] for a in ABILITY_NAMES if a not in mapping)
            return None, f"属性映射不完整，还缺：{missing}。"
        return [mapping[ab] for ab in ABILITY_NAMES], ""

    if len(bare) != 6:
        return None, "需要恰好 6 个属性值（力量/敏捷/体质/智力/感知/魅力）。"
    return bare, ""


_RE_BONUS = re.compile(
    r"^([力敏体智感魅]|力量|敏捷|体质|智力|感知|魅力|str|dex|con|int|wis|cha)"
    r"([+]?\d{1,2})$",
    re.IGNORECASE,
)


def parse_bonus_choice(
    text: str, chooses: list[ChooseSpec]
) -> tuple[dict[str, int] | None, str]:
    """解析种族/背景加值选择输入 → ({属性: 数值}, 错误)。

    输入格式：「力+2 敏+1」「力量2 敏捷1」「str+2 dex 1」（顺序无关）。
    校验：所选 (属性, 数值) 多重集必须**恰好匹配一个方案**（choose spec）——
      - count 型（2014 半精灵式）：选 count 个不同属性各 +1，属性 ∈ from_set；
      - weighted 型（2024 背景式）：数值多重集 == weights 多重集，属性互异且 ∈ from_set。
    失败返回错误文案并列出全部可选方案（含正例）。
    """
    src = (text or "").strip()
    if not src:
        return None, "请选择加值方案，例如：力+2 敏+1。"
    picks: dict[str, int] = {}
    for token in src.replace("，", " ").replace(",", " ").split():
        m = _RE_BONUS.match(token.strip())
        if not m:
            return None, f"无法识别的加值「{token}」，应为「力+2 敏+1」形式。"
        ab = (
            _ABILITY_CN_SINGLE.get(m.group(1))
            or ABILITY_CN_REV.get(m.group(1))
            or m.group(1).lower()
        )
        if ab not in ABILITY_NAMES:
            return None, f"无法识别的属性「{m.group(1)}」。"
        if ab in picks:
            return None, f"属性「{ABILITY_CN[ab]}」不能重复选择。"
        picks[ab] = int(m.group(2))
    if not picks:
        return None, "请至少选择一个属性加值。"
    for spec in chooses:
        if _matches_choose_scheme(picks, spec):
            return picks, ""
    lines = [
        f"方案 {i}：{_describe_choose(spec)}"
        for i, spec in enumerate(chooses, 1)
    ]
    return None, "所选加值不匹配任何可选方案，请重选：\n" + "\n".join(lines)


def _matches_choose_scheme(picks: dict[str, int], spec: ChooseSpec) -> bool:
    """玩家的加值选择是否恰好匹配一个方案。"""
    if not picks:
        return False
    for ab, amount in picks.items():
        if ab not in spec.from_set or amount <= 0:
            return False
    amounts = sorted(picks.values())
    if spec.kind == "count":
        return len(picks) == spec.count and all(a == 1 for a in amounts)
    if spec.kind == "weighted":
        return amounts == sorted(spec.weights)
    return False


def _describe_choose(spec: ChooseSpec) -> str:
    """方案的人类可读描述（供提问与错误文案）。"""
    ab_list = "、".join(ABILITY_CN[a] for a in spec.from_set)
    if spec.kind == "count":
        return f"从 {ab_list} 中选 {spec.count} 项，各 +1"
    w = "、".join(f"+{x}" for x in spec.weights)
    return f"从 {ab_list} 中分配 {w}（每项给不同属性）"


# ---------------------------------------------------------------------------
# 引导状态机
# ---------------------------------------------------------------------------


@dataclass
class ChargenDraft:
    """车卡草稿：状态机的 KV 中间态，落库后删除。"""

    state: str = S_IDLE
    edition: str = "2014"
    data: dict = field(default_factory=dict)  # race/class_name/subclass/background/species/alignment/name
    ability_pool: list[int] = field(default_factory=list)  # 掷骰法代骰结果
    ability_detail: str = ""  # 代骰明细文本（入投掷历史）
    ability_assign: list[int] = field(default_factory=list)  # 加值前分配（六维顺序）
    ability_bonus: dict = field(default_factory=dict)  # v0.18：choose 加值选择 {"str": 2, ...}
    backstory_parts: dict = field(default_factory=dict)  # origin/decision/event
    starting_level: int = 1

    def __post_init__(self) -> None:
        if self.edition not in ("2014", "2024"):
            self.edition = "2014"
        if not isinstance(self.data, dict):
            self.data = {}
        self.starting_level = max(1, min(20, int(self.starting_level)))

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "edition": self.edition,
            "data": dict(self.data),
            "ability_pool": list(self.ability_pool),
            "ability_detail": self.ability_detail,
            "ability_assign": list(self.ability_assign),
            "ability_bonus": dict(self.ability_bonus),
            "backstory_parts": dict(self.backstory_parts),
            "starting_level": self.starting_level,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChargenDraft:
        if not isinstance(data, dict):
            return cls()
        return cls(
            state=str(data.get("state", S_IDLE)),
            edition=str(data.get("edition", "2014")),
            data=data.get("data") if isinstance(data.get("data"), dict) else {},
            ability_pool=[
                int(v) for v in data.get("ability_pool", []) if isinstance(v, (int, float))
            ],
            ability_detail=str(data.get("ability_detail", "") or ""),
            ability_assign=[
                int(v) for v in data.get("ability_assign", []) if isinstance(v, (int, float))
            ],
            ability_bonus=(
                {
                    str(k): int(v)
                    for k, v in data.get("ability_bonus", {}).items()
                    if isinstance(v, (int, float))
                }
                if isinstance(data.get("ability_bonus"), dict)
                else {}
            ),
            backstory_parts=(
                data.get("backstory_parts")
                if isinstance(data.get("backstory_parts"), dict)
                else {}
            ),
            starting_level=int(data.get("starting_level", 1) or 1),
        )


@dataclass
class StepReply:
    """引导每一步的返回：三段式（进度/校验/下一问），LLM 只做话术包装。"""

    progress: str
    check: str
    next_question: str
    done: bool = False  # True = 已落库完成，草稿已删除

    def format(self) -> str:
        return f"【进度】{self.progress}\n【校验】{self.check}\n【下一问】{self.next_question}"


class ChargenManager:
    """开卡规则 + 引导状态机的管理器（KV 持久化）。

    依赖注入：
      - character_manager：落库/查重用；
      - kb_manager：RACE/CLASS/BACKGROUND/ORIGIN 名称校验用（可空，空则放行）；
      - roll_fn：掷骰执行回调（main.py 注入，经过插件骰子限制并写历史），
        签名 roll_fn(expr) -> (total|None, detail)。
    """

    def __init__(
        self,
        star: Star,
        character_manager: CharacterManager,
        kb_manager: object | None = None,
        roll_fn: Callable[[str], tuple[int | None, str]] | None = None,
        inventory_manager=None,
    ) -> None:
        self._star = star
        self._characters = character_manager
        self._kb = kb_manager
        self._roll_fn = roll_fn
        self._inventory = inventory_manager
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 群规则 KV
    # ------------------------------------------------------------------

    @staticmethod
    def _rule_key(origin: str) -> str:
        return f"{_KV_PREFIX_RULE}{origin}"

    async def get_rule(self, event: AstrMessageEvent) -> ChargenRule:
        try:
            raw = await self._star.get_kv_data(self._rule_key(event.unified_msg_origin), None)
            if isinstance(raw, dict):
                return ChargenRule.from_dict(raw)
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取开卡规则失败: {e}")
        except Exception:  # noqa: BLE001
            pass
        return ChargenRule()

    async def set_rule(self, event: AstrMessageEvent, rule: ChargenRule) -> None:
        try:
            await self._star.put_kv_data(
                self._rule_key(event.unified_msg_origin), rule.to_dict()
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入开卡规则失败: {e}")
        except Exception:  # noqa: BLE001
            pass

    async def reset_rule(self, event: AstrMessageEvent) -> None:
        try:
            await self._star.delete_kv_data(self._rule_key(event.unified_msg_origin))
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 重置开卡规则失败: {e}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 草稿 KV
    # ------------------------------------------------------------------

    @staticmethod
    def _draft_key(origin: str, sender_id: str) -> str:
        return f"{_KV_PREFIX_DRAFT}{origin}:{sender_id}"

    async def get_draft(
        self, event: AstrMessageEvent, sender_id: str | None = None
    ) -> ChargenDraft | None:
        sid = sender_id if sender_id is not None else str(event.get_sender_id())
        try:
            raw = await self._star.get_kv_data(
                self._draft_key(event.unified_msg_origin, sid), None
            )
            if isinstance(raw, dict):
                draft = ChargenDraft.from_dict(raw)
                if draft.state != S_IDLE:
                    return draft
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取车卡草稿失败: {e}")
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _save_draft(
        self, event: AstrMessageEvent, draft: ChargenDraft, sender_id: str
    ) -> None:
        try:
            await self._star.put_kv_data(
                self._draft_key(event.unified_msg_origin, sender_id), draft.to_dict()
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入车卡草稿失败: {e}")
        except Exception:  # noqa: BLE001
            pass

    async def _delete_draft(self, event: AstrMessageEvent, sender_id: str) -> None:
        try:
            await self._star.delete_kv_data(
                self._draft_key(event.unified_msg_origin, sender_id)
            )
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 删除车卡草稿失败: {e}")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 名称校验（知识库）
    # ------------------------------------------------------------------

    def _kb_has(self, kind: str, name: str) -> bool:
        """知识库精确命中校验；知识库不可用时放行（兜底）。"""
        kb = self._kb
        if kb is None:
            return True
        try:
            if not kb.available:
                return True
            hits = kb.search(name, kind=kind, limit=1)
            return any(h.name == name for h in hits)
        except Exception:  # noqa: BLE001 — 查询失败放行，不阻塞引导
            return True

    def _bonus_offer(self, draft: ChargenDraft, rule: ChargenRule):
        """当前种族/背景的属性加值 offer（kb 缺失/异常返回 None）。

        2014 取种族（含 choose 的半精灵式）；2024 取背景（weighted choose）。
        """
        kb = self._kb
        if kb is None:
            return None
        try:
            if rule.edition == "2024":
                return kb.background_ability(draft.data.get("background", ""))
            return kb.race_ability(draft.data.get("race", ""), rule.edition)
        except Exception:  # noqa: BLE001 — 查询失败视为无加值，不阻塞引导
            return None

    # ------------------------------------------------------------------
    # 引导流程
    # ------------------------------------------------------------------

    def _step_order(self, rule: ChargenRule) -> list[str]:
        """按版本与规则展开步骤列表。"""
        steps = [S_CONFIRM]
        if rule.edition == "2014":
            steps += [S_RACE, S_CLASS, S_BACKGROUND]
        else:
            steps += [S_CLASS, S_ORIGIN_BG, S_ORIGIN_SPECIES]
        if rule.ability.kind == "roll":
            steps.append(S_ABILITY_METHOD)
        steps += [
            S_ABILITY_ASSIGN, S_ABILITY_BONUS, S_ALIGNMENT,
            S_BACKSTORY_ORIGIN, S_BACKSTORY_DECISION, S_BACKSTORY_EVENT,
            S_NAME,
        ]
        return steps

    def _progress_text(self, draft: ChargenDraft, rule: ChargenRule) -> str:
        steps = self._step_order(rule)
        path = "2014 路径" if rule.edition == "2014" else "2024 路径"
        if draft.state not in steps:
            return f"引导（{path}）"
        total = len(steps)
        idx = steps.index(draft.state) + 1
        label = _STEP_CN.get(draft.state, draft.state)
        return f"步骤 {idx}/{total}：{label}（{path}）"

    async def start(
        self,
        event: AstrMessageEvent,
        sender_id: str | None = None,
        prefill: dict[str, str] | None = None,
    ) -> StepReply:
        """开始（或重新开始）车卡引导：覆盖旧草稿。

        prefill（v0.35.0，构筑咨询深联动）：可选预填项，dict[str, str]，
        支持键 race/class_name/background（2024 路径 race 写 species，
        class_name 兼容键 class）。逐项复用知识库校验（与推进步一致），
        合法项写入草稿并跳过对应步骤，非法项忽略（留在正常询问步）并在
        check 注明；全预填时跳过 CONFIRM，否则仍从 CONFIRM 确认规则。
        """
        sid = sender_id if sender_id is not None else str(event.get_sender_id())
        rule = await self.get_rule(event)
        draft = ChargenDraft(
            state=S_CONFIRM,
            edition=rule.edition,
            starting_level=rule.starting_level,
        )
        notes: list[str] = []
        skipped = 0
        all_done = False
        if prefill:
            pf: dict[str, str] = {}
            for k, v in (prefill or {}).items():
                v = str(v or "").strip()
                if v:
                    pf[str(k).strip().lower()] = v
            if rule.edition == "2014":
                editable = (S_RACE, S_CLASS, S_BACKGROUND)
                fill = {
                    S_RACE: ("race", "race", pf.get("race") or pf.get("species")),
                    S_CLASS: ("class_name", "class", pf.get("class_name") or pf.get("class")),
                    S_BACKGROUND: ("background", "background", pf.get("background")),
                }
            else:
                editable = (S_CLASS, S_ORIGIN_BG, S_ORIGIN_SPECIES)
                fill = {
                    S_CLASS: ("class_name", "class", pf.get("class_name") or pf.get("class")),
                    S_ORIGIN_BG: ("background", "background", pf.get("background")),
                    S_ORIGIN_SPECIES: ("species", "race", pf.get("race") or pf.get("species")),
                }
            for step in editable:
                key, kind, value = fill[step]
                if not value:
                    break  # 缺项：停在当前步（链式预填不跳空）
                if not self._kb_has(kind, value):
                    notes.append(
                        f"「{value}」不是有效的"
                        f"{_KIND_CN.get(kind, kind)}，需重答"
                    )
                    break
                draft.data[key] = value
                skipped += 1
                notes.append(
                    f"已预填{_KIND_CN.get(kind, kind)}「{value}」"
                    + ("（2024 物种写入 race 键）" if step == S_ORIGIN_SPECIES else "")
                )
            all_done = skipped >= len(editable)
            if not all_done:
                # 部分预填：停在第一个未填/非法步（跳过 CONFIRM，规则视为已确认）
                draft.state = editable[skipped]
            else:
                # 全预填：跳到属性生成/分配步（CONFIRM 与前置步骤一并跳过）
                draft.state = (
                    S_ABILITY_METHOD if rule.ability.kind == "roll"
                    else S_ABILITY_ASSIGN
                )
        async with self._lock:
            await self._save_draft(event, draft, sid)
        check = "已开始车卡引导。"
        if notes:
            check = "；".join(notes) + "。"
            if all_done:
                check += " 已完成前置步骤，从属性阶段继续。"
            else:
                check += " 未完成项请在下一问中补充。"
        return StepReply(
            progress=self._progress_text(draft, rule),
            check=check,
            next_question=_question_for(draft, rule, self._characters),
        )

    async def status(
        self, event: AstrMessageEvent, sender_id: str | None = None
    ) -> StepReply:
        """查看引导进度（不推进状态）。"""
        sid = sender_id if sender_id is not None else str(event.get_sender_id())
        rule = await self.get_rule(event)
        draft = await self.get_draft(event, sid)
        if draft is None:
            return StepReply(
                progress="未开始",
                check="当前没有进行中的车卡引导。",
                next_question="如玩家想车卡，请只问一个问题：是否开始车卡？（回答后用开始动作）",
            )
        return StepReply(
            progress=self._progress_text(draft, rule),
            check=_status_check_text(draft),
            next_question=_question_for(draft, rule, self._characters),
        )

    async def cancel(
        self, event: AstrMessageEvent, sender_id: str | None = None
    ) -> StepReply:
        """取消引导并删除草稿。"""
        sid = sender_id if sender_id is not None else str(event.get_sender_id())
        async with self._lock:
            await self._delete_draft(event, sid)
        return StepReply(
            progress="已取消",
            check="车卡引导已取消，草稿已丢弃。",
            next_question="如玩家想重新开始，请再问一次是否开始车卡。",
        )

    async def advance(
        self,
        event: AstrMessageEvent,
        text: str,
        assign: str = "",
        sender_id: str | None = None,
    ) -> StepReply:
        """推进引导一步：校验当前步输入 → 写入草稿 → 返回下一问。

        assign 参数供 llm_tool 显式传属性映射；命令层传入 ""。
        校验失败时状态不推进，返回拒绝原因（硬拒绝）。
        """
        sid = sender_id if sender_id is not None else str(event.get_sender_id())
        rule = await self.get_rule(event)
        draft = await self.get_draft(event, sid)
        if draft is None:
            return StepReply(
                progress="未开始",
                check="当前没有进行中的车卡引导。",
                next_question="请先调用 start 开始车卡，再逐步提交答案。",
            )

        answer = (text or "").strip()
        if answer.lower() in _CANCEL_ALIASES:
            return await self.cancel(event, sid)

        async with self._lock:
            # 重新加载（锁内保证串行），防止两次 advance 交错。
            draft = await self.get_draft(event, sid)
            if draft is None:
                return StepReply(
                    progress="未开始",
                    check="当前没有进行中的车卡引导。",
                    next_question="请先调用 start 开始车卡。",
                )
            progress = self._progress_text(draft, rule)
            result = await self._advance_locked(event, draft, rule, answer, assign)
        return result

    async def _advance_locked(
        self,
        event: AstrMessageEvent,
        draft: ChargenDraft,
        rule: ChargenRule,
        answer: str,
        assign: str,
    ) -> StepReply:
        """锁内状态迁移。当前步校验失败 → 返回拒绝文案，state 不变。"""
        state = draft.state
        progress = self._progress_text(draft, rule)

        # ---- CONFIRM ----
        if state == S_CONFIRM:
            draft.state = S_RACE if rule.edition == "2014" else S_CLASS
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check="已确认开卡规则。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- RACE（2014）----
        if state == S_RACE:
            if not answer:
                return _reject(progress, "请输入种族名称。")
            if not self._kb_has("race", answer):
                return _reject(
                    progress,
                    f"知识库中找不到种族「{answer}」，请让玩家换一个，"
                    "或用 /查种族 搜索后再选。",
                )
            draft.data["race"] = answer
            draft.state = S_CLASS
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=f"已接受种族「{answer}」。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- CLASS（两版共有）----
        if state == S_CLASS:
            if not answer:
                return _reject(progress, "请输入职业名称。")
            parts = answer.split()
            class_name = parts[0]
            if not self._kb_has("class", class_name):
                return _reject(
                    progress,
                    f"知识库中找不到职业「{class_name}」，请让玩家换一个，"
                    "或用 /查职业 搜索后再选。",
                )
            draft.data["class_name"] = class_name
            subclass = ""
            if len(parts) > 1:
                subclass = _sanitize_text(parts[1], 40)
            if rule.subclass_at_creation == "on" and subclass:
                draft.data["subclass"] = subclass
            elif rule.subclass_at_creation == "off":
                draft.data["subclass"] = ""
            else:
                draft.data["subclass"] = subclass  # auto：玩家主动给了就记
            draft.state = S_BACKGROUND if rule.edition == "2014" else S_ORIGIN_BG
            await self._save_draft(event, draft, str(event.get_sender_id()))
            check = f"已接受职业「{class_name}」。"
            if draft.data.get("subclass"):
                check += f" 子职「{subclass}」。"
            elif rule.subclass_at_creation == "auto":
                check += " 子职按规则等级再定。"
            return StepReply(
                progress=progress,
                check=check,
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- BACKGROUND（2014）----
        if state == S_BACKGROUND:
            if not answer:
                return _reject(progress, "请输入背景名称。")
            if not self._kb_has("background", answer):
                return _reject(
                    progress,
                    f"知识库中找不到背景「{answer}」，请让玩家换一个，"
                    "或用 /查背景 搜索后再选。",
                )
            draft.data["background"] = answer
            draft.state = (
                S_ABILITY_METHOD if rule.ability.kind == "roll" else S_ABILITY_ASSIGN
            )
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=f"已接受背景「{answer}」。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- ORIGIN_BG（2024：起源=背景）----
        if state == S_ORIGIN_BG:
            if not answer:
                return _reject(progress, "请输入背景名称。")
            if not self._kb_has("background", answer):
                return _reject(
                    progress,
                    f"知识库中找不到背景「{answer}」，请让玩家换一个，"
                    "或用 /查背景 搜索后再选。",
                )
            draft.data["background"] = answer
            draft.state = S_ORIGIN_SPECIES
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=f"已接受背景「{answer}」（2024 背景决定属性加值方案）。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- ORIGIN_SPECIES（2024：起源=物种）----
        if state == S_ORIGIN_SPECIES:
            if not answer:
                return _reject(progress, "请输入物种（种族）名称。")
            if not self._kb_has("race", answer):
                return _reject(
                    progress,
                    f"知识库中找不到物种「{answer}」，请让玩家换一个，"
                    "或用 /查种族 搜索后再选。",
                )
            draft.data["species"] = answer
            draft.state = (
                S_ABILITY_METHOD if rule.ability.kind == "roll" else S_ABILITY_ASSIGN
            )
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=f"已接受物种「{answer}」。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- ABILITY_METHOD（掷骰法代骰，插件执行）----
        if state == S_ABILITY_METHOD:
            if self._roll_fn is None:
                return _reject(progress, "掷骰功能未就绪，请稍后再试。")
            method = rule.ability
            pool: list[int] = []
            lines: list[str] = []
            for i in range(method.count):
                total, detail = self._roll_fn(method.expr)
                if total is None:
                    return _reject(progress, f"代骰第 {i+1} 组失败：{detail}")
                pool.append(total)
                lines.append(f"第{i+1}组 {detail} → **{total}**")
            draft.ability_pool = pool
            draft.ability_detail = "\n".join(lines)
            draft.state = S_ABILITY_ASSIGN
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=(
                    f"已代骰 {method.count} 组 {method.expr}：\n"
                    + "\n".join(lines)
                    + "\n请让玩家把这六个数值分配到六维（各用一次）。"
                ),
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- ABILITY_ASSIGN ----
        if state == S_ABILITY_ASSIGN:
            scores, err = parse_ability_input(answer, assign)
            if err:
                return _reject(progress, err)
            if rule.ability.kind == "point_buy":
                err = validate_point_buy(scores, rule.ability)
            elif rule.ability.kind == "standard_array":
                err = validate_standard_array(scores, rule.ability)
            else:
                err = validate_rolled_assign(scores, draft.ability_pool)
            if err:
                return _reject(progress, err)
            draft.ability_assign = scores
            offer = self._bonus_offer(draft, rule)
            need_bonus_step = bool(offer and offer.chooses)
            draft.state = S_ABILITY_BONUS if need_bonus_step else S_ALIGNMENT
            if need_bonus_step:
                draft.data["bonus_options"] = _offer_options_text(offer)
            await self._save_draft(event, draft, str(event.get_sender_id()))
            shown = "　".join(
                f"{ABILITY_CN[ab]} {v}" for ab, v in zip(ABILITY_NAMES, scores)
            )
            check = f"已接受属性分配（加值前）：{shown}。"
            if need_bonus_step:
                check += " 种族/背景含自选加值方案，下一步请玩家选择。"
            else:
                flat = offer.flat if offer else {}
                if flat:
                    flat_str = "、".join(
                        f"{ABILITY_CN[k]}+{v}" for k, v in sorted(flat.items())
                    )
                    check += f" 将自动应用固定加值：{flat_str}（确认步展示最终值）。"
            return StepReply(
                progress=progress,
                check=check,
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- ABILITY_BONUS（v0.18：种族/背景 choose 加值选择）----
        if state == S_ABILITY_BONUS:
            offer = self._bonus_offer(draft, rule)
            if not offer or not offer.chooses:
                # 防御：无可选方案（数据缺失/异常）→ 自动跳过
                draft.state = S_ALIGNMENT
                await self._save_draft(event, draft, str(event.get_sender_id()))
                return StepReply(
                    progress=progress,
                    check="当前没有自选加值方案，自动进入下一步。",
                    next_question=_question_for(draft, rule, self._characters),
                )
            picks, err = parse_bonus_choice(answer, offer.chooses)
            if err:
                return _reject(progress, err)
            draft.ability_bonus = picks
            draft.state = S_ALIGNMENT
            await self._save_draft(event, draft, str(event.get_sender_id()))
            chosen = "、".join(
                f"{ABILITY_CN[k]}+{v}" for k, v in sorted(picks.items())
            )
            check = f"已接受加值选择：{chosen}。"
            if offer.flat:
                flat_str = "、".join(
                    f"{ABILITY_CN[k]}+{v}" for k, v in sorted(offer.flat.items())
                )
                check += f" 另有固定加值 {flat_str}，确认步一并叠加。"
            return StepReply(
                progress=progress,
                check=check,
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- ALIGNMENT ----
        if state == S_ALIGNMENT:
            if not answer:
                return _reject(progress, "请输入阵营（如 守序善良、混乱中立）。")
            draft.data["alignment"] = _sanitize_text(answer, 20)
            draft.state = S_BACKSTORY_ORIGIN
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=f"已接受阵营「{draft.data['alignment']}」。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- BACKSTORY_ORIGIN / DECISION / EVENT ----
        if state in (S_BACKSTORY_ORIGIN, S_BACKSTORY_DECISION, S_BACKSTORY_EVENT):
            if not answer:
                return _reject(progress, "生平环节请给一点内容（哪怕一句话）。")
            key = {
                S_BACKSTORY_ORIGIN: "origin",
                S_BACKSTORY_DECISION: "decision",
                S_BACKSTORY_EVENT: "event",
            }[state]
            draft.backstory_parts[key] = _sanitize_text(answer)
            next_state = {
                S_BACKSTORY_ORIGIN: S_BACKSTORY_DECISION,
                S_BACKSTORY_DECISION: S_BACKSTORY_EVENT,
                S_BACKSTORY_EVENT: S_NAME,
            }[state]
            draft.state = next_state
            await self._save_draft(event, draft, str(event.get_sender_id()))
            return StepReply(
                progress=progress,
                check=f"已记录生平（{key}）。",
                next_question=_question_for(draft, rule, self._characters),
            )

        # ---- NAME ----
        if state == S_NAME:
            name = _sanitize_card_name(answer)
            if not name:
                return _reject(progress, "卡名不能为空，请给角色起个名字。")
            existing = await self._characters.list_cards(event)
            if name in existing:
                return _reject(
                    progress,
                    f"你已有一张名为「{name}」的卡，请换一个名字。",
                )
            draft.data["name"] = name
            reply = await self._finalize(event, draft, rule)
            return reply

        return StepReply(
            progress=progress,
            check=f"未知状态 {state}，草稿可能已损坏，请取消后重新开始。",
            next_question="如玩家想重新开始，请再问一次是否开始车卡。",
        )

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------

    async def _grant_starting_gold(
        self,
        event: AstrMessageEvent,
        class_name: str,
        edition: str,
        gold_rule: str = "auto",
    ) -> tuple[int | None, str]:
        """发放起始金币（v0.20.0，金币单位）。

        三态（群规则「起始金币」设置项）：
          - "auto"：按职业 goldAlternative 代骰（如 5d4×10，仅 2014 有）；
          - 纯数字：DM 固定金额（不代骰，全团一致）；
          - 骰式：DM 自定义随机财富（如 5d4×10），插件代骰。

        写入玩家个人背包「金币」条目（value 按面值 100 铜）。
        返回 (发放金币数, 说明文本)；查询/代骰失败返回 (None, "") 静默跳过
        （不阻断落库），背包写入失败返回 (None, 提示文本)。
        """
        if self._inventory is None:
            return None, ""
        gold_rule = (gold_rule or "auto").strip().lower()

        async def _write_gold(qty: int, desc: str) -> tuple[int | None, str]:
            qty = max(1, min(int(qty), 99999))
            try:
                await self._inventory.add_item(event, "金币", qty, value=100.0)
            except Exception as e:  # noqa: BLE001 — 写入失败不阻断落库
                logger.warning(f"[trpg_assistant] 起始金币写入背包失败: {e}")
                return None, (
                    "⚠️ 起始金币自动发放失败，请 DM 用 /bag add 金币 <数量> 手动补发。"
                )
            return qty, f"🎲 {desc}（已写入个人背包）。"

        # 1) DM 固定金额：不代骰
        if gold_rule.isdigit():
            return await _write_gold(
                int(gold_rule), f"DM 规则固定 **{int(gold_rule)} 金币**"
            )

        # 2) DM 自定义骰式
        if gold_rule != "auto":
            if self._roll_fn is None:
                return None, ""
            dice, mult = _split_gold_expr(gold_rule)
            try:
                total, detail = self._roll_fn(dice)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[trpg_assistant] 起始金币代骰异常（跳过发放）: {e}")
                return None, ""
            if total is None:
                return None, ""
            qty = int(total) * mult
            return await _write_gold(
                qty, f"DM 规则代骰：{dice} ×{mult} = **{qty} 金币**（明细 {detail}）"
            )

        # 3) auto：按职业 goldAlternative
        kb_gold = getattr(self._kb, "starting_gold", None)
        if kb_gold is None or self._roll_fn is None:
            return None, ""
        try:
            found = kb_gold(class_name, edition)
        except Exception as e:  # noqa: BLE001 — 查询层异常静默跳过
            logger.warning(f"[trpg_assistant] 查询起始金币失败（跳过发放）: {e}")
            return None, ""
        if found is None:
            return None, ""
        dice, mult = found
        try:
            total, detail = self._roll_fn(dice)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 起始金币代骰异常（跳过发放）: {e}")
            return None, ""
        if total is None:
            return None, ""
        qty = int(total) * mult
        return await _write_gold(
            qty, f"起始金币已代骰发放：{dice} ×{mult} = **{qty} 金币**（明细 {detail}）"
        )

    async def _finalize(
        self, event: AstrMessageEvent, draft: ChargenDraft, rule: ChargenRule
    ) -> StepReply:
        """NAME 校验通过后：叠加属性加值 → 引擎重算 → 保存 → 发放起始金币 → 删草稿（最后一步）。"""
        sid = str(event.get_sender_id())
        progress = self._progress_text(draft, rule)
        scores = list(draft.ability_assign or [10] * 6)
        # 叠加种族/背景加值（v0.18 全自动，替代 LLM 引导+DM 复核）
        offer = self._bonus_offer(draft, rule)
        flat = offer.flat if offer else {}
        chosen = draft.ability_bonus or {}
        bonus_map: dict[str, int] = {}
        for ab, amount in flat.items():
            bonus_map[ab] = bonus_map.get(ab, 0) + amount
        for ab, amount in chosen.items():
            bonus_map[ab] = bonus_map.get(ab, 0) + amount
        clamped: list[str] = []
        for ab, amount in bonus_map.items():
            idx = ABILITY_NAMES.index(ab)
            new_val = scores[idx] + amount
            if new_val < 1 or new_val > 30:
                clamped.append(f"{ABILITY_CN[ab]} {new_val}→{max(1, min(30, new_val))}")
                new_val = max(1, min(30, new_val))
            scores[idx] = new_val
        ability = AbilityScores(
            strength=scores[0],
            dexterity=scores[1],
            constitution=scores[2],
            intelligence=scores[3],
            wisdom=scores[4],
            charisma=scores[5],
        )
        class_name = draft.data.get("class_name", "")
        sheet = CharacterSheet(
            name=draft.data.get("name", "未知冒险者"),
            edition=draft.edition,
            classes=(
                [
                    ClassLevel(
                        class_name=class_name,
                        subclass=draft.data.get("subclass", ""),
                        level=draft.starting_level,
                    )
                ]
                if class_name
                else []
            ),
            race=draft.data.get("race") or draft.data.get("species", ""),
            background=draft.data.get("background", ""),
            alignment=draft.data.get("alignment", ""),
            ability_scores=ability,
            backstory=_merge_backstory(draft.backstory_parts),
        )
        # 规则引擎重算战斗字段 base 层（bonus 不动；失败不影响落库）
        report_text = ""
        if self._kb is not None:
            try:
                from .chargen_engine import recalc_base

                report = recalc_base(sheet, self._kb)
                report_text = report.text
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[trpg_assistant] 规则引擎重算失败（不影响落库）: {e}")
        err = await self._characters.save_card(event, sheet)
        if err is not None:
            return _reject(progress, f"落库失败：{err}")
        # 起始金币发放（群规则三态：自动/固定/骰式；失败不阻断落库）
        gold_text = ""
        if class_name:
            _qty, gold_text = await self._grant_starting_gold(
                event, class_name, draft.edition, rule.starting_gold
            )
        await self._delete_draft(event, sid)
        bonus_desc = "、".join(
            f"{ABILITY_CN[k]}+{v}" for k, v in sorted(bonus_map.items())
        ) if bonus_map else "无"
        equipment_hint = (
            f"角色卡「{draft.data.get('name')}」已保存！"
            f"\n属性加值已自动叠加（{bonus_desc}）。"
            f"\n战斗字段已自动计算：{report_text or '无可用数据'}。"
        )
        if gold_text:
            equipment_hint += f"\n{gold_text}"
        else:
            equipment_hint += (
                "\n请用 manage_inventory 工具按职业/背景的起始装备清单，"
                "把起始装备加入玩家的个人背包。"
            )
        if clamped:
            equipment_hint += "\n注意：以下属性超出 1-30 范围已截断：" + "；".join(clamped) + "。"
        return StepReply(
            progress=progress,
            check="已完成车卡并落库！",
            next_question=equipment_hint,
            done=True,
        )


# ---------------------------------------------------------------------------
# 状态机辅助文本
# ---------------------------------------------------------------------------

_STEP_CN: dict[str, str] = {
    S_CONFIRM: "确认开卡规则",
    S_RACE: "选择种族",
    S_CLASS: "选择职业",
    S_BACKGROUND: "选择背景",
    S_ORIGIN_BG: "确定起源·背景",
    S_ORIGIN_SPECIES: "确定起源·物种",
    S_ABILITY_METHOD: "掷骰生成属性",
    S_ABILITY_ASSIGN: "分配属性值",
    S_ABILITY_BONUS: "选择属性加值",
    S_ALIGNMENT: "选择阵营",
    S_BACKSTORY_ORIGIN: "生平·出身",
    S_BACKSTORY_DECISION: "生平·个人决定",
    S_BACKSTORY_EVENT: "生平·人生经历",
    S_NAME: "命名与确认",
}


def _offer_options_text(offer) -> str:
    """加值 offer 的可选方案文本（存入草稿供提问复用）。"""
    lines = []
    flat = getattr(offer, "flat", None) or {}
    if flat:
        lines.append(
            "固定加值：" + "、".join(
                f"{ABILITY_CN[k]}+{v}" for k, v in sorted(flat.items())
            )
        )
    chooses = getattr(offer, "chooses", None) or []
    for i, spec in enumerate(chooses, 1):
        lines.append(f"方案 {i}：{_describe_choose(spec)}")
    return "\n".join(lines) if lines else "（无）"


def _question_for(
    draft: ChargenDraft, rule: ChargenRule, characters: CharacterManager
) -> str:
    """按当前状态生成「下一问」指令文本（LLM 据此发问，一次一问）。"""
    no_answer = "禁止替玩家作答，只转述问题。"
    if draft.state == S_CONFIRM:
        return (
            f"确认开卡规则后进入下一步。若玩家要求改规则，提示 DM 用「/车卡规则」。"
            + no_answer
        )
    if draft.state == S_RACE:
        return (
            "请只问玩家一个问题：选择种族。建议先列出 3-5 个常见种族"
            "（人类/精灵/矮人/半兽人/提夫林…），或提示用 /查种族 <名> 查看详情。"
            + no_answer
        )
    if draft.state == S_CLASS:
        hint = ""
        if rule.subclass_at_creation == "on":
            hint = "（群规则允许开卡时确定子职，可让玩家一并回答「职业 子职」）"
        elif rule.subclass_at_creation == "off":
            hint = "（群规则：车卡时不选子职）"
        else:
            hint = "（群规则：子职按规则等级再定）"
        return (
            "请只问玩家一个问题：选择职业" + hint + "。建议列出 3-5 个常见职业"
            "（战士/法师/游侠/牧师/盗贼…），或提示用 /查职业 <名> 查看详情。" + no_answer
        )
    if draft.state == S_BACKGROUND:
        return (
            "请只问玩家一个问题：选择背景。建议列出 3-5 个常见背景"
            "（士兵/流浪儿/智者/贵族/民间英雄…），或提示用 /查背景 <名> 查看详情。"
            + no_answer
        )
    if draft.state == S_ORIGIN_BG:
        return (
            "请只问玩家一个问题：确定起源（背景）。2024 规则中背景决定属性加值方案，"
            "建议提示玩家用 /查背景 <名> 查看详情。" + no_answer
        )
    if draft.state == S_ORIGIN_SPECIES:
        return (
            "请只问玩家一个问题：确定起源（物种，即种族）。"
            "提示用 /查种族 <名> 查看详情。" + no_answer
        )
    if draft.state == S_ABILITY_METHOD:
        return "输入任意确认文本（如「骰」）触发插件代骰，然后引导玩家分配。"
    if draft.state == S_ABILITY_BONUS:
        options = draft.data.get("bonus_options") or "请从可选方案中选择。"
        return (
            "请只问玩家一个问题：选择属性加值方案。\n" + options
            + "\n玩家回答格式示例「力+2 敏+1」。禁止替玩家作答。"
        )
    if draft.state == S_ABILITY_ASSIGN:
        rule_text = (
            f"群规则：{rule.ability.describe()}。"
            if rule.ability.kind == "point_buy"
            else (
                "请让玩家把代骰出的六个数值分配到六维，各用一次。"
                if rule.ability.kind == "roll"
                else "标准数组：15、14、13、12、10、8 各用一次，自由分配。"
            )
        )
        return (
            "请只问玩家一个问题：如何分配属性值？格式示例「15 14 13 12 10 8」"
            f"（按 力/敏/体/智/感/魅 顺序）或「力15 敏14 体13 智12 感10 魅8」。"
            + rule_text + no_answer
        )
    if draft.state == S_ALIGNMENT:
        return (
            "请只问玩家一个问题：选择阵营（如 守序善良、混乱中立、绝对中立）。"
            + no_answer
        )
    if draft.state == S_BACKSTORY_ORIGIN:
        return XGE_ORIGIN_PROMPT + no_answer
    if draft.state == S_BACKSTORY_DECISION:
        return XGE_DECISION_PROMPT + no_answer
    if draft.state == S_BACKSTORY_EVENT:
        return XGE_EVENT_PROMPT + no_answer
    if draft.state == S_NAME:
        return (
            "请只问玩家一个问题：给角色起个名字（将作为角色卡名）。"
            "起名后插件将自动完成属性加值叠加与战斗字段（HP/AC/法术位/攻击）计算。"
            + no_answer
        )
    return "（无待处理步骤）"


def _status_check_text(draft: ChargenDraft) -> str:
    """status 返回的已收集信息摘要。"""
    parts: list[str] = []
    data = draft.data
    if data.get("race"):
        parts.append(f"种族 {data['race']}")
    if data.get("species"):
        parts.append(f"物种 {data['species']}")
    if data.get("class_name"):
        cls = data["class_name"] + (f"·{data['subclass']}" if data.get("subclass") else "")
        parts.append(f"职业 {cls}")
    if data.get("background"):
        parts.append(f"背景 {data['background']}")
    if data.get("alignment"):
        parts.append(f"阵营 {data['alignment']}")
    if draft.ability_pool:
        parts.append("代骰池 " + "、".join(str(v) for v in draft.ability_pool))
    if draft.ability_assign:
        parts.append(
            "属性分配 " + "　".join(
                f"{ABILITY_CN[ab]} {v}" for ab, v in zip(ABILITY_NAMES, draft.ability_assign)
            )
        )
    if draft.backstory_parts:
        parts.append("生平已填写 " + "、".join(draft.backstory_parts.keys()))
    return "已收集：" + ("；".join(parts) if parts else "（暂无）") + "。"


def _merge_backstory(parts: dict) -> str:
    """将生平三段拼为最终文本。"""
    sections = [
        ("出身", parts.get("origin", "")),
        ("个人决定", parts.get("decision", "")),
        ("人生经历", parts.get("event", "")),
    ]
    lines = [
        f"【{title}】{text}"
        for title, text in sections
        if text
    ]
    return "\n".join(lines)


def _reject(progress: str, reason: str) -> StepReply:
    """构造硬拒绝回复（状态不推进）。"""
    return StepReply(
        progress=progress,
        check=f"✗ 未接受：{reason}",
        next_question="请让玩家重新回答当前问题；如玩家有异议，可请 DM 用「/车卡规则」修改群规则后再试。",
    )

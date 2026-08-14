"""
main.py — AstrBot 跑团助手插件入口。

指令：
  /r [表达式]    使用 DnD 骰池语法掷骰。
  /roll [表达式] /r 的别名。
  /rh [me|all|clear]  查看或清空当前会话的投掷历史记录。
    无参数 / all：群聊显示全员历史，私聊显示本人历史。
    me：仅显示自己的投掷记录（群聊内使用）。
    clear：清空当前会话历史（群聊需白名单权限）。
  /ri [参数]     掷先攻并入列（DnD 规则：d20 + 调整值）。
    无参数 / +调整值：为本人掷骰；后附名称可代掷；
    <固定值> [名称]：以固定值录入（DM 抄书值）。
  /init [参数]   查看/推进/管理先攻列表。
    无参数：查看列表；end：推进回合（轮数计数）；
    del <名称>：移除单位（白名单权限）；clr：清空本场战斗（白名单权限）。
  /bag [子命令]  个人背包与队伍背包管理。
    无参数：查看自己的背包；add/rm：放入/取出物品；
    edit：修改物品属性（w=/v=/note=，- 表示清除）；
    give @某人：赠送；party/put/take：队伍背包查看/存入/取出；
    clear：清空自己的背包；party clear：清空队伍背包（白名单权限）。
  /卡 [子命令]   角色卡管理（多卡 + 活跃切换）。
    无参数：查看活跃卡；列表/用/删/改名：卡管理；
    设 <字段> <值>：设置 hp/ac/速度/法术位N/攻击/主手/副手/护甲/背景/种族/职业/版本/六维属性
    （力量/敏捷/体质/智力/感知/魅力）/生平/背景故事/阵营/语言；
    升级/降级 [职业名]：±1 级并自动重算；熟练 技能|豁免 +名 -名：增减熟练；骰 <名称> <表达式>：命名掷骰。
  /车卡 [子命令] 车卡引导（LLM 经工具逐步提问为主）。
    空：开始或查看进度；状态：查看当前问题；答 <答案>：提交答案；取消。
  /车卡规则 [设置] 群级开卡规则（版本/属性生成方式/子职时机/起始等级，
    写入需管理权限）。内置属性方式别名：27buy/32buy/dnd5/标准数组。
  /dnd [组数]   按 DND 5e 规则随机生成角色属性（每组 4d6kh3×6，
    默认 1 组，上限 20，结果可直接分配填入六维）。
  /查法术|查怪|查物品|查专长|查背景 <名称>
                 查询 DND 知识库，返回同名全部版本（如 2014/2024 双版法术）。
  /查职业 <职业名> [子职名|版本|等级段|特性 [特性名]]
                 查询职业特性：默认返回分层概要总表（第1~4层，每行
                 「N级 名称：一句话概要」）；第二参数可钻取子职（战士 勇士）、
                 版本（战士 2024）、层级（战士 第2层）、等级段（战士 5级）
                 或「特性」输出本职特性完整说明（按层级分条发送）。
  /kb version   查看知识库版本与数据来源。
  /帮助 [组名]   指令大全（/帮助 知识库 查看知识库指令详解）。

支持的骰池语法（Roll20 规范）：
  d20                    单个 d20。
  1d20+5                 1 枚 d20 加 +5 修正。
  4dF                    4 枚 FATE/Fudge 骰（-/0/+）。
  4d6kh3                 掷 4d6，保留最高 3 个。
  8d100k4                掷 8d100，保留最高 4（k = kh 简写）。
  2d20kl1                掷 2d20，保留最低 1 个（劣势）。
  d20adv                 优势骰（2d20kh1 的简写）。
  d20dis                 劣势骰（2d20kl1 的简写）。
  d20优势 / d20劣势      中文优势/劣势（紧贴后缀，等价 adv/dis）。
  8d6d3 / 8d6dl3         掷 8d6，丢弃最低 3 个。
  8d6dh3                 掷 8d6，丢弃最高 3 个。
  d6!                    标准爆炸骰（掷出最大值追加一骰）。
  d6!>4                  掷出 >=4 即爆炸。
  5d6!!                  复合爆炸（Shadowrun 风格，追加值合并）。
  5d6!p                  穿透爆炸（HackMaster 风格，追加骰 -1）。
  3d6>3                  目标数成功计数（>=3 算成功）。
  10d6<4                 目标数成功计数（<=4 算成功）。
  3d6>3f1                成功计数 + 失败计数（1 算失败）。
  2d8r<2                 重骰：<=2 的骰值循环重掷。
  2d6ro<2                重骰：<=2 只重掷一次。
  8d6s / 8d6sd           掷 8d6，结果升序/降序显示。
  2d6+1d4+3              多骰组合加修正值。
  3#d20+d6                多重投掷：重复投掷 3 次 d20+d6（上限 20 次）。
  3d6*(2+4)d12            完整四则运算：乘法与括号。
  (2+3)d6                 括号算式作为骰数（5 枚 d6）。
  3d(2*4)                 括号算式作为骰面（3 枚 d8）。
  1d20+5#攻击检定        用 '#' 分隔附加标签。
  1d20+5 攻击检定        用空格分隔附加标签。
  d20 感知 15            技能检定：标签 + DC，输出"成功/失败"。
  d20adv 察觉 13         优势技能检定。

LLM 函数工具：
  插件注册了 `roll_dice`、`manage_initiative`、`manage_inventory`、
  `manage_shop`、`manage_character`、`guide_chargen`、`advise_build`、
  `query_dnd_knowledge` 与 `manage_homebrew` 工具：
  - roll_dice：LLM 在 TRPG 叙事中自动掷骰。
  - manage_initiative：LLM 自动管理战斗先攻（掷/录/查看/推进/移除/清空）。
  - manage_inventory：LLM 自动管理背包（放入/取出/查看/队伍流转/清空）。
  - manage_shop：LLM 自动完成商店买卖结算（查看商品/购买扣款找零/卖回）。
  - manage_character：LLM 管理角色卡（查看/建卡/升级/设值）。
  - guide_chargen：LLM 引导车卡（按插件返回的进度逐问推进）。
  - query_dnd_knowledge：LLM 查询内置 DND 知识库（法术/怪物/物品/专长/背景/职业，
    支持同名多版本、结构化筛选，答案必须依据工具返回的原文）。
  - advise_build：LLM 提供构筑/升级建议（候选档案由插件确定性组装）。
  - manage_homebrew：LLM 转录/写入/点评私设（write 需配置开启+白名单或管理员）。
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .chargen import (
    ChargenManager,
    ChargenRule,
    StepReply,
    parse_ability_input,
    parse_rule_edit,
)
from .character import (
    ABILITY_ALIAS,
    ABILITY_CN,
    ABILITY_NAMES,
    CharacterManager,
    CharacterSheet,
    SKILL_ALIAS,
    SKILL_CN_REV,
    _norm_spell_ring,
    _spell_ring_label,
    resolve_roll_alias,
)
from .dice_parser import DiceParseError, parse
from .dice_roller import DiceRollError, roll
from .formatter import format_result
from .history import ROLL_ERROR_PREFIXES, RollHistoryManager
from .homebrew import KIND_LABEL
from .homebrew_writer import (
    atomic_write_text,
    derive_filename,
    flatten_raw_entries,
    merge_homebrew_texts,
    sanitize_filename,
    validate_homebrew_text,
)
from .initiative import InitiativeManager
from .inventory import InventoryManager, _UNSET
from .kb import (
    CLASS_TIERS,
    MACHINE_FLAG,
    NO_HALLUCINATION_NOTE,
    KnowledgeBaseManager,
    resolve_db_path,
)
from .build_advisor import (
    assemble_level_up,
    assemble_new_build,
    dossier_to_text,
)
from .money import format_cp, parse_money
from .shop import ShopManager
from .kb_enums import (
    SPEED_TYPE_CN_REV,
    resolve_ability,
    resolve_alignment,
    resolve_background_keyword,
    resolve_class_keyword,
    resolve_class_role,
    resolve_component,
    resolve_condition,
    resolve_creature_type,
    resolve_damage_type,
    resolve_environment,
    resolve_feat_keyword,
    resolve_feat_type,
    resolve_item_type,
    resolve_kind,
    resolve_monster_type,
    resolve_property,
    resolve_race_keyword,
    resolve_rarity,
    resolve_school,
    resolve_sense_type,
    resolve_shape,
    resolve_size,
    resolve_speed_type,
    resolve_spell_keyword,
    resolve_subclass_keyword,
    OPTIONAL_FEATURE_TYPE_CN,
    resolve_target,
)

# 内存前缀缓存所允许的最大会话来源数量。
_PREFIX_CACHE_MAX: int = 512
# 前缀缓存条目 TTL（秒）。超时后下次访问触发 KV 重新验证，
# 降低外部直接修改 KV 后本实例长期持有陈旧前缀的风险。
_PREFIX_CACHE_TTL: float = 300.0

# /ri 参数中的调整值正则：必须带显式 +/- 号，后跟 1~3 位数字。
_INIT_MOD_RE = re.compile(r"^([+-])(\d{1,3})$")

# 先攻固定值的允许范围。
_INIT_FIXED_MIN = -99
_INIT_FIXED_MAX = 999


def _parse_ri_arg(arg: str, default_name: str) -> tuple[str, int, str]:
    """
    解析 /ri 命令参数。

    Args:
        arg: 去除命令名后的参数字符串（可能为空）。
        default_name: 未指定名称时使用的默认名称（通常为发送者昵称）。

    Returns:
        (kind, number, name) 三元组：
          - kind="roll":  掷 d20 + number（number 为带符号调整值）。
          - kind="fixed": 以固定值 number 入列（参数首 token 为不带符号的整数）。
          - kind="invalid": 参数无法识别（number/name 无意义）。
        name 为清洗后的单位名称（未指定时取 default_name）。
    """
    arg = arg.strip()
    if not arg:
        return "roll", 0, default_name

    tokens = arg.split(None, 1)
    first = tokens[0]
    name_part = tokens[1].strip() if len(tokens) > 1 else ""
    name = name_part or default_name

    mod_match = _INIT_MOD_RE.match(first)
    if mod_match:
        sign = 1 if mod_match.group(1) == "+" else -1
        return "roll", sign * int(mod_match.group(2)), name

    if first.isdigit():
        number = int(first)
        if _INIT_FIXED_MIN <= number <= _INIT_FIXED_MAX:
            return "fixed", number, name
        return "invalid", 0, name

    return "invalid", 0, name

# ---------------------------------------------------------------------------
# 配置读取辅助函数
# ---------------------------------------------------------------------------


def _safe_int(
    value: object,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """将任意配置值安全转换为 int，转换失败或超出 [min_val, max_val] 范围时返回默认值。

    WebUI 的 _conf_schema.json 滑块范围只是前端提示，直接手改配置文件可绕过，
    因此这里再加一道 max_val 上限校验，行为与既有的 min_val 下限校验一致。
    """
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if min_val is not None and result < min_val:
        return default
    if max_val is not None and result > max_val:
        return default
    return result


def _safe_bool(value: object, default: bool) -> bool:
    """将任意配置值安全转换为 bool，转换失败时返回默认值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    try:
        return bool(value)
    except Exception as e:
        # 与 history.py 的告警风格一致：兜底吞掉异常前先记录，避免静默失效难以排查。
        logger.warning(
            f"[trpg_assistant] 配置值转换为 bool 失败，使用默认值: {value!r} ({e})"
        )
        return default


def _compose_tool_expr(
    expression: str, label: str, dc: float | None, default_sides: int
) -> str:
    """
    组合 LLM 工具调用参数为完整骰池表达式字符串（供 parse() 解析）。

    纯函数，便于独立单元测试；不涉及 event/KV 等副作用。

    Args:
        expression: 骰池表达式，可能为空——AstrBot 生成的工具 schema 不含
            required 列表，LLM 省略该参数时会以空字符串（默认值）调用，
            此时退化为掷当前会话默认骰，而非直接 TypeError。
        label: 说明标签，可能为空。
        dc: 难度等级（DC），为 None 时不参与合成；转换为 int 失败
            （如 NaN、inf）时忽略 dc，保持 label 原样，不抛异常。
        default_sides: expression 为空时使用的默认骰面数。

    Returns:
        expression（去空白，必要时回退默认骰）+ 可选 "#label"（含 DC）后缀。
    """
    expr = expression.strip()
    if not expr:
        expr = f"d{default_sides}"

    if dc is not None:
        try:
            dc_int = int(dc)
        except (ValueError, OverflowError, TypeError):
            dc_int = None
        if dc_int is not None:
            label = f"{label} {dc_int}".strip()

    return f"{expr}#{label}" if label else expr


# ---------------------------------------------------------------------------
# 解析失败或请求帮助时显示的语法提示
# ---------------------------------------------------------------------------

_SYNTAX_HELP = (
    "用法：/r [骰池表达式] [标签] [DC]\n"
    "示例：\n"
    "  /r d20\n"
    "  /r 1d20+5\n"
    "  /r 4dF                  FATE骰\n"
    "  /r 4d6kh3\n"
    "  /r 8d100k4              k = kh 简写\n"
    "  /r 8d6d3                丢弃最低3\n"
    "  /r 8d6dh3               丢弃最高3\n"
    "  /r d20adv\n"
    "  /r d20dis\n"
    "  /r d6!                  标准爆炸\n"
    "  /r d6!>4                自定义爆炸点\n"
    "  /r 5d6!!                复合爆炸\n"
    "  /r 5d6!p                穿透爆炸\n"
    "  /r 3d6>3                目标数成功计数\n"
    "  /r 3d6>3f1              成功+失败计数\n"
    "  /r 2d8r<2               重骰\n"
    "  /r 2d6ro<2              只重骰一次\n"
    "  /r 8d6s                 排序(升序)\n"
    "  /r 8d6sd                排序(降序)\n"
    "  /r 2d6+1d4+3 伤害\n"
    "  /r 3#d20+d6              重复投掷3次（上限20）\n"
    "  /r 3d6*(2+4)d12          四则运算+括号\n"
    "  /r (2+3)d6               括号算式作骰数\n"
    "  /r 3d(2*4)               括号算式作骰面\n"
    "  /r 1d20+5#攻击检定\n"
    "  /r d20 感知 15\n"
    "  /r d20adv 察觉 13\n"
    "  /r d20优势              中文优势（等价 d20adv）\n"
    "  /r d20劣势              中文劣势（等价 d20dis）"
)


# ---------------------------------------------------------------------------
# 中文「优势/劣势」→ 引擎 adv/dis 语法糖（命令层映射，dice_parser 上游只读）
# ---------------------------------------------------------------------------

# 仅当「优势/劣势」紧贴 ASCII 骰式字符（数字/字母）后缀时才映射；
# 标签内（如「d20 战斗优势」）或前置用法不误伤，孤立词由 _do_roll 拒绝。
_ZH_ADV_SUB = re.compile(r"([0-9A-Za-z])优势")
_ZH_DIS_SUB = re.compile(r"([0-9A-Za-z])劣势")
# 孤立「优势/劣势」词：前为行首或空白、后为空白/行首/加减号或紧贴骰式字符
# —— 覆盖带空格（d20 优势）、前置（优势d20）、单独使用（优势）等非紧贴后缀写法
_ZH_ORPHAN_ADV_DIS = re.compile(r"(?:^|\s)(优势|劣势)(?=\s|$|[+\-−]|[0-9A-Za-z])")


def _map_zh_adv_dis(expression_str: str) -> str:
    """把紧贴骰式的「优势/劣势」映射为引擎 adv/dis 语法糖；其余出现位置原样保留。"""
    s = _ZH_ADV_SUB.sub(r"\1adv", expression_str)
    s = _ZH_DIS_SUB.sub(r"\1dis", s)
    return s


# /dnd 属性生成：组数上限 / 每组属性个数 / 掷骰式（DND 5e 标准 4d6 取最大 3）
_DND_MAX_GROUPS = 20
_DND_SCORE_COUNT = 6
_DND_ROLL_EXPR = "4d6kh3"


# ---------------------------------------------------------------------------
# /bag 命令解析辅助（模块级纯函数，便于单测，同 _parse_ri_arg 风格）
# ---------------------------------------------------------------------------


def _resolve_event(event):
    """兼容 AstrBot 新旧版本 llm_tool 注入的上下文对象。

    老版（< v4.5）直接注入 AstrMessageEvent；v4.5+ 的新 agent 体系注入
    ContextWrapper（其 .context 为 AstrAgentContext，真正的事件在
    .context.event）。统一解出带 unified_msg_origin 的真实事件对象；
    无法识别时返回 None（调用方给出友好提示，不崩）。
    """
    if event is None:
        return None
    if hasattr(event, "unified_msg_origin"):
        return event
    # ContextWrapper[AstrAgentContext]：context → agent_context.event
    inner = getattr(event, "context", None)
    if inner is None:
        inner = getattr(event, "agent_context", None)
    if inner is not None:
        ev = getattr(inner, "event", None)
        if ev is not None and hasattr(ev, "unified_msg_origin"):
            return ev
    return None


def _tokenize(arg: str) -> list[str] | None:
    """shlex 分词，支持英文引号包裹含空格的物品名；引号不配对返回 None。"""
    if not arg:
        return []
    try:
        return shlex.split(arg)
    except ValueError:
        return None


def _parse_add_tokens(tokens: list[str]) -> dict | str:
    """
    解析 /bag add 的位置参数 + 键值对混合参数。

    规则：token 含 '=' 且键 ∈ {w, v, note} → 键值对（任意顺序、可缺省）；
    其余按序填充位置槽 [名称, 数量]。
    note= 的值允许引号包裹含空格（已由 _tokenize 处理）。

    Returns:
        成功返回 {"name", "qty", "weight", "value", "note"}
        （weight/value 为 float|None，note 为 str|None）；
        失败返回错误文案字符串（由调用方直接作为回复，可附用法行）。
    """
    positional: list[str] = []
    weight: float | None = None
    value: float | None = None
    note: str | None = None
    for tok in tokens:
        key, sep, raw_val = tok.partition("=")
        if sep and key.lower() in ("w", "v", "note"):
            k = key.lower()
            if k == "note":
                note = raw_val
            else:
                try:
                    num = float(raw_val)
                except ValueError:
                    return f"无效的 {k}= 值：'{raw_val}'，应为非负数字。"
                if num != num or num < 0:  # NaN 或负数
                    return f"无效的 {k}= 值：'{raw_val}'，应为非负数字。"
                if k == "w":
                    weight = num
                else:
                    value = num
        else:
            positional.append(tok)

    if not positional:
        return "请提供物品名称。"
    if len(positional) < 2:
        return "请提供物品数量。"
    if len(positional) > 2:
        extras = "、".join(f"'{t}'" for t in positional[2:])
        return f"无法识别的参数：{extras}（物品名含空格时请用英文双引号包裹）。"

    try:
        qty = int(positional[1])
    except ValueError:
        return f"无效的数量：'{positional[1]}'，应为 1~99999 的整数。"
    if not 1 <= qty <= 99999:
        return f"无效的数量：'{positional[1]}'，应为 1~99999 的整数。"

    return {
        "name": positional[0],
        "qty": qty,
        "weight": weight,
        "value": value,
        "note": note,
    }


def _parse_name_qty(tokens: list[str], default_qty: int = 1) -> tuple[str, int] | str:
    """
    解析「名称 [数量]」参数（rm/put/take/give 共用），数量缺省 default_qty。

    Returns:
        成功返回 (名称, 数量)；失败返回错误文案字符串。
    """
    if not tokens:
        return "请提供物品名称。"
    if len(tokens) > 2:
        extras = "、".join(f"'{t}'" for t in tokens[2:])
        return f"无法识别的参数：{extras}（物品名含空格时请用英文双引号包裹）。"
    qty = default_qty
    if len(tokens) == 2:
        try:
            qty = int(tokens[1])
        except ValueError:
            return f"无效的数量：'{tokens[1]}'，应为 1~99999 的整数。"
        if not 1 <= qty <= 99999:
            return f"无效的数量：'{tokens[1]}'，应为 1~99999 的整数。"
    return tokens[0], qty


def _parse_batch_name_qty(tokens: list[str]) -> list[tuple[str, int]] | str:
    """解析批量「名称 [数量] 名称 [数量] …」（/商店 买/卖 共用）。

    非纯数字 token = 下一个物品名（数量默认 1）；纯数字 token = 前一个
    物品的数量（数量可省略）。向后兼容单件写法：`买 长剑` = x1、
    `买 长剑 2` = x2。数字出现在名称位（如 `买 2 长剑`）报错。
    返回 [(名称, 数量), ...] 或错误文案字符串。
    """
    if not tokens:
        return "请提供物品名称。"
    pairs: list[tuple[str, int]] = []
    name = ""
    for tok in tokens:
        if tok.isdigit() or (tok.startswith("-") and tok[1:].isdigit()):
            if not name:
                return f"数量「{tok}」前缺少物品名称。"
            qty = int(tok)
            if not 1 <= qty <= 99999:
                return f"无效的数量：'{tok}'，应为 1~99999 的整数。"
            pairs.append((name, qty))
            name = ""
        else:
            if name:
                pairs.append((name, 1))
            name = tok
    if name:
        pairs.append((name, 1))
    return pairs


def _parse_batch_bag_add(tokens: list[str]) -> list[dict] | str:
    """解析批量放入背包参数：「名称 [数量] [重=X|价=X|备注=X] 名称 …」。

    /发放 与 /bag add 批量共用（/收回 不涉及属性，复用 _parse_batch_name_qty）。
    非属性、非纯数字 token = 下一个物品名（数量默认 1）；纯数字 token =
    前一个物品的数量；属性 token（重=/w=、价=/v=、备注=/note=）归属当前
    物品。向后兼容单件写法：`发放 长剑` = x1、`发放 长剑 2` = x2、
    `发放 长剑 价=5银` = x1 且价值 50 铜。数字出现在名称位（如 `发放 2 长剑`）
    报错。返回 [{"name","qty","weight","value","note"}, ...] 或错误文案字符串。
    """
    if not tokens:
        return "请提供物品名称。"
    items: list[dict] = []
    prev_number = False
    for tok in tokens:
        if tok.isdigit() or (tok.startswith("-") and tok[1:].isdigit()):
            if not items:
                return f"数量「{tok}」前缺少物品名称。"
            if prev_number:
                return f"数量「{tok}」重复，同一物品只能指定一次数量。"
            qty = int(tok)
            if not 1 <= qty <= 99999:
                return f"无效的数量：'{tok}'，应为 1~99999 的整数。"
            items[-1]["qty"] = qty
            prev_number = True
            continue
        key, sep, raw = tok.partition("=")
        if sep and key.lower() in ("重", "w", "价", "v", "备注", "note"):
            if not items:
                return f"属性「{tok}」前缺少物品名称。"
            k = key.lower()
            if k in ("重", "w"):
                try:
                    num = float(raw)
                except ValueError:
                    return f"无效的重量「{raw}」（应为非负数字）。"
                if num != num or num < 0:  # NaN 或负数
                    return f"无效的重量「{raw}」（应为非负数字）。"
                items[-1]["weight"] = num
            elif k in ("价", "v"):
                m = parse_money(raw)
                if m is None:
                    return f"无法解析价值「{raw}」（支持 2金5银 / 150）。"
                items[-1]["value"] = float(m)
            else:
                items[-1]["note"] = raw
            prev_number = False
            continue
        items.append(
            {"name": tok, "qty": 1, "weight": None, "value": None, "note": None}
        )
        prev_number = False
    return items


def _format_buy_result(result) -> str:
    """批量购买的单行结果（成功/失败），供批量汇总输出。"""
    if result.ok:
        stock_note = (
            f"，余 {result.stock_left} 件" if result.stock_left is not None else ""
        )
        return (
            f"✅ 已购买 {result.item_name} ×{result.qty}，"
            f"花费 {format_cp(result.total_cp)}"
            f"（{format_cp(result.price_cp)}/件）{stock_note}。"
        )
    if result.reason == "not_found":
        return f"❌ 商店里没有「{result.item_name}」。"
    if result.reason == "sold_out":
        return f"❌ 「{result.item_name}」库存不足（现有 {result.stock_left} 个）。"
    if result.reason == "no_price":
        return f"❌ 「{result.item_name}」没有定价（知识库无库价），需 DM 设价。"
    return (
        f"❌ 钱不够：购买 {result.item_name} ×{result.qty} 需"
        f" {format_cp(result.total_cp)}，还差 {format_cp(result.shortfall_cp)}。"
    )


def _format_sell_result(result) -> str:
    """批量卖回的单行结果（成功/失败），供批量汇总输出。"""
    if result.ok:
        stock_note = (
            f"，商店余 {result.stock_left} 件" if result.stock_left is not None else ""
        )
        return (
            f"💰 已卖出 {result.item_name} ×{result.qty}，"
            f"获得 {format_cp(result.pay_cp)}"
            f"（{format_cp(result.price_cp)}/件×回购系数）{stock_note}。"
        )
    if result.reason == "not_found":
        return f"❌ 商店不收「{result.item_name}」（只回收在架商品）。"
    if result.reason == "no_price":
        return f"❌ 「{result.item_name}」没有定价，暂无法回收。"
    return f"❌ 背包里没有足够的「{result.item_name}」可以出售。"


def _normalize_tool_items_base(items) -> list[dict] | str:
    """LLM items 参数的公共基础归一化（shop/inventory 共用）。

    None 返回 []（调用方回退单件 item+qty）；str 先 json.loads（部分模型
    会把数组序列化成字符串）；list 逐元素校验——元素须为 dict 且含 item
    （兼容 name 键），qty 缺省 1（数字/数字字符串均可）。
    返回 [{"item", "qty"}, ...] 或错误文案字符串；扩展字段由调用方二次校验。
    """
    if items is None:
        return []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except (ValueError, TypeError):
            return f"items 参数无法解析：{items!r}（应为物品对象数组）。"
    if not isinstance(items, list) or not items:
        return 'items 参数应为非空数组，元素为 {"item": 名称, "qty": 数量}。'
    out: list[dict] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            return f"items[{i}] 应为对象 {{item, qty, ...}}。"
        name = (raw.get("item") or raw.get("name") or "").strip()
        if not name:
            return f"items[{i}] 缺少物品名称（item 字段）。"
        try:
            qty = int(float(raw.get("qty", 1)))
        except (TypeError, ValueError):
            return f"items[{i}] 的数量「{raw.get('qty')}」不是有效整数。"
        if not 1 <= qty <= 99999:
            return f"items[{i}] 的数量「{qty}」应在 1~99999 之间。"
        out.append({"item": name, "qty": qty})
    return out


def _normalize_tool_items(items) -> list[dict] | str:
    """把 LLM 工具传的 items 归一化为 [{item, qty, price?, stock?}, ...]。

    容错：None 返回 []（调用方回退单件 item+qty）；str 先 json.loads（部分
    模型会把数组序列化成字符串）；list 逐元素校验——元素须为 dict 且含 item
    （兼容 name 键），qty 缺省 1（数字/数字字符串均可），add 可含 price
    （"2金" 或铜币整数）与 stock（数字或 "无限"）。
    返回列表或错误文案字符串。
    """
    base = _normalize_tool_items_base(items)
    if isinstance(base, str):
        return base
    if not base:
        return []
    # base 已保证 items 可遍历（str 已解析）；重新取原始 list 读扩展字段。
    raw_list = json.loads(items) if isinstance(items, str) else items
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):  # base 已校验，此处防御
            return f"items[{i}] 应为对象 {{item, qty, ...}}。"
        if "price" in raw and raw["price"] is not None:
            price = raw["price"]
            if isinstance(price, (int, float)):
                base[i]["price"] = int(price)
            else:
                m = parse_money(str(price))
                if m is None:
                    return f"items[{i}] 的价格「{price}」无法解析（支持 2金 / 150）。"
                base[i]["price"] = m
        if "stock" in raw and raw["stock"] is not None:
            s = str(raw["stock"]).strip()
            if s in ("无限", "inf", "∞"):
                base[i]["stock"] = None
            elif s.isdigit():
                base[i]["stock"] = int(s)
            else:
                return (
                    f"items[{i}] 的库存「{raw['stock']}」无法解析（支持数字或 无限）。"
                )
    return base


def _normalize_tool_inventory_items(items) -> list[dict] | str:
    """把 LLM 工具传的背包 items 归一化为 [{item, qty, weight?, value?, note?}, ...]。

    在 _normalize_tool_items_base 之上追加背包扩展字段：add 场景可含 weight
    （单件重量，非负数字）、value（单件价值，"2金5银" 或铜币整数）、note
    （备注，字符串）。返回列表或错误文案字符串。
    """
    base = _normalize_tool_items_base(items)
    if isinstance(base, str):
        return base
    if not base:
        return []
    raw_list = json.loads(items) if isinstance(items, str) else items
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):  # base 已校验，此处防御
            return f"items[{i}] 应为对象 {{item, qty, ...}}。"
        if "weight" in raw and raw["weight"] is not None:
            try:
                w = float(raw["weight"])
            except (TypeError, ValueError):
                return f"items[{i}] 的重量「{raw['weight']}」不是有效数字。"
            if w != w or w < 0:  # NaN 或负数
                return f"items[{i}] 的重量「{w}」应为非负数字。"
            base[i]["weight"] = w
        if "value" in raw and raw["value"] is not None:
            v = raw["value"]
            if isinstance(v, (int, float)):
                vv = float(v)
            else:
                m = parse_money(str(v))
                if m is None:
                    return f"items[{i}] 的价值「{v}」无法解析（支持 2金5银 / 150）。"
                vv = float(m)
            if vv != vv or vv < 0:  # NaN 或负数
                return f"items[{i}] 的价值「{v}」应为非负数字。"
            base[i]["value"] = vv
        if "note" in raw and raw["note"] is not None:
            base[i]["note"] = str(raw["note"])
    return base


def _parse_edit_tokens(tokens: list[str]) -> dict | str:
    """
    解析 /bag edit 的参数：<名称> + 至少一个 w=/v=/note= 键值对。

    返回 {"name": str, "weight": ..., "value": ..., "note": ...}：
      属性键不存在 = 不修改该字段；值为 None = 清除（用户写了 w=-/v=-/note=-）；
      否则为新值。w/v 须为非负浮点数或 '-'；note 任意字符串或 '-'。
    失败返回错误文案字符串。
    """
    positional: list[str] = []
    fields: dict[str, object] = {}
    for tok in tokens:
        key, sep, raw_val = tok.partition("=")
        if sep and key.lower() in ("w", "v", "note"):
            k = key.lower()
            if k == "note":
                fields["note"] = None if raw_val == "-" else raw_val
            else:
                if raw_val == "-":
                    fields["weight" if k == "w" else "value"] = None
                else:
                    try:
                        num = float(raw_val)
                    except ValueError:
                        return (
                            f"无效的 {k}= 值：'{raw_val}'，"
                            "应为非负数字或 -（清除）。"
                        )
                    if num != num or num < 0:  # NaN 或负数
                        return (
                            f"无效的 {k}= 值：'{raw_val}'，"
                            "应为非负数字或 -（清除）。"
                        )
                    fields["weight" if k == "w" else "value"] = num
        else:
            positional.append(tok)

    if not positional:
        return "请提供物品名称。"
    if len(positional) > 1:
        extras = "、".join(f"'{t}'" for t in positional[1:])
        return f"无法识别的参数：{extras}（物品名含空格时请用英文双引号包裹）。"
    if not fields:
        return "请至少提供一个要修改的属性（w=/v=/note=）。"
    return {"name": positional[0], **fields}


def _extract_at_target(event: AstrMessageEvent) -> str | None:
    """
    从消息链中提取第一个 At 组件的目标用户 ID（aiocqhttp 平台为 At.qq）。

    无消息链或无 At 组件时返回 None。平台相关，整体 try/except 容错；
    测试替身（FakeEvent）无 message_obj 属性时同样返回 None。
    """
    try:
        message_obj = getattr(event, "message_obj", None)
        chain = getattr(message_obj, "message", None)
        if not chain:
            return None
        for comp in chain:
            if comp.__class__.__name__ == "At":
                qq = getattr(comp, "qq", None)
                if qq is not None and str(qq) != "all":
                    return str(qq)
    except Exception:  # noqa: BLE001 — 平台差异不可枚举，容错返回 None
        return None
    return None


# 知识库条目类型 → 命令文案用中文标签。
_KB_KIND_LABEL = {
    "spell": "法术",
    "monster": "怪",
    "item": "物品",
    "feat": "专长",
    "background": "背景",
    "condition": "状态",
    "race": "种族",
}


# ---------------------------------------------------------------------------
# /筛X 特性筛选：token 宽容解析
# 每个空格分隔的 token 按优先级尝试识别：数值条件（CR/环级/距离）→
# 关键词（专注/需同调）→ 枚举表（伤害→状态→武器属性→环境→学派→怪类→
# 稀有度→形状→目标→成分，按 kind 裁剪）。识别不了的进 unknown 列表。
# ---------------------------------------------------------------------------

_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_RE_LEVEL = re.compile(r"^(?:([0-9零一二三四五六七八九])环|戏法)$")
_RE_CR = re.compile(
    r"^cr\s*([0-9]+(?:\.[0-9]+)?(?:/[0-9]+)?)(以下|以上)?$", re.IGNORECASE
)
_RE_FEET = re.compile(r"^(?:距离)?([0-9]+)尺(以上|以下|以内)?$")
# 专长属性门槛连写（如「敏捷13」「敏捷13以上」）：中文属性/英文缩写 + 数值。
_RE_FEAT_ABILITY = re.compile(
    r"^(力量|敏捷|体质|智力|感知|魅力|力|敏|体|智|感|魅|"
    r"str|dex|con|int|wis|cha)(13|19)(以上|以下)?$",
    re.IGNORECASE,
)


def _cn_digit(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def _parse_cr_token(s: str) -> float | None:
    """'3' / '0.25' / '1/4' → float；解析失败返回 None。"""
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


def _parse_monster_suffix(t: str) -> tuple[str, str] | None:
    """怪物筛怪后缀词 → (facet, canonical 值)。

    v0.45.0：火焰伤害/火焰抗性/火焰免疫/火焰易伤/震慑免疫/掘穴速度。
    「X免疫」伤害词表优先（毒素免疫→伤害免疫），未命中落状态词表
    （震慑免疫/中毒免疫→状态免疫）；词表天然不冲突（毒素=伤害、
    中毒=状态）。「X速度」经 SPEED_TYPE_CN_REV 归一为中文 tag 值。
    """
    if not t:
        return None
    for suffix in ("伤害", "抗性", "易伤", "免疫", "速度"):
        if not t.endswith(suffix):
            continue
        base = t[: -len(suffix)]
        if suffix == "免疫":
            dmg = resolve_damage_type(base)
            if dmg:
                return ("dmg_immune", dmg)
            cond_cn = resolve_condition(base)
            if cond_cn:
                return ("condition_immune", cond_cn)
            return None
        if suffix == "速度":
            key = resolve_speed_type(base)
            if key:
                return ("speed_type", SPEED_TYPE_CN_REV.get(key, key))
            return None
        canonical = resolve_damage_type(base)
        if canonical:
            facet = {
                "伤害": "dmg_dealt",
                "抗性": "dmg_resist",
                "易伤": "dmg_vuln",
            }[suffix]
            return (facet, canonical)
        return None
    return None


def _parse_filter_tokens(
    tokens: list[str], kind: str,
    feat_free_lookup: Callable[[str], str | None] | None = None,
    spell_free_lookup: Callable[[str], str | None] | None = None,
    class_free_lookup: Callable[[str], str | None] | None = None,
    subclass_free_lookup: Callable[[str], str | None] | None = None,
    race_free_lookup: Callable[[str], str | None] | None = None,
    background_free_lookup: Callable[[str], str | None] | None = None,
    monster_free_lookup: Callable[[str], str | None] | None = None,
) -> tuple[dict, list[str]]:
    """解析筛指令条件 token。

    返回 (cond, unknown)：cond 含 level/school/cr_min/cr_max/mtype/rarity/
    concentration/attunement/range_type/range_max/tags（[(facet, value), ...]）。

    feat_free_lookup：专长裸词自由文本维度判定（term → facet，None=未命中；
    由调用方查 entry_tags 值集给出，保持本函数可纯测）。
    spell_free_lookup：法术裸词标签值集判定（v0.27.0，同法术前缀词「标签」）。
    class_free_lookup / subclass_free_lookup：职业/子职裸词标签值集判定
    （v0.33.0，同对应前缀词「定位/标签」）。
    race_free_lookup / background_free_lookup：种族/背景裸词标签值集判定
    （v0.34.0，同对应前缀词「标签」）。
    monster_free_lookup：怪物裸词标签值集判定（v0.45.0，特质名/感官/
    阵营/速度类型自由词兜底）。
    """
    cond: dict = {
        "level": None, "school": None,
        "cr_min": None, "cr_max": None,
        "mtype": None, "rarity": None,
        "concentration": None, "attunement": None,
        "range_type": None, "range_max": None, "range_min": None,
        "speed_type": None, "speed_min": None, "speed_max": None,
        "darkvision_min": None, "_darkvision_pending": False,
        "tags": [], "unknown": [],
        "spell_class": None,  # v0.35.0 职业法术表反查（仅法术）
    }
    # 各 kind 可用的枚举解析器（按优先级排序）
    enum_parsers: list[tuple[str, object]] = []
    if kind == "monster":
        enum_parsers = [
            ("dmg_dealt", resolve_damage_type),
            ("condition_inflict", resolve_condition),
            ("environment", resolve_environment),
            ("mtype", resolve_monster_type),
            # v0.45.0：伤害/状态防御维度 + 速度/感官/阵营（筛怪 6 维）
            ("dmg_resist", resolve_damage_type),
            ("dmg_immune", resolve_damage_type),
            ("dmg_vuln", resolve_damage_type),
            ("condition_immune", resolve_condition),
            ("speed_type", resolve_speed_type),
            ("sense_type", resolve_sense_type),
            ("alignment", resolve_alignment),
        ]
    elif kind == "spell":
        enum_parsers = [
            ("dmg_dealt", resolve_damage_type),
            ("condition_inflict", resolve_condition),
            ("school", resolve_school),
            ("spell_shape", resolve_shape),
            ("spell_target", resolve_target),
            ("spell_component", resolve_component),
            ("spell_keyword", resolve_spell_keyword),
        ]
    elif kind == "item":
        enum_parsers = [
            ("dmg_dealt", resolve_damage_type),
            ("condition_inflict", resolve_condition),
            ("weapon_property", resolve_property),
            ("item_type", resolve_item_type),
            ("rarity", resolve_rarity),
        ]
    elif kind == "race":
        enum_parsers = [
            # 种族语境：伤害类型 = 天生抗性（非造成伤害）
            ("dmg_resist", resolve_damage_type),
            ("size", resolve_size),
            ("creature_type", resolve_creature_type),
            ("speed_type", resolve_speed_type),
            # v0.34.0：语义标签（飞行/变形/水陆两栖/魅力…，speed_type 未覆盖的词）
            ("race_keyword", resolve_race_keyword),
        ]
    elif kind == "feat":
        enum_parsers = [
            ("feat_type", resolve_feat_type),
            ("ability_increase", resolve_ability),
        ]
    elif kind == "class":
        # 职业：定位（武者/奥法/神职/专家）与关键字（近战/治疗/狂暴…）都可裸词。
        enum_parsers = [
            ("class_role", resolve_class_role),
            ("class_keyword", resolve_class_keyword),
        ]
    elif kind == "subclass":
        enum_parsers = [
            ("subclass_keyword", resolve_subclass_keyword),
        ]
    elif kind == "background":
        # v0.34.0：背景语义标签（技能/身份/工具/起始专长…）裸词直接反查。
        enum_parsers = [
            ("background_keyword", resolve_background_keyword),
        ]

    # 专长筛选的前缀词：修饰下一个 token 归属的维度（如「类型 战斗风格」、
    # 「前置专长 巨人打击」）；裸词则按 enum → 自由文本维度顺序自动解析。
    _FEAT_PREFIX = {
        "类型": "feat_type",
        "提升": "ability_increase",
        "属性": "ability_increase",
        "前置种族": "prereq_race",
        "种族": "prereq_race",
        "前置属性": "prereq_ability",
        "前置专长": "prereq_feat",
        "专长": "prereq_feat",
        "前置特性": "prereq_feature",
        "特性": "prereq_feature",
        "标签": "feat_keyword",
        "关键字": "feat_keyword",
        "关键词": "feat_keyword",
    }
    # 法术筛选的前缀词（v0.27.0）：标签维度显式指定（如「标签 控场」），
    # 避免与学派（「防护」学派 vs 防护大类）等既有维度混淆。
    # v0.35.0：加「职业」→ 职业法术表反查（如「职业 法师」）。
    _SPELL_PREFIX = {
        "标签": "spell_keyword",
        "关键字": "spell_keyword",
        "关键词": "spell_keyword",
        "职业": "spell_class",
    }
    # 职业筛选的前缀词（v0.33.0）：定位维度显式指定（如「定位 武者」），
    # 标签维度与裸词一致（「标签 近战」）。
    _CLASS_PREFIX = {
        "定位": "class_role",
        "标签": "class_keyword",
        "关键字": "class_keyword",
        "关键词": "class_keyword",
    }
    # 子职筛选的前缀词（v0.33.0）：仅标签维度（如「标签 治疗」）。
    _SUBCLASS_PREFIX = {
        "标签": "subclass_keyword",
        "关键字": "subclass_keyword",
        "关键词": "subclass_keyword",
    }
    # 种族/背景筛选的前缀词（v0.34.0）：仅标签维度（如「标签 变形」/
    # 「标签 隐匿」），与裸词语义一致，显式指定避免与既有维度混淆。
    _RACE_PREFIX = {
        "标签": "race_keyword",
        "关键字": "race_keyword",
        "关键词": "race_keyword",
    }
    _BACKGROUND_PREFIX = {
        "标签": "background_keyword",
        "关键字": "background_keyword",
        "关键词": "background_keyword",
    }
    feat_pending: str | None = None  # 前缀词后的维度
    feat_ability_name: str | None = None  # prereq_ability 暂存属性名（等待数字）
    feat_skip: set[int] = set()  # 已被「属性 数字」组合消耗的下标
    spell_pending: str | None = None  # 法术前缀词后的维度
    class_pending: str | None = None  # 职业/子职前缀词后的维度
    race_pending: str | None = None  # 种族/背景前缀词后的维度

    for idx, tok in enumerate(tokens):
        if idx in feat_skip:
            continue
        t = (tok or "").strip()
        if not t:
            continue
        # 1) 数值条件
        if kind == "monster":
            m = _RE_CR.match(t)
            if m:
                cr = _parse_cr_token(m.group(1))
                if cr is not None:
                    suffix = m.group(2)
                    if suffix == "以下":
                        cond["cr_max"] = cr
                    elif suffix == "以上":
                        cond["cr_min"] = cr
                    else:
                        cond["cr_min"] = cond["cr_max"] = cr
                continue
            # v0.45.0：后缀词（火焰伤害/火焰抗性/火焰免疫/火焰易伤/
            # 震慑免疫/掘穴速度），先于枚举解析（避免「震慑免疫」误走状态施加）
            suffix_hit = _parse_monster_suffix(t)
            if suffix_hit:
                cond["tags"].append(suffix_hit)
                continue
        if kind == "spell":
            m = _RE_LEVEL.match(t)
            if m:
                cond["level"] = 0 if m.group(0) == "戏法" else _cn_digit(m.group(1))
                continue
            m = _RE_FEET.match(t)
            if m:
                feet = int(m.group(1))
                suffix = m.group(2)
                if suffix == "以上":
                    cond["range_min"] = feet
                else:  # 裸 N尺 / 以下 / 以内 → 最大距离
                    cond["range_max"] = feet
                continue
            if t in ("触碰", "自身", "特殊"):
                cond["range_type"] = t
                continue
            # 独立「以上」修饰前一个距离 token（如「10尺 以上」）
            if t in ("以上", "以下") and cond["range_max"] is not None:
                if t == "以上":
                    cond["range_min"] = cond["range_max"]
                    cond["range_max"] = None
                continue
        if kind == "race":
            m = _RE_FEET.match(t)
            if m:
                feet = int(m.group(1))
                suffix = m.group(2)
                # 黑暗视觉 60尺：跟在「黑暗视觉」词后的首个距离 → 距离下限
                if cond["_darkvision_pending"]:
                    cond["_darkvision_pending"] = False
                    cond["darkvision_min"] = feet
                # 速度语义（与 /筛法术 距离相反）：裸 N尺 / 以上 = 至少 N 尺
                elif suffix == "以下":
                    cond["speed_max"] = feet
                else:
                    cond["speed_min"] = feet
                continue
            # 独立「以上/以下」修饰前一个速度 token（如「40尺 以下」→ 至多 40）
            if t in ("以上", "以下") and cond["speed_min"] is not None:
                if t == "以下":
                    cond["speed_max"] = cond["speed_min"]
                    cond["speed_min"] = None
                continue
        if kind == "feat":
            # 裸词「敏捷 13」分离形式：属性名 + 纯数字 → 属性门槛（先于提升拦截）
            if (
                feat_pending is None and feat_ability_name is None
                and idx + 1 < len(tokens)
                and resolve_ability(t)
                and tokens[idx + 1].strip().isdigit()
            ):
                cond["tags"].append((
                    "prereq_ability",
                    f"{resolve_ability(t)} {tokens[idx + 1].strip()}",
                ))
                feat_skip.add(idx + 1)
                continue
            # 前缀词 → 设置 pending 维度（如「前置专长 巨人打击」）
            if t in _FEAT_PREFIX:
                feat_pending = _FEAT_PREFIX[t]
                continue
            # prereq_ability 分离形式：属性名暂存，等下一个数字 token
            if feat_pending == "prereq_ability" and feat_ability_name is None:
                ab = resolve_ability(t)
                if ab:
                    feat_ability_name = ab
                    continue
            # 属性门槛连写（如「敏捷13」「敏捷13以上」）
            m = _RE_FEAT_ABILITY.match(t)
            if m and (feat_pending == "prereq_ability" or feat_ability_name):
                ab = resolve_ability(m.group(1)) or feat_ability_name
                if ab:
                    cond["tags"].append(("prereq_ability", f"{ab} {m.group(2)}"))
                    feat_pending = feat_ability_name = None
                    continue
            # pending 维度：自由文本直接作值（种族/专长/特性名/标签）
            if feat_pending in ("prereq_race", "prereq_feat", "prereq_feature"):
                cond["tags"].append((feat_pending, t))
                feat_pending = None
                continue
            if feat_pending == "feat_keyword":
                # 标签值先归一（「重武器」→「重型」），未收录词原样保留
                canonical = resolve_feat_keyword(t) or t
                cond["tags"].append(("feat_keyword", canonical))
                feat_pending = None
                continue
            if feat_pending == "prereq_ability" and feat_ability_name:
                # 「敏捷 13」分离形式收尾：属性名 + 当前数字 token
                if t.isdigit():
                    cond["tags"].append(("prereq_ability", f"{feat_ability_name} {t}"))
                    feat_pending = feat_ability_name = None
                    continue
        # 法术前缀词 → 设置 pending 维度（如「标签 控场」「职业 法师」）
        if kind == "spell" and t in _SPELL_PREFIX:
            spell_pending = _SPELL_PREFIX[t]
            continue
        if kind == "spell" and spell_pending == "spell_keyword":
            # 标签值先归一（「控制」→「控场」），未收录词原样保留
            canonical = resolve_spell_keyword(t) or t
            cond["tags"].append(("spell_keyword", canonical))
            spell_pending = None
            continue
        if kind == "spell" and spell_pending == "spell_class":
            # 职业法术表反查（v0.35.0）：职业名在调用方解析为库内中文名
            cond["spell_class"] = t
            spell_pending = None
            continue
        # 职业/子职前缀词 → 设置 pending 维度（如「定位 武者」「标签 近战」）。
        # 「标签/关键字/关键词」在 _CLASS_PREFIX 与 _SUBCLASS_PREFIX 都有定义，
        # 必须按 kind 分流，否则子职会被路由到 class_keyword facet。
        if kind in ("class", "subclass") and (
            t in _CLASS_PREFIX or t in _SUBCLASS_PREFIX
        ):
            class_pending = (
                _SUBCLASS_PREFIX.get(t)
                if kind == "subclass"
                else _CLASS_PREFIX.get(t)
            )
            continue
        if kind in ("class", "subclass") and class_pending is not None:
            # 标签值先归一（「潜行」→「隐匿」），未收录词原样保留
            if kind == "subclass":
                canonical = resolve_subclass_keyword(t) or t
            else:
                canonical = resolve_class_keyword(t) or resolve_class_role(t) or t
            cond["tags"].append((class_pending, canonical))
            class_pending = None
            continue
        # 种族/背景前缀词 → 设置 pending 维度（如「标签 变形」「标签 隐匿」）。
        if kind in ("race", "background") and (
            t in _RACE_PREFIX or t in _BACKGROUND_PREFIX
        ):
            race_pending = (
                _BACKGROUND_PREFIX.get(t)
                if kind == "background"
                else _RACE_PREFIX.get(t)
            )
            continue
        if kind in ("race", "background") and race_pending is not None:
            # 标签值先归一（「夜视」→「黑暗视觉」），未收录词原样保留
            canonical = (
                resolve_background_keyword(t) if kind == "background"
                else resolve_race_keyword(t)
            ) or t
            cond["tags"].append((race_pending, canonical))
            race_pending = None
            continue
        # 2) 关键词
        if kind == "spell" and t == "专注":
            cond["concentration"] = True
            continue
        if kind == "item" and t in ("需同调", "同调"):
            cond["attunement"] = True
            continue
        if kind == "race" and t in ("黑暗视觉", "夜视"):
            cond["darkvision_min"] = 1
            cond["_darkvision_pending"] = True
            continue
        # 3) 枚举表
        matched = False
        for key, resolver in enum_parsers:
            canonical = resolver(t)
            if canonical:
                if key == "speed_type":
                    # 种族：数值筛选走 cond（英文键查侧表列）；
                    # 怪物：类型筛选走 tags，值归一为中文（构建期 tag 值为中文）。
                    if kind == "race":
                        cond[key] = canonical
                    else:
                        cond["tags"].append(
                            (key, SPEED_TYPE_CN_REV.get(canonical, canonical))
                        )
                elif key in ("mtype", "school", "rarity", "range_type"):
                    cond[key] = canonical
                elif key in ("spell_shape", "spell_target", "spell_component"):
                    cond["tags"].append((key, canonical))
                else:  # dmg_dealt / condition_inflict / environment / weapon_property /
                    #      size / creature_type / dmg_resist / dmg_immune / dmg_vuln /
                    #      condition_immune / sense_type / alignment
                    cond["tags"].append((key, canonical))
                matched = True
                break
        if matched:
            continue
        # 专长裸词：自由文本维度判定（查值集：种族 → 特性 → 前置专长名 → 标签）
        if kind == "feat" and feat_free_lookup is not None:
            # 标签先归一（「重武器」→「重型」），未收录词原样探测
            probe = resolve_feat_keyword(t) or t
            dim = feat_free_lookup(probe)
            if dim:
                # feat_keyword 值用 canonical（命中词表别名时），其余维度用原词
                cond["tags"].append((dim, probe if dim == "feat_keyword" else t))
                continue
        # 法术裸词：标签值集判定（v0.27.0；enum 未命中时的兜底，覆盖 AI 自由词）
        if kind == "spell" and spell_free_lookup is not None:
            probe = resolve_spell_keyword(t) or t
            dim = spell_free_lookup(probe)
            if dim:
                cond["tags"].append((dim, probe if dim == "spell_keyword" else t))
                continue
        # 职业裸词：标签值集判定（v0.33.0；enum 未命中时的兜底，覆盖 AI 自由词）
        if kind == "class" and class_free_lookup is not None:
            probe = resolve_class_keyword(t) or t
            dim = class_free_lookup(probe)
            if dim:
                cond["tags"].append((dim, probe if dim == "class_keyword" else t))
                continue
        # 子职裸词：标签值集判定（v0.33.0；enum 未命中时的兜底，覆盖 AI 自由词）
        if kind == "subclass" and subclass_free_lookup is not None:
            probe = resolve_subclass_keyword(t) or t
            dim = subclass_free_lookup(probe)
            if dim:
                cond["tags"].append((dim, probe))
                continue
        # 种族裸词：标签值集判定（v0.34.0；enum 未命中时的兜底，覆盖 AI 自由词）
        if kind == "race" and race_free_lookup is not None:
            probe = resolve_race_keyword(t) or t
            dim = race_free_lookup(probe)
            if dim:
                # race_keyword 值用 canonical（命中词表别名时），其余维度用原词
                cond["tags"].append((dim, probe if dim == "race_keyword" else t))
                continue
        # 背景裸词：标签值集判定（v0.34.0；enum 未命中时的兜底，覆盖 AI 自由词）
        if kind == "background" and background_free_lookup is not None:
            probe = resolve_background_keyword(t) or t
            dim = background_free_lookup(probe)
            if dim:
                cond["tags"].append((dim, probe))
                continue
        # 怪物裸词：标签值集判定（v0.45.0；enum 未命中时的兜底，覆盖
        # 特质名/感官/阵营/速度类型自由词，如「再生」「真实视觉」「守序善良」）
        if kind == "monster" and monster_free_lookup is not None:
            dim = monster_free_lookup(t)
            if dim:
                cond["tags"].append((dim, t))
                continue
        cond["unknown"].append(t)
    return cond, cond["unknown"]


# 筛指令展示用类别标签（区别于 /查X 的 _KB_KIND_LABEL）。
_FILTER_KIND_LABEL = {
    "spell": "法术",
    "monster": "怪物",
    "item": "物品",
    "race": "种族",
    "feat": "专长",
    "class": "职业",
    "subclass": "子职",
    "background": "背景",
}

# /筛X 用法帮助，{p} 为显示前缀。
_FILTER_HELP = {
    "monster": (
        "筛怪用法：{p}筛怪 <条件...>，条件用空格分隔、可组合\n"
        "  伤害：火焰/寒冷/暗蚀…    伤害细分：火焰伤害/火焰抗性/火焰免疫/火焰易伤\n"
        "  状态免疫：震慑免疫/中毒免疫…    状态：魅惑/恐慌/中毒/麻痹/震慑…\n"
        "  速度类型：掘穴速度/飞行速度/游泳速度…    感官：真实视觉/黑暗视觉/盲视…\n"
        "  阵营：守序善良/混乱邪恶/任意阵营…    特性名：再生/魔法抗性…\n"
        "  怪类：龙类/不死生物/野兽…          CR：CR5、CR5以下、CR5以上\n"
        "示例：{p}筛怪 火焰 CR5以下、{p}筛怪 火焰免疫、{p}筛怪 真实视觉、"
        "{p}筛怪 守序善良"
    ),
    "spell": (
        "筛法术用法：{p}筛法术 <条件...>，条件用空格分隔、可组合\n"
        "  伤害类型：火焰/寒冷/暗蚀…   状态：魅惑/恐慌…\n"
        "  成分：言语/姿势/材料   专注   环级：戏法、3环\n"
        "  距离：30尺、30尺以上、触碰、自身   形状：球形/锥形/线形\n"
        "  目标：单体/多体/自我   学派：塑能/死灵/防护…\n"
        "  能力标签（裸词自动反查，或 {p}筛法术 标签 <词>）：\n"
        "    控场：束缚/定身/减速/击倒/震慑   治疗：治疗/回复/复活\n"
        "    增益：护甲/优势/免疫/加速   减益：诅咒/劣势/虚弱\n"
        "    召唤/位移/防护：召唤/传送/护盾/结界   侦查/潜行：侦测/隐形/隐匿\n"
        "    社交/探索/幻术/即死：魅惑/开锁/幻象/即死   造物/战斗辅助/施法辅助\n"
        "  职业法术表（v0.35.0）：{p}筛法术 职业 <职业名>（如 职业 法师）\n"
        "示例：{p}筛法术 专注 3环 火焰、{p}筛法术 控场 3环、"
        "{p}筛法术 标签 治疗、{p}筛法术 言语 10尺、{p}筛法术 职业 法师 3环"
    ),
    "item": (
        "筛物品用法：{p}筛物品 <条件...>，条件用空格分隔、可组合\n"
        "  伤害类型：挥砍/暗蚀/火焰…   状态：魅惑/恐慌…\n"
        "  武器属性：灵巧/重型/轻型/双手/投掷/两用…\n"
        "  物品大类：武器/盾牌/重甲/中甲/轻甲/法器/权杖/魔杖/戒指/药水/卷轴…\n"
        "  基础物品名：{p}筛物品 长剑（列出全部长剑系魔法武器）\n"
        "  稀有度：普通/非普通/珍稀/极珍稀/传说/神器/非魔法物品/魔法物品/未知   需同调\n"
        "示例：{p}筛物品 暗蚀 灵巧、{p}筛物品 长剑 传说、{p}筛物品 武器"
    ),
    "race": (
        "筛种族用法：{p}筛种族 <条件...>，条件用空格分隔、可组合\n"
        "  伤害类型（天生抗性）：火焰/寒冷/暗蚀/毒素…\n"
        "  体型：微型/小型/中型/大型   生物类型：类人生物/妖精/亡灵/构装…\n"
        "  速度类型：飞行/攀爬/游泳/掘穴（缺省步行）   速度：40尺、40尺以下\n"
        "  黑暗视觉：黑暗视觉（有无）、黑暗视觉 60尺（≥60）\n"
        "  天生施法：{p}筛种族 迷踪步（法术名直接反查）\n"
        "  能力标签（裸词自动反查，或 {p}筛种族 标签 <词>）：\n"
        "    属性倾向：力量/敏捷/体质/智力/感知/魅力\n"
        "    战斗方式：近战/远程/施法/坦克/爆发/隐匿/持盾/双持/徒手/天生武器\n"
        "    防御生存：天生护甲/护甲/生命/再生/魔法抗性/坚韧   机动：飞行/跳跃/传送/水陆两栖\n"
        "    主题风味：龙/亡灵/恶魔/精类/巨人/元素/神圣/火焰/寒冰/海洋/机械/灵能…\n"
        "    特殊机制：变形/狂暴/语言/黑暗视觉/天生施法/强力构筑/日照敏感/长寿/隐形…\n"
        "示例：{p}筛种族 飞行 60尺、{p}筛种族 火焰 中型、{p}筛种族 变形、"
        "{p}筛种族 标签 水陆两栖"
    ),
    "feat": (
        "筛专长用法：{p}筛专长 <条件...>，条件用空格分隔、可组合\n"
        "  类型：通用/起源/战斗风格/传奇恩惠/黑暗赠礼/龙纹\n"
        "  属性提升：力量/敏捷/体质/智力/感知/魅力（如：{p}筛专长 力量）\n"
        "  先决条件（裸词自动判定，或加前缀指定维度）：\n"
        "    种族：{p}筛专长 半身人   特性：{p}筛专长 特性 战斗风格\n"
        "    专长：{p}筛专长 前置专长 巨人打击\n"
        "    属性：{p}筛专长 前置属性 敏捷13（或 敏捷 13）\n"
        "  能力标签（裸词自动反查，或 {p}筛专长 标签 <词>）：\n"
        "    攻击方式：近战/远程/徒手/投掷/双持/双手/重型/灵巧/轻型/触及/持盾\n"
        "    战斗输出：伤害/命中/重击/击杀/额外攻击/附赠动作/反应/借机攻击/先攻\n"
        "    防御生存：防御/护甲/生命/治疗/豁免/抗性/减伤   机动：速度/位移/跳跃/逃脱/潜行\n"
        "    控场：击倒/束缚/减速/缴械   施法：戏法/法术位/专注/法术攻击/仪式\n"
        "    技能/探索/特殊：专精/工具/隐匿/说服/侦查/负重/幸运/坐骑/同伴/狂暴…\n"
        "示例：{p}筛专长 远程、{p}筛专长 近战 伤害、{p}筛专长 标签 机动、"
        "{p}筛专长 力量 起源、{p}筛专长 前置属性 力量13"
    ),
    "class": (
        "筛职业用法：{p}筛职业 <条件...>，条件用空格分隔、可组合\n"
        "  职业定位（裸词自动反查，或 {p}筛职业 定位 <词>）：\n"
        "    武者：战士/野蛮人/武僧   奥法：法师/术士/魔契师\n"
        "    神职：牧师/圣武士/德鲁伊   专家：游侠/游荡者/吟游诗人/奇械师\n"
        "  能力标签（裸词自动反查，或 {p}筛职业 标签 <词>）：\n"
        "    战斗方式：近战/远程/徒手/双持/双手/重甲/中甲/轻型护甲/持盾/坦克/爆发/先攻\n"
        "    施法：施法/奥术施法/神术施法/自然施法/契约施法/仪式/戏法/专注/法术位/法术攻击\n"
        "    辅助：治疗/增益/减益/防护/驱散/召唤/结界/圣疗/激励\n"
        "    技能倾向：技能/隐匿/社交/探索/追踪/开锁/求生/驯兽/侦查/说服/威吓/巧手/奥秘/宗教/医药/自然\n"
        "    属性依赖：力量/敏捷/体质/智力/感知/魅力\n"
        "    特殊机制：狂暴/气力/偷袭/变形/魔宠/野兽伙伴/魔契/智械/炼金/枪械/灵能/龙纹/神术通道\n"
        "示例：{p}筛职业 武者、{p}筛职业 近战 爆发、{p}筛职业 奥术施法 智力、"
        "{p}筛职业 标签 治疗"
    ),
    "subclass": (
        "筛子职用法：{p}筛子职 <条件...>，条件用空格分隔、可组合\n"
        "  定位倾向：近战/远程/施法/坦克/治疗/辅助/控场/爆发/隐匿/召唤/变形/宠物/侦查/社交/探索/位移/结界/诅咒\n"
        "  主题风味：神圣/黑暗/元素/自然/奥术/战术/龙/亡灵/恶魔/精类/巨人/鲜血/暗影/风暴/火焰/寒冰/闪电/雷鸣/强酸/毒素/光耀/生命/死亡/知识/战争/诡术/梦境/星辰/大地/海洋/月亮/孢子/野兽/植物/武器/格斗/箭术/瘟疫/鬼火\n"
        "  特色机制：狂暴/偷袭/气力/灵感/魔宠/野兽伙伴/变形/魔契/龙纹/造物/亡者/附体/祝福/元素召唤/神圣通道/奥术传承/龙语/机关/枪械/炼金/灵能\n"
        "示例：{p}筛子职 治疗 神圣、{p}筛子职 塑能、{p}筛子职 标签 召唤、"
        "{p}筛子职 暗影 位移"
    ),
    "background": (
        "筛背景用法：{p}筛背景 <条件...>，条件用空格分隔、可组合\n"
        "  属性倾向：力量/敏捷/体质/智力/感知/魅力（2024 背景按加权主属性）\n"
        "  技能倾向（裸词自动反查，或 {p}筛背景 标签 <词>）：\n"
        "    技能：体操/驯兽/奥秘/运动/欺瞒/历史/洞悉/威吓/调查/医药/自然/察觉/表演/说服/宗教/巧手/隐匿/求生\n"
        "  身份主题：贵族/罪犯/士兵/学者/艺人/工匠/农民/水手/商人/隐士/教士/盗贼/佣兵/海盗/骑士/间谍/猎人/医生/律师/政客/赌徒/流浪儿…\n"
        "  工具装备：赌具/乐器/盗贼工具/易容工具/文书伪造工具/草药工具/领航工具/毒药工具/铁匠工具/修补工具…\n"
        "  特殊机制：起始专长/语言/多语言/声望/地位/财富/人脉/组织/公会/教团/学院/家族/骑士团/密探/伪装/仆从/随从\n"
        "示例：{p}筛背景 隐匿 盗贼工具、{p}筛背景 贵族、{p}筛背景 标签 起始专长"
    ),
}


# /帮助 内容：分组指令大全（群聊玩家可随时查询）。
# cmds = 该组命令名列表（概览用）；lines = 详细语法（/帮助 <组名> 用）。
_HELP_SECTIONS = {
    "知识库": {
        "cmds": "/查法术 /查怪 /查物品 /查专长 /查背景 /查职业 /查状态 /查种族 /查询 /筛怪 /筛法术 /筛物品 /筛种族 /筛专长 /筛职业 /筛子职 /筛背景 /kb",
        "lines": [
            "/查法术 <名称>          查询法术效果（同名返回 2014/2024 全部版本）",
            "/查怪 <名称>            查询怪物数据与能力（特性/动作/吐息等）",
            "/查物品 <名称>          查询魔法物品（稀有度、同调、效果）",
            "/查专长 <名称>          查询专长",
            "/查背景 <名称>          查询背景",
            "/查职业 <职业> [子职|版本|等级段|特性]  默认返回分层概要总表（第1~4层，每行「N级 名称：一句话概要」，版本随群规则/最新版）；第二参数可钻取：子职（战士 勇士）、版本（战士 2024）、层级（战士 第2层）、等级段（战士 5级）、特性全文（战士 特性，按层级分条发送；战士 特性 动作如潮 查看单个特性）",
            "/查状态 <名称>          查询状态（目盲/魅惑…，2014/2024 双版本）",
            "/查种族 <名称>          查询种族（体型/速度/黑暗视觉/抗性/天生施法）",
            "/查询 <关键词> [-全文]  跨法术/怪物/物品等全部类别广搜，-全文 额外搜正文",
            "/筛怪 <条件...>         按特性反查怪物，如：/筛怪 火焰 CR5以下、/筛怪 火焰免疫、/筛怪 震慑免疫、/筛怪 掘穴速度、/筛怪 再生、/筛怪 真实视觉、/筛怪 守序善良",
            "/筛法术 <条件...>       按特性/能力标签反查法术，如：/筛法术 专注 3环、/筛法术 控场 3环、/筛法术 标签 治疗",
            "/筛物品 <条件...>       按特性反查物品，如：/筛物品 暗蚀 灵巧",
            "/筛种族 <条件...>       按特性/能力标签反查种族，如：/筛种族 飞行 60尺、/筛种族 迷踪步、/筛种族 变形、/筛种族 标签 水陆两栖",
            "/筛专长 <条件...>       反查专长类型/属性提升/先决条件/能力标签，如：/筛专长 远程、/筛专长 近战 伤害、/筛专长 力量 起源",
            "/筛职业 <条件...>       反查职业定位/能力标签/属性依赖，如：/筛职业 武者、/筛职业 近战 爆发、/筛职业 奥术施法 智力",
            "/筛子职 <条件...>       反查子职定位倾向/主题风味/特色机制，如：/筛子职 治疗 神圣、/筛子职 塑能",
            "/筛背景 <条件...>       反查背景技能/身份/工具/起始专长标签，如：/筛背景 隐匿 盗贼工具、/筛背景 贵族、/筛背景 标签 起始专长",
            "/kb version            查看知识库版本与数据来源",
            "/kb reload             重载私设（房规）目录；/kb 私设 查看概况",
            "记不全名称可用部分名称或英文名查询，错别字自动容错。",
        ],
    },
    "先攻": {
        "cmds": "/ri /init",
        "lines": [
            "/ri                     掷 d20 先攻入列（有活跃卡时自动带卡上先攻）",
            "/ri +3                  掷 d20+3 先攻入列",
            "/ri +2 食人魔           为具名单位代掷入列",
            "/ri 15 哥布林甲         以固定值录入（DM 抄书值）",
            "/init                   查看先攻列表",
            "/init end               推进回合（绕回时轮数 +1）",
            "/init del <名称>        移除单位（白名单权限）",
            "/init clr               清空本场战斗（白名单权限）",
        ],
    },
    "背包": {
        "cmds": "/bag /背包",
        "lines": [
            "/bag                    查看自己的背包",
            "/bag list @某人         查看他人背包（只读）",
            "/bag add <名称> <数量> [w=重量] [v=价值] [note=备注]  放入物品",
            "/bag rm <名称> [数量]   取出物品（归零自动删除）",
            "/bag edit <名称> [w=|-] [v=|-] [note=|-]  修改物品属性",
            "/bag give @某人 <名称> [数量]  赠送物品",
            "/bag party              查看队伍背包",
            "/bag put/take <名称> [数量]  存入/取出队伍背包",
            "/bag clear              清空自己的背包",
            "/bag party clear        清空队伍背包（白名单权限）",
        ],
    },
    "商店": {
        "cmds": "/商店",
        "lines": [
            "/商店                  查看商品列表（库价/覆盖价，库存余量，每页 30 条）",
            "/商店 2                 翻页（或 /商店 页 2）",
            "/商店 买 <名称> [数量] [<名称> [数量] …]   购买（自动从背包扣除金银铜并找零；可批量）",
            "/商店 卖 <名称> [数量] [<名称> [数量] …]   卖回商店（只收在架商品，按售价×回购系数付款；可批量）",
            "/商店 初始化            用 PHB/XPHB 非魔法物品重建商店（管理员，覆盖现有列表）",
            "/商店 上架 <名称> [价=] [库存=] [<名称> …]  添加商品，可批量（不写价则用库价；管理员）",
            "/商店 下架 <名称> [<名称> …]  移除商品，可批量（管理员）",
            "/商店 设价 <名称> <价格|自动>  覆盖价格（如 2金5银；「自动」恢复库价；管理员）",
            "/商店 设库存 <名称> <数量|无限>  设置库存（0=售罄；管理员）",
            "/商店 回购率 <系数>      设置回购系数，如 0.5=半价（管理员）",
            "/商店 清空              清空整店全部商品（管理员；回购系数保留）",
            "价格单位：1金币=10银币=100铜币；背包价值字段统一为铜币口径。",
        ],
    },
    "角色卡": {
        "cmds": "/卡 /车卡 /车卡规则",
        "lines": [
            "/卡                    查看活跃角色卡",
            "/卡 列表               列出全部角色卡（⭐ 为活跃卡）",
            "/卡 用 <卡名>          切换活跃卡",
            "/卡 删 <卡名>          删除角色卡",
            "/卡 改名 <旧名> <新名>  重命名",
            "/卡 设 <字段> <值>     设置 hp/ac/速度/法术位N/攻击/主手/副手/护甲/背景/种族/职业/版本/六维属性（力量/敏捷/体质/智力/感知/魅力）/生平/背景故事/阵营/语言/信仰/年龄/性别/身高/体重/生命骰已用/激励/先攻/法术（hp/ac/速度/先攻为房规加值，其余直接覆盖）",
            "/卡 详情 [字段]       查看完整字段：生平/人物信息/专长/特性（种族+职业）/攻击/熟练/语言/装备/法术（卡面折叠的全文）",
            "/卡 升级 [职业名]     指定职业 +1 级（默认主职业），战斗字段自动重算",
            "/卡 降级 [职业名]     指定职业 -1 级（默认主职业，最低 1 级），战斗字段自动重算",
            "/卡 熟练 技能|豁免 +名 -名   增减熟练",
            "/卡 法术 加|删 <环阶> <法术名>  已知法术单条增删（v0.31.0）",
            "/卡 骰 <名称> <表达式>  命名掷骰（登记后 /r <名称> 直接掷；名 - 删除，v0.32.0）",
            "/车卡                  开始/继续车卡引导（LLM 逐步提问）",
            "/车卡 状态             查看引导进度",
            "/车卡 答 <答案>         提交当前步答案（无 LLM 时兜底）",
            "/卡 导入 / /车卡 导入 <卡文本>  把文本角色卡直接落库（战斗字段自动重算）",
            "/车卡 取消             中止引导",
            "/车卡规则 [设置]       查看/设置群开卡规则（版本/属性生成/子职/起始等级/起始金币，需管理权限）",
            "/r 力量 /r 察觉 /r 敏捷豁免   用活跃卡自动加修正掷骰",
            "/r 攻击 [武器名|列表]   主手武器/指定武器攻击检定（用卡上攻击加值）",
            "战斗字段（HP/AC/法术位/攻击）由规则引擎按职业/装备自动计算；/卡 设 仅作房规加值调整",
        ],
    },
    "骰子": {
        "cmds": "/r /roll /dnd /dset /rprefix",
        "lines": [
            "/r [表达式]             掷骰（d20、4d6kh3、2d20kl1、5d6!!、3d6>3…）",
            "/r 3#d20+d6             重复投掷 3 次（上限 20）",
            "/r 3d6*(2+4)d12         四则运算 + 括号；括号可作骰数/骰面",
            "/r d20 感知 15          技能检定（标签 + DC，输出成功/失败）",
            "/r 攻击 长剑 15         角色卡攻击检定（默认主手武器，可指定武器 + DC）",
            "/r d20优势 /r d20劣势   中文优势/劣势（紧贴后缀，等价 d20adv / d20dis）",
            "/dnd [组数]             按 5e 规则掷属性（4d6kh3×6 为一组，默认 1 组，上限 20）",
            "/dset [面数]            查询/设置本会话默认骰面数",
            "/rprefix [符号]         查询/设置自定义触发前缀（如 .r）",
        ],
    },
    "历史": {
        "cmds": "/rh",
        "lines": [
            "/rh                    查看投掷历史",
            "/rh me                 只看自己的投掷记录",
            "/rh clear              清空历史（群聊需白名单权限）",
        ],
    },
    "帮助": {
        "cmds": "/帮助",
        "lines": [
            "/帮助                  查看指令大全（本帮助）",
            "/帮助 <组名>            查看某组指令的详细语法",
            "可用组：知识库 / 先攻 / 背包 / 角色卡 / 骰子 / 历史",
        ],
    },
}

_HELP_TOPIC_ALIAS = {
    "知识库": "知识库", "kb": "知识库", "查": "知识库", "知识": "知识库",
    "筛": "知识库", "查询": "知识库", "搜索": "知识库",
    "先攻": "先攻", "init": "先攻", "initiative": "先攻", "ri": "先攻",
    "背包": "背包", "bag": "背包", "inventory": "背包",
    "商店": "商店", "shop": "商店", "店铺": "商店",
    "角色卡": "角色卡", "char": "角色卡", "character": "角色卡",
    "车卡": "角色卡", "chargen": "角色卡", "开卡": "角色卡", "卡": "角色卡",
    "骰子": "骰子", "dice": "骰子", "roll": "骰子", "r": "骰子",
    "历史": "历史", "history": "历史", "rh": "历史",
    "帮助": "帮助", "help": "帮助", "菜单": "帮助",
}

_HELP_EMOJI = {
    "知识库": "📚",
    "先攻": "⚔️",
    "背包": "🎒",
    "商店": "🏪",
    "角色卡": "🧙",
    "骰子": "🎲",
    "历史": "📜",
    "帮助": "ℹ️",
}


def _format_help_overview(display_prefix: str = "/") -> str:
    """全部指令分组概览。"""
    lines = [f"{display_prefix}帮助 - 跑团助手指令大全"]
    lines.append("回复「/帮助 <组名>」查看详细语法，例如：/帮助 知识库")
    for topic in ("知识库", "先攻", "背包", "商店", "角色卡", "骰子", "历史", "帮助"):
        section = _HELP_SECTIONS[topic]
        lines.append(f"{_HELP_EMOJI[topic]} {topic}：{section['cmds']}")
    lines.append("ℹ️ 可用组：知识库 / 先攻 / 背包 / 商店 / 角色卡 / 骰子 / 历史")
    return "\n".join(lines)


def _format_help_topic(topic: str, display_prefix: str = "/") -> str:
    """某组指令详细语法。"""
    lines = [f"{_HELP_EMOJI[topic]} {topic} 指令详解："]
    lines.extend(display_prefix + line.strip().lstrip("/") if line.startswith("/") else line
                 for line in _HELP_SECTIONS[topic]["lines"])
    lines.append(f"回复「{display_prefix}帮助」查看全部指令。")
    return "\n".join(lines)


# /bag 用法帮助模板，{p} 为显示前缀（'/' 或自定义前缀）。
_BAG_HELP = (
    "背包指令用法：\n"
    "  别名：{p}背包、{p}inventory 等同 {p}bag，任选其一。\n"
    "  {p}bag                        查看自己的背包\n"
    "  {p}bag list @某人             查看他人背包（只读）\n"
    "  {p}bag add <名称> <数量> [w=单件重量] [v=单件价值] [note=备注]\n"
    "  {p}bag rm <名称> [数量]       取出物品（数量缺省 1，归零自动删除）\n"
    "  {p}bag edit <名称> [w=|-] [v=|-] [note=|-]  修改物品属性（- 表示清除）\n"
    "  {p}bag give @某人 <名称> [数量]  赠送物品\n"
    "  {p}bag party                  查看队伍背包\n"
    "  {p}bag party edit <名称> [w=|-] [v=|-] [note=|-]  修改队伍背包物品\n"
    "  {p}bag put <名称> [数量]      存入队伍背包\n"
    "  {p}bag take <名称> [数量]     从队伍背包取出\n"
    "  {p}bag clear                  清空自己的背包\n"
    "  {p}bag party clear            清空队伍背包（管理员）\n"
    "  {p}发放 <名称> [数量] [重=X] [价=X] [备注=X] [<名称> …]  发放战利品到队伍背包（可批量）\n"
    "  {p}收回 <名称> [数量] [<名称> [数量] …]   从队伍背包收回物品（管理员；可批量）\n"
    "物品名含空格时请用英文双引号包裹。"
)


# /商店 用法帮助模板，{p} 为显示前缀。
_SHOP_HELP = (
    "商店指令用法：\n"
    "  {p}商店                       查看商品列表（第 1 页）\n"
    "  {p}商店 <页码>                 翻页，如 {p}商店 2（或 {p}商店 页 2）\n"
    "  {p}商店 买 <名称> [数量] [<名称> [数量] …]   购买（自动扣背包货币并找零；可批量）\n"
    "  {p}商店 卖 <名称> [数量] [<名称> [数量] …]   卖回商店（只收在架商品；可批量）\n"
    "  {p}商店 初始化                用 PHB/XPHB 非魔法物品重建商店（管理员）\n"
    "  {p}商店 上架 <名称> [价=金额] [库存=数量] [<名称> …]  可批量上架（管理员）\n"
    "  {p}商店 下架 <名称> [<名称> …]    可批量下架（管理员）\n"
    "  {p}商店 设价 <名称> <金额|自动>   （自动 = 恢复库价）\n"
    "  {p}商店 设库存 <名称> <数量|无限>\n"
    "  {p}商店 回购率 <系数>          如 0.5 = 半价回收\n"
    "  {p}商店 清空                   清空整店全部商品（管理员；回购系数保留）\n"
    "批量示例：{p}商店 买 长剑 匕首 2 = 长剑×1 + 匕首×2；"
    "数量省略 = 1。\n"
    "金额写法：2金5银、150（数字=铜币）；1金币=10银币=100铜币。"
)


# ---------------------------------------------------------------------------
# 插件主类
# ---------------------------------------------------------------------------


class TrpgAssistantPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        cfg = config or {}
        # _safe_int / _safe_bool 防止非数字字符串或越界值导致插件加载失败。
        # max_val 与 _conf_schema.json 中的滑块上限保持一致，
        # 防止手改配置文件绕过 WebUI 的范围提示。
        self.max_dice_count: int = _safe_int(
            cfg.get("max_dice_count"), 100, min_val=1, max_val=1000
        )
        self.max_dice_sides: int = _safe_int(
            cfg.get("max_dice_sides"), 1000, min_val=1, max_val=10000
        )
        self.exploding_max_depth: int = _safe_int(
            cfg.get("exploding_max_depth"), 20, min_val=1, max_val=100
        )
        self.max_input_len: int = _safe_int(
            cfg.get("max_input_len"), 200, min_val=10, max_val=1000
        )
        self.max_repeat_count: int = _safe_int(
            cfg.get("max_repeat_count"), 20, min_val=1, max_val=100
        )
        self.show_detail: bool = _safe_bool(cfg.get("show_detail"), True)

        # 骰面大小配置
        self.default_dice_sides: int = _safe_int(
            cfg.get("default_dice_sides"), 20, min_val=2, max_val=10000
        )
        self.allow_session_dice_sides: bool = _safe_bool(
            cfg.get("allow_session_dice_sides"), True
        )
        self.enable_whitelist: bool = _safe_bool(cfg.get("enable_whitelist"), False)
        raw_whitelist = cfg.get("whitelist_users") or []
        self.whitelist_users: list[str] = (
            [str(u) for u in raw_whitelist] if isinstance(raw_whitelist, list) else []
        )
        self.allow_private_bypass_whitelist: bool = _safe_bool(
            cfg.get("allow_private_bypass_whitelist"), True
        )

        # 自定义触发前缀配置
        self.default_cmd_prefix: str = str(cfg.get("default_cmd_prefix") or "")
        self.allow_custom_prefix: bool = _safe_bool(
            cfg.get("allow_custom_prefix"), True
        )

        # 投掷历史配置
        self.enable_history: bool = _safe_bool(cfg.get("enable_history"), True)
        self.allow_view_history: bool = _safe_bool(cfg.get("allow_view_history"), True)
        self.max_history_count: int = _safe_int(
            cfg.get("max_history_count"), 50, min_val=1, max_val=500
        )

        # 私设写入开关（v0.37.0）：默认关，write 动作拒绝并提示去 WebUI 开启
        self.homebrew_write_enabled: bool = _safe_bool(
            cfg.get("homebrew_write_enabled"), False
        )
        # 私设写盘串行锁（检查冲突→写盘→reload 临界区）
        self._homebrew_write_lock = asyncio.Lock()

        # 投掷历史管理器
        self._history = RollHistoryManager(
            star=self,
            max_count=self.max_history_count,
            enabled=self.enable_history,
        )

        # 先攻（Initiative）管理器
        self._initiative = InitiativeManager(star=self)

        # 背包（Inventory）管理器
        self._inventory = InventoryManager(star=self)

        # 角色卡管理器延迟初始化，避免核心接口尚未实现时被意外调用
        self._character_manager: CharacterManager | None = None

        # 开卡引导管理器延迟初始化（依赖 character_manager 与知识库）
        self._chargen_manager: ChargenManager | None = None

        # 商店管理器延迟初始化（依赖知识库与背包管理器）
        self._shop_manager: ShopManager | None = None

        # DND 知识库管理器（只读 SQLite，懒加载，见 kb.py）
        self._kb_manager: KnowledgeBaseManager | None = None

        # 内存写透式 LRU 缓存，按会话来源存储自定义前缀。
        # 避免在 custom_prefix_route 中对每条消息都进行 KV 存储查询。
        # 键为 unified_msg_origin 字符串；None 表示“未设置自定义前缀”。
        # OrderedDict 保留插入顺序，命中时调用 move_to_end() 可实现
        # O(1) 的 LRU 驱逐，容量溢出时调用 popitem(last=False)。
        self._prefix_cache: OrderedDict[str, str | None] = (
            OrderedDict()
        )  # 每个缓存条目的写入时间戳（monotonic），用于 TTL 过期检测。
        self._prefix_cache_ts: dict[str, float] = {}

    @property
    def character_manager(self) -> CharacterManager:
        """懒加载角色卡管理器。"""
        if self._character_manager is None:
            self._character_manager = CharacterManager(star=self)
        return self._character_manager

    @property
    def chargen_manager(self) -> ChargenManager:
        """懒加载开卡引导管理器（群规则 + 引导状态机）。

        依赖注入：角色卡管理器、知识库管理器（名称校验）、掷骰回调
        （经插件限制并写入投掷历史）、背包管理器（起始金币自动发放）。
        """
        if self._chargen_manager is None:
            self._chargen_manager = ChargenManager(
                star=self,
                character_manager=self.character_manager,
                kb_manager=self.kb_manager,
                roll_fn=self._roll_chargen,
                inventory_manager=self._inventory,
            )
        return self._chargen_manager

    def _recalc_card(self, card) -> object | None:
        """规则引擎重算角色卡战斗字段 base 层；失败返回 None（不阻断主流程）。

        chargen_engine 为纯函数模块（engine → character/kb 单向依赖），
        无需实例，命令层直接用 kb_manager 调用。
        """
        try:
            from .chargen_engine import recalc_base

            return recalc_base(card, self.kb_manager)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 规则引擎重算失败: {e}")
            return None

    @staticmethod
    def _is_generated_attack(card: CharacterSheet, name: str) -> bool:
        """攻击条目是否为规则引擎生成集（装备槽武器 / 「{职业}法术攻击」，v0.31.0）。

        生成条目删除后重算会恢复，删除时据此提示（要彻底移除需清空装备槽）。
        """
        slots = (card.equipment.main_hand, card.equipment.off_hand)
        if name in {s.strip() for s in slots if s and s.strip()}:
            return True
        return any(name == f"{c.class_name}法术攻击" for c in card.classes)

    @property
    def shop_manager(self) -> ShopManager:
        """懒加载商店管理器（依赖知识库与背包管理器）。"""
        if self._shop_manager is None:
            self._shop_manager = ShopManager(
                star=self,
                kb_manager=self.kb_manager,
                inventory_manager=self._inventory,
            )
        return self._shop_manager

    @property
    def kb_manager(self) -> KnowledgeBaseManager:
        """懒加载 DND 知识库管理器。

        优先使用 data_dir 下的 kb_update.db（未来在线更新的产物，本期未实现），
        否则使用随插件打包的内置库 kb_data/dnd_kb.db（最终兜底）。
        """
        if self._kb_manager is None:
            builtin = Path(__file__).resolve().parent / "kb_data" / "dnd_kb.db"
            update: Path | None = None
            homebrew_dir: Path | None = None
            try:
                from astrbot.core.star.star_tools import StarTools

                data_dir = Path(StarTools.get_data_dir())
                update = data_dir / "kb_update.db"
                homebrew_dir = data_dir / "trpg_homebrew"
            except Exception:  # noqa: BLE001 — 测试替身环境无 StarTools，回退内置库
                update = None
            self._kb_manager = KnowledgeBaseManager(
                resolve_db_path(builtin, update),
                homebrew_dir=homebrew_dir,
            )
        return self._kb_manager

    async def initialize(self) -> None:
        logger.info(
            "[trpg_assistant] 跑团助手插件已加载。"
            f"限制: 最多骰子数={self.max_dice_count}, 最大面数={self.max_dice_sides}, "
            f"爆炸深度={self.exploding_max_depth}, 显示明细={self.show_detail}, "
            f"默认骰面={self.default_dice_sides}, 允许会话设置={self.allow_session_dice_sides}, "
            f"启用历史={self.enable_history}, 允许查看历史={self.allow_view_history}, "
            f"最大历史记录数={self.max_history_count}"
        )

    # ------------------------------------------------------------------
    # 骰面大小辅助方法
    # ------------------------------------------------------------------

    async def _get_effective_sides(self, event: AstrMessageEvent) -> int:
        """
        获取当前会话的有效默认骰面数。

        优先使用会话级设置（通过 /dset 命令设置），不存在则回退到全局默认值。
        """
        key = f"session_sides:{event.unified_msg_origin}"
        sides = await self.get_kv_data(key, self.default_dice_sides)
        # max_val 防止外部直接改写 KV 写入超过当前 max_dice_sides 上限的值。
        return _safe_int(
            sides, self.default_dice_sides, min_val=2, max_val=self.max_dice_sides
        )

    async def _get_effective_prefix(self, event: AstrMessageEvent) -> str:
        """
        获取当前会话的有效自定义触发前缀。

        优先使用会话级设置（通过 /rprefix 命令设置），不存在则回退到全局配置值。
        结果缓存在 _prefix_cache 中，避免对每条消息都访问 KV 存储。
        缓存条目在 _PREFIX_CACHE_TTL 秒后过期失效，届时重新读取 KV，
        以便感知外部直接修改（其他实例、后台脚本等）带来的前缀变更。
        """
        origin = event.unified_msg_origin
        now = time.monotonic()
        ttl_expired = now - self._prefix_cache_ts.get(origin, 0.0) > _PREFIX_CACHE_TTL
        if origin not in self._prefix_cache or ttl_expired:
            kv_key = f"custom_prefix:{origin}"
            raw = await self.get_kv_data(kv_key, None)
            self._set_prefix_cache(origin, str(raw) if raw is not None else None)
        else:
            # 缓存命中且未过期：将条目提升至最近使用位置。
            self._prefix_cache.move_to_end(origin)
        cached = self._prefix_cache[origin]
        return cached if cached is not None else self.default_cmd_prefix

    def _set_prefix_cache(self, origin: str, value: str | None) -> None:
        """写入前缀缓存，内部自动执行容量检查与 LRU 驱逐，所有写入路径均通过此方法。"""
        if (
            origin not in self._prefix_cache
            and len(self._prefix_cache) >= _PREFIX_CACHE_MAX
        ):
            # 缓存已满 且 key 不在缓存中：驱逐最久未使用的条目及其时间戳。
            evicted, _ = self._prefix_cache.popitem(last=False)
            self._prefix_cache_ts.pop(evicted, None)
        self._prefix_cache[origin] = value
        self._prefix_cache.move_to_end(origin)  # 提升至 MRU 位置
        self._prefix_cache_ts[origin] = time.monotonic()  # 刷新写入时间戳

    async def _whitelist_check(self, event: AstrMessageEvent) -> bool:
        """
        管理命令白名单验证（不含功能开关检查）。

        注意：此白名单仅限制 /dset 和 /rprefix 等管理命令的使用权限，
        不影响 /r 掷骰指令（掷骰对所有用户始终开放）。

        判断顺序：
        1. enable_whitelist 为 False → 始终允许
        2. 私聊且 allow_private_bypass_whitelist → 允许
        3. whitelist_users 非空 → 检查 sender_id 是否在列表中
        4. whitelist_users 为空 → 使用 AstrBot 管理员判断
        """
        if not self.enable_whitelist:
            return True
        if self.allow_private_bypass_whitelist and event.is_private_chat():
            return True
        sender_id = str(event.get_sender_id())
        if self.whitelist_users:
            return sender_id in self.whitelist_users
        # 白名单为空，回退到 AstrBot 全局管理员
        return event.is_admin()

    async def _check_permission(self, event: AstrMessageEvent) -> bool:
        """
        检查当前用户是否有权使用 /dset 命令。

        判断顺序：
        1. allow_session_dice_sides 为 False → 始终拒绝
        2. 通用白名单验证
        """
        if not self.allow_session_dice_sides:
            return False
        return await self._whitelist_check(event)

    async def _check_prefix_permission(self, event: AstrMessageEvent) -> bool:
        """
        检查当前用户是否有权使用 /rprefix 命令。

        判断顺序：
        1. allow_custom_prefix 为 False → 始终拒绝
        2. 通用白名单验证
        """
        if not self.allow_custom_prefix:
            return False
        return await self._whitelist_check(event)

    async def _check_destructive_permission(self, event: AstrMessageEvent) -> bool:
        """
        检查当前用户是否有权执行群聊中的破坏性管理操作（清空历史 / 先攻列表、
        移除先攻单位等）。

        私聊中任何人均可执行（仅影响自身数据）。
        群聊中始终需要明确权限：
          - enable_whitelist 开启时：使用通用白名单验证（已在白名单或管理员）。
          - enable_whitelist 关闭时：回退到 AstrBot 管理员判断，
            避免白名单功能被整体关闭时任意群成员就可执行破坏性操作。
        """
        if event.is_private_chat():
            return True
        # 群聊始终需要权限：白名单已开启则用通用验证，
        # 关闭时回退到管理员判断而非全放行。
        if self.enable_whitelist:
            return await self._whitelist_check(event)
        return event.is_admin()

    async def _check_history_clear_permission(self, event: AstrMessageEvent) -> bool:
        """
        检查当前用户是否有权清除群聊投掷历史。

        私聊中任何人均可清除（仅影响自身数据）。
        群聊中始终需要明确权限（见 _check_destructive_permission）。
        """
        return await self._check_destructive_permission(event)

    async def _check_homebrew_write_permission(self, event: AstrMessageEvent) -> bool:
        """
        检查当前用户是否有权写入私设文件（manage_homebrew write）。

        私设是插件级全局数据（data_dir/trpg_homebrew），任何会话写入都影响
        所有群，因此**私聊不放行**（区别于 _check_destructive_permission 的
        会话级数据语义，也不走 _whitelist_check 的私聊旁路）：
          - enable_whitelist 开启且白名单非空：sender ∈ whitelist_users；
          - 其余情况（含白名单为空）：回退 AstrBot 管理员判断。
        """
        if self.enable_whitelist and self.whitelist_users:
            return str(event.get_sender_id()) in self.whitelist_users
        return event.is_admin()

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _do_roll(self, expression_str: str, default_sides: int = 20) -> str:
        """
        解析、执行并格式化一条骰池表达式。

        返回纯文本结果字符串，所有异常均被捕获并转换为可读错误信息。

        Args:
            expression_str: 骰池表达式字符串。
            default_sides: 空表达式时使用的默认骰面数。
        """
        # 中文优势/劣势 → adv/dis 语法糖（必须在 parse 之前，_strip_label 会截断 CJK）。
        expression_str = _map_zh_adv_dis(expression_str)
        if _ZH_ORPHAN_ADV_DIS.search(expression_str):
            return (
                "解析错误: 优势/劣势必须紧贴骰子（如 d20优势 / d20劣势），"
                "带空格、前置或单独使用均不支持\n" + _SYNTAX_HELP
            )

        try:
            expr = parse(
                expression_str,
                default_sides=default_sides,
                max_input_len=self.max_input_len,
            )
        except DiceParseError as e:
            return f"解析错误: {e}\n{_SYNTAX_HELP}"

        try:
            result = roll(
                expr,
                max_dice=self.max_dice_count,
                max_sides=self.max_dice_sides,
                exploding_depth=self.exploding_max_depth,
                max_repeat=self.max_repeat_count,
            )
        except DiceRollError as e:
            return f"掷骰错误: {e}"

        try:
            return format_result(result, show_detail=self.show_detail)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            logger.exception(f"[trpg_assistant] 格式化结果时发生意外错误: {e}")
            return "掷骰完成，但格式化时发生内部错误"

    def _roll_d20(self) -> int | None:
        """
        掷一枚 d20 用于先攻判定。

        返回骰面值（1~20）；解析/掷骰异常时返回 None（调用方负责提示）。
        先攻使用标准 d20，不受会话默认骰面影响。
        """
        try:
            expr = parse("d20", default_sides=20, max_input_len=self.max_input_len)
            result = roll(
                expr,
                max_dice=self.max_dice_count,
                max_sides=self.max_dice_sides,
                exploding_depth=self.exploding_max_depth,
                max_repeat=self.max_repeat_count,
            )
        except (DiceParseError, DiceRollError) as e:
            logger.warning(f"[trpg_assistant] 先攻掷骰失败: {e}")
            return None
        return result.total

    def _roll_chargen(self, expr_str: str) -> tuple[int | None, str]:
        """开卡代骰回调：解析并掷一次骰，返回 (合计, 明细文本)。

        经插件骰子限制（max_dice/max_sides/爆炸深度）。不在此写投掷历史
        （模块边界），由命令/工具层在收到代骰回复后统一记录。
        失败返回 (None, 错误文案)。
        """
        try:
            expr = parse(expr_str, default_sides=20, max_input_len=self.max_input_len)
        except DiceParseError as e:
            return None, f"解析错误: {e}"
        try:
            result = roll(
                expr,
                max_dice=self.max_dice_count,
                max_sides=self.max_dice_sides,
                exploding_depth=self.exploding_max_depth,
                max_repeat=self.max_repeat_count,
            )
        except DiceRollError as e:
            return None, f"掷骰错误: {e}"
        # 明细：保留骰列表 → 合计（4d6kh3=[4,6,2,1]→13）
        kept: list[int] = []
        for grp in result.group_results:
            kept.extend(grp.kept_rolls)
        detail = f"{expr_str}=[{','.join(str(v) for v in kept)}]→{result.total}"
        return result.total, detail

    async def _try_character_roll(
        self, event: AstrMessageEvent, expression_str: str
    ) -> tuple[str, str] | None:
        """识别「/r 力量」「/r 察觉」「/r 敏捷豁免」「/r 攻击」「/r 命名掷骰」并查活跃卡拼表达式。

        - /r 攻击 [武器名|列表]：用角色卡 attack_bonuses 的攻击加值掷攻击检定；
          无参数默认主手武器，「列表」枚举全部攻击选项，尾数纯数字为 DC。
        - /r <命名掷骰名>（v0.32.0）：用 /卡 骰 登记的自定义表达式直接掷，
          优先于内建别名（玩家显式登记意图覆盖默认行为）。
        返回 (掷骰输出, 用于历史记录的表达式)；提示类输出 hist_expr 为空串
        （不写投掷历史）；无命中或无活跃卡返回 None（调用方走原掷骰逻辑，
        报错文案保持原样）。
        """
        card = await self.character_manager.get_card(event)
        hit = resolve_roll_alias(
            expression_str, card.named_rolls if card is not None else None
        )
        if hit is None or card is None:
            return None
        kind, key, rest = hit
        if kind == "ability":
            mod, tags = card.ability_check(key)
            label_cn = f"{ABILITY_CN[key]}检定"
            tag_str = " " + " ".join(tags) if tags else ""
            detail = f"（{card.name}·{ABILITY_CN[key]}{card.ability_scores.get(key)}{tag_str}）"
        elif kind == "save":
            mod = card.get_save_modifier(key)
            label_cn = f"{ABILITY_CN[key]}豁免"
            prof = " 熟练" if key in card.save_proficiencies else ""
            detail = f"（{card.name}{prof}）"
        elif kind == "skill":
            mod, tags = card.skill_check(key)
            label_cn = SKILL_CN_REV.get(key, key)
            tag_str = " " + " ".join(tags) if tags else ""
            detail = f"（{card.name}{tag_str}）"
        elif kind == "named":
            # v0.32.0：命名掷骰——用 /卡 骰 登记的自定义表达式直接掷
            named_expr = card.named_rolls.get(key)
            if not named_expr:
                return None
            label_cn = "命名掷骰"  # 中文标签，防止 _strip_label 误并 ASCII
            detail = f"（{card.name}·「{key}」）"
            expr = f"{named_expr} {label_cn}{detail}"
            rest = rest.strip()
            if rest:
                expr += f" {rest}"  # DC 尾数由解析器提取
            output = self._do_roll(expr, default_sides=20)
            return output, expr
        else:  # attack：/r 攻击、/r 攻击 长剑、/r 攻击 列表、/r 攻击 15
            rest = rest.strip()
            # 尾数纯数字视为 DC（/r 攻击 15、/r 攻击 长剑 15）
            dc, name_query = "", rest
            if rest and rest.isdigit():
                dc, name_query = rest, ""
            elif rest:
                parts = rest.rsplit(None, 1)
                if len(parts) == 2 and parts[1].isdigit():
                    dc, name_query = parts[1], parts[0]
            if name_query in ("列表", "list", "全部"):
                return self._attack_list_text(card), ""
            target = (
                card.resolve_attack(name_query)
                if name_query
                else card.main_hand_attack()
            )
            if target is None:
                return self._attack_miss_text(card, name_query), ""
            wname, mod = target
            label_cn = "攻击"  # 中文标签，防止 _strip_label 误并 ASCII
            detail = f"（{card.name}·{wname}）"
            expr = f"1d20+{mod} {label_cn}{detail}"
            if dc:
                expr += f" {dc}"  # DC 尾数由解析器提取
            output = self._do_roll(expr, default_sides=20)
            return output, expr
        expr = f"1d20+{mod} {label_cn}{detail}"
        rest = rest.strip()
        if rest:
            expr += f" {rest}"  # DC 尾数由解析器提取
        output = self._do_roll(expr, default_sides=20)
        return output, expr

    @staticmethod
    def _attack_list_text(card: "CharacterSheet") -> str:
        """攻击选项枚举文案；attack_bonuses 为空时给出引导。"""
        attacks = card.list_attacks()
        if not attacks:
            return "卡上无攻击条目；用「/卡 设 攻击 <名称>=<加值>」添加，或先装备主手武器。"
        listing = "　".join(f"{n}({v:+d})" for n, v in attacks)
        return f"攻击选项：{listing}\n用法：/r 攻击 <武器名>，无参数时默认主手武器"

    @staticmethod
    def _attack_miss_text(card: "CharacterSheet", name_query: str) -> str:
        """攻击目标未命中（未知名/主手为空）时的提示文案。"""
        attacks = card.list_attacks()
        if not attacks:
            return "卡上无攻击条目；用「/卡 设 攻击 <名称>=<加值>」添加，或先装备主手武器。"
        if name_query:
            cands = [
                n for n, _ in attacks if name_query in n or n.startswith(name_query)
            ][:5]
            hint = f"相近：{'、'.join(cands)}" if cands else ""
            return f"未找到攻击「{name_query}」{hint}\n可用 /r 攻击 列表 查看全部攻击选项"
        listing = "　".join(f"{n}({v:+d})" for n, v in attacks)
        return f"未装备主手武器。攻击选项：{listing}\n用法：/r 攻击 <武器名>"

    def _validate_dice_expr(self, expr_str: str) -> bool:
        """校验骰式语法（供 /车卡规则 自定义掷骰预校验）。"""
        try:
            parse(expr_str, default_sides=20, max_input_len=self.max_input_len)
            return True
        except DiceParseError:
            return False

    # ------------------------------------------------------------------
    # /dset 命令核心逻辑（供标准命令和自定义前缀路由共用）
    # ------------------------------------------------------------------

    async def _handle_dset(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """
        /dset 命令核心逻辑，由 dset_cmd 和 custom_prefix_route 统一调用。

        Args:
            event: 消息事件。
            arg: 去除命令名后的参数字符串（空字符串表示仅查询）。
            display_prefix: 回复提示中显示的前缀符号（如 '/' 或自定义符号）。
        """
        key = f"session_sides:{event.unified_msg_origin}"

        # 权限检查（查询与写入均需授权，防止未授权用户探测会话配置）
        if not await self._check_permission(event):
            if not self.allow_session_dice_sides:
                yield event.plain_result("管理员已禁用会话骰面设置功能。")
            else:
                yield event.plain_result(
                    "你没有权限使用此命令。"
                    + (
                        "（白名单模式已启用，请联系管理员）"
                        if self.enable_whitelist
                        else ""
                    )
                )
            return

        # 查询当前设置
        if not arg:
            current = await self._get_effective_sides(event)
            is_session_set = await self.get_kv_data(key, None) is not None
            source = "会话设置" if is_session_set else "默认"
            yield event.plain_result(
                f"当前默认骰面数: d{current}（{source}）\n"
                f"用法: {display_prefix}dset <面数>\n"
                f"示例: {display_prefix}dset 6\n"
                f"重置: {display_prefix}dset reset\n"
            )
            return

        # 重置会话设置
        if arg.lower() in ("reset", "重置", "0"):
            await self.delete_kv_data(key)
            yield event.plain_result(
                f"已清除骰面设置，恢复为默认 d{self.default_dice_sides}。"
            )
            return

        # 解析并验证面数
        try:
            new_sides = int(arg)
        except ValueError:
            yield event.plain_result(
                f"无效的面数: '{arg}'，请输入 2~{self.max_dice_sides} 之间的整数，或 reset 重置。"
            )
            return

        if new_sides < 2:
            yield event.plain_result("骰面数不能小于 2。")
            return
        if new_sides > self.max_dice_sides:
            yield event.plain_result(f"骰面数不能超过限制 {self.max_dice_sides}。")
            return

        await self.put_kv_data(key, new_sides)
        yield event.plain_result(
            f"已将当前默认骰面数设为 d{new_sides}。后续 {display_prefix}r 将默认投掷 d{new_sides}。"
        )

    # ------------------------------------------------------------------
    # /r 指令处理器
    # ------------------------------------------------------------------

    @filter.command("r", alias={"roll"})
    async def roll_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        使用 DnD 骰池语法掷骰。

        用法: /r [骰池表达式] [标签] [DC]
        示例: /r 1d20+5, /r 4d6kh3, /r d20adv, /r d6!, /r 2d6+1d4+3 伤害
              /r d20 感知 15, /r d20感知15, /r d20+3 奥秘 12
              /r 攻击, /r 攻击 长剑, /r 攻击 列表（角色卡攻击检定）
        """
        raw_msg: str = event.message_str.strip()

        # 去掉开头的指令名（/r 或 /roll），提取骰池表达式部分。
        parts = raw_msg.split(None, 1)  # 按第一个空白字符分割
        expression_str = parts[1].strip() if len(parts) > 1 else ""

        effective_sides = await self._get_effective_sides(event)

        # 无参数时默认掷一个 dN（N 为会话/全局默认骰面数）
        if not expression_str:
            expression_str = f"d{effective_sides}"

        # 角色卡联动：/r 力量、/r 察觉、/r 敏捷豁免、/r 攻击 → 查活跃卡拼 1d20+修正
        hit = await self._try_character_roll(event, expression_str)
        if hit is not None:
            output, hist_expr = hit
            if hist_expr and not output.startswith(ROLL_ERROR_PREFIXES):
                await self._history.add(event, hist_expr, output)
            yield event.plain_result(output)
            return

        output = self._do_roll(expression_str, default_sides=effective_sides)
        if not output.startswith(ROLL_ERROR_PREFIXES):
            await self._history.add(event, expression_str, output)
        yield event.plain_result(output)

    @filter.command("dnd")
    async def dnd_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        按 DND 5e 规则随机生成角色属性（掷骰开卡）。

        每组掷 6 次 4d6kh3（4d6 取最大 3 之和），得到 6 个属性值；
        组数 N 默认 1，上限 20。结果可原样分配填入六维属性。

        用法: /dnd [组数]
        示例: /dnd, /dnd 5
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)  # 按第一个空白字符分割
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_dnd(event, arg, display_prefix="/"):
            yield msg

    async def _handle_dnd(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """按 DND 5e 规则随机生成角色属性（掷骰开卡）。

        /dnd 命令核心逻辑，由 dnd_cmd 和 custom_prefix_route 统一调用。
        每组掷 6 次 4d6kh3（4d6 取最大 3 之和），得到 6 个属性值；
        组数 N 默认 1，上限 20。结果可原样分配填入六维属性。

        用法: {display_prefix}dnd [组数]
        示例: {display_prefix}dnd, {display_prefix}dnd 5
        """
        n = 1
        if arg:
            try:
                n = int(arg)  # int() 顺带兼容全角数字
            except ValueError:
                yield event.plain_result(
                    f"用法：{display_prefix}dnd [组数]（正整数，1~{_DND_MAX_GROUPS}，默认 1 组）"
                )
                return
        if n < 1:
            yield event.plain_result(
                f"组数至少为 1。用法：{display_prefix}dnd [组数]"
            )
            return
        if n > _DND_MAX_GROUPS:
            yield event.plain_result(
                f"组数不能超过 {_DND_MAX_GROUPS}（防止刷屏）。用法：{display_prefix}dnd [组数]"
            )
            return

        lines: list[str] = []
        for k in range(1, n + 1):
            scores: list[int] = []
            for j in range(1, _DND_SCORE_COUNT + 1):
                total, detail = self._roll_chargen(_DND_ROLL_EXPR)
                if total is None:
                    yield event.plain_result(
                        f"第 {k} 组第 {j} 次掷骰失败：{detail}，已中止。"
                    )
                    return
                scores.append(total)
            lines.append(f"第{k}组: " + " ".join(str(v) for v in scores))
        output = "\n".join(lines)
        if n > 1:
            output += f"\n（{_DND_ROLL_EXPR} 标准属性掷法，每组 6 项按序对应六维）"
        await self._history.add(event, f"dnd {n}", output)
        yield event.plain_result(output)

    # ------------------------------------------------------------------
    # /dset 与 /rprefix 指令
    # ------------------------------------------------------------------

    @filter.command("dset", alias={"dice_set"})
    async def dset_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        设置当前会话的默认骰面数。

        用法:
          /dset <面数>     将当前会话默认骰面数设为指定值
          /dset reset     清除默认骰子面数设置，恢复为全局默认
          /dset           查看当前会话的默认骰面数
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_dset(event, arg, display_prefix="/"):
            yield msg

    @filter.command("rprefix")
    async def rprefix_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        设置或查询当前会话的自定义骰子指令触发前缀。

        用法:
          /rprefix              查看当前会话的有效前缀
          /rprefix <前缀>       将当前会话触发前缀设为指定符号（如 . 或 !）
          /rprefix reset        清除会话前缀，恢复全局默认
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        key = f"custom_prefix:{event.unified_msg_origin}"

        # 查询当前前缀
        if not arg:
            current = await self._get_effective_prefix(event)
            if current:
                yield event.plain_result(
                    f"当前触发前缀：{current!r}\n"
                    f"用法：/rprefix <前缀> 设置前缀（如 /rprefix . 或 /rprefix !）\n"
                    f"重置：/rprefix reset"
                )
            else:
                yield event.plain_result(
                    "当前未设置自定义前缀，使用系统默认前缀\n"
                    "用法：/rprefix <前缀> 设置前缀（如 /rprefix . 或 /rprefix !）"
                )
            return

        # 权限检查
        if not await self._check_prefix_permission(event):
            if not self.allow_custom_prefix:
                yield event.plain_result("管理员已禁用自定义触发前缀功能。")
            else:
                yield event.plain_result(
                    "你没有权限使用此命令。"
                    + (
                        "（白名单模式已启用，请联系管理员）"
                        if self.enable_whitelist
                        else ""
                    )
                )
            return

        # 重置会话前缀
        if arg.lower() in ("reset", "重置", "清除"):
            await self.delete_kv_data(key)
            self._set_prefix_cache(event.unified_msg_origin, None)
            if self.default_cmd_prefix:
                yield event.plain_result(
                    f"已清除自定义骰子前缀设置，恢复为默认前缀 {self.default_cmd_prefix!r}。"
                )
            else:
                yield event.plain_result("已清除自定义骰子前缀设置，使用默认前缀。")
            return

        # 前缀长度校验
        if len(arg) > 5:
            yield event.plain_result("前缀过长，建议使用 1~2 个字符（如 . 或 !!）。")
            return

        # 拒绝与系统命令前缀 '/' 相同的前缀——路由层明确忽略它，
        # 设置后自定义路由不会生效，形成"可设置但不可用"的逻辑陷阱。
        if arg == "/":
            yield event.plain_result(
                "前缀 '/' 与系统命令前缀冲突，设置后自定义路由不会生效，"
                "请选择其他符号（如 . ! ~ !!）。"
            )
            return

        # 前缀字符集校验：不允许空白字符或字母，避免路由歧义和误触发。
        if any(c.isspace() or c.isalpha() for c in arg):
            yield event.plain_result(
                "前缀不能包含空白字符或字母，请使用标点/符号（如 . ! ~ !! 等）。"
            )
            return

        await self.put_kv_data(key, arg)
        self._set_prefix_cache(event.unified_msg_origin, arg)
        yield event.plain_result(
            f"已将自定义骰子前缀设为 {arg!r}。\n"
            f"现在可用 {arg}r、{arg}roll、{arg}dset 等触发骰子功能，\n"
            f"也可继续使用 /r 等前缀。"
        )

    # ------------------------------------------------------------------
    # /rh 指令：投掷历史记录
    # ------------------------------------------------------------------

    async def _handle_rhistory(
        self, event: AstrMessageEvent, arg: str
    ) -> AsyncGenerator:
        """
        /rh 命令核心逻辑，由 rhistory_cmd 和 custom_prefix_route 统一调用。

        Args:
            event: 消息事件。
            arg: 去除命令名后的参数字符串（空字符串表示查询全部/默认模式）。
        """
        if not self.enable_history:
            yield event.plain_result("投掷历史记录功能未启用。")
            return

        arg_lower = arg.lower()

        # --- 清除历史 ---
        if arg_lower in ("clear", "清除", "清空"):
            # 私聊任何人均可清除，群聊始终需要权限（白名单关闭时回退管理员判断）
            if not await self._check_history_clear_permission(event):
                yield event.plain_result("你没有权限清除群聊中的投掷历史记录。")
                return
            count = await self._history.clear(event)
            yield event.plain_result(
                f"已清空投掷历史，共删除 {count} 条记录。"
                if count
                else "当前暂无投掷历史记录。"
            )
            return

        # --- 查看历史（需检查查看权限）---
        if not self.allow_view_history:
            yield event.plain_result("管理员已禁用投掷历史查看功能。")
            return

        is_group = not event.is_private_chat()

        if arg_lower in ("me",):
            # 仅显示自己的记录（群聊过滤，私聊无差别）
            sender_id = str(event.get_sender_id())
            sender_name = str(event.get_sender_name())
            entries = await self._history.get_by_sender(event, sender_id)
            # 群聊中明确显示是谁的记录，避免歧义。
            title = f"{sender_name} 的投掷记录" if is_group else "我的投掷记录"
            text = RollHistoryManager.format_entries(
                entries, show_sender=False, title=title
            )
            yield event.plain_result(text)
            return

        # 默认：all 或空白
        entries = await self._history.get_all(event)
        # 群聊显示发送者昵称(ID)，便于区分同名用户；私聊不显示
        text = RollHistoryManager.format_entries(entries, show_sender=is_group)
        yield event.plain_result(text)

    @filter.command("rh", alias={"rhistory"})
    async def rhistory_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        查看或清除当前会话的投掷历史记录。

        用法:
          /rh           查看会话全部历史（群聊含发送者，私聊不显示）
          /rh all       同无参数
          /rh me        仅显示自己的投掷记录（群聊内使用）
          /rh clear     清空当前会话历史（群聊需白名单权限）
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_rhistory(event, arg):
            yield msg

    # ------------------------------------------------------------------
    # /ri 与 /init 指令：先攻追踪
    # ------------------------------------------------------------------

    @filter.command("ri")
    async def ri_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        掷先攻并加入当前会话的先攻列表。

        用法:
          /ri              掷 d20 入列（本人）；已有活跃角色卡时自动附加卡上先攻
          /ri +3           掷 d20+3 入列（本人）
          /ri +2 食人魔    为具名单位掷 d20+2 入列（DM 代掷）
          /ri 15 哥布林甲  以固定值 15 录入（DM 抄书值）
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        sender_name = str(event.get_sender_name()).strip() or "未知单位"
        kind, number, name = _parse_ri_arg(arg, sender_name)

        if kind == "invalid":
            yield event.plain_result(
                "用法：/ri [+调整值] [名称] 或 /ri <固定值> [名称]\n"
                "示例：/ri +3、/ri +2 食人魔、/ri 15 哥布林甲"
            )
            return

        if kind == "roll":
            # v0.30.0：无显式调整值时联动活跃角色卡的先攻（=敏捷修正+房规加值）
            number_used = number
            note = ""
            if not arg:
                card = await self.character_manager.get_card(event)
                if card is not None:
                    number_used = card.initiative.total
                    note = f"（角色卡：{card.name} 先攻 {number_used:+d}）"
            die = self._roll_d20()
            if die is None:
                yield event.plain_result("先攻掷骰失败，请稍后再试。")
                return
            value = die + number_used
            _, entry = await self._initiative.add(
                event,
                name=name,
                value=value,
                modifier=number_used,
                user_id=str(event.get_sender_id()),
                is_fixed=False,
            )
            yield event.plain_result(
                f"{InitiativeManager.format_entry_confirmation(entry)}，已加入先攻列表。{note}"
            )
            return

        # kind == "fixed"
        _, entry = await self._initiative.add(
            event,
            name=name,
            value=number,
            user_id=str(event.get_sender_id()),
            is_fixed=True,
        )
        yield event.plain_result(
            f"{InitiativeManager.format_entry_confirmation(entry)}，已加入先攻列表。"
        )

    @filter.command("init", alias={"initiative"})
    async def init_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        查看或管理当前会话的先攻列表。

        用法:
          /init              查看先攻列表
          /init end          推进回合（播报下一位，绕回时轮数+1）
          /init del <名称>   移除单位（白名单权限）
          /init clr          清空本场战斗（白名单权限）
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_init(event, arg, display_prefix="/"):
            yield msg

    async def _handle_init(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """
        /init 命令核心逻辑，由 init_cmd 和 custom_prefix_route 统一调用。

        Args:
            event: 消息事件。
            arg: 去除命令名后的参数字符串（空字符串表示查看列表）。
            display_prefix: 回复提示中显示的前缀符号（如 '/' 或自定义符号）。
        """
        # 子命令只取第一个 token（如 "del 食人魔" → sub="del", rest="食人魔"），
        # 否则带名称的子命令永远匹配不到，落入用法提示。
        arg_parts = arg.split(None, 1)
        sub = arg_parts[0].lower() if arg_parts and arg_parts[0] else ""
        rest = arg_parts[1].strip() if len(arg_parts) > 1 else ""

        # --- 查看列表 ---
        if not arg:
            state = await self._initiative.get_state(event)
            yield event.plain_result(InitiativeManager.format_list(state))
            return

        # --- 推进回合 ---
        if sub in ("end", "next", "下一位", "下一回合", "推进"):
            result = await self._initiative.advance(event)
            yield event.plain_result(InitiativeManager.format_advance(result))
            return

        # --- 移除单位 ---
        if sub in ("del", "remove", "移除", "删除"):
            if not await self._check_destructive_permission(event):
                yield event.plain_result(
                    "你没有权限移除先攻单位。"
                    + (
                        "（白名单模式已启用，请联系管理员）"
                        if self.enable_whitelist
                        else ""
                    )
                )
                return
            if not rest:
                yield event.plain_result(f"用法：{display_prefix}init del <名称>")
                return
            result = await self._initiative.remove(event, rest)
            if result.removed is None:
                yield event.plain_result(f"先攻列表中未找到「{rest}」。")
                return
            lines = [f"☠️ 已移除 {result.removed.name}（先攻 {result.removed.value}）。"]
            if result.next_current is not None:
                lines.append(
                    f"现在轮到 {result.next_current.name}"
                    f"（先攻 {result.next_current.value}）行动。"
                )
            lines.append(f"剩余 {len(result.state.entries)} 个单位。")
            yield event.plain_result("\n".join(lines))
            return

        # --- 清空本场战斗 ---
        if sub in ("clr", "clear", "清空", "清除"):
            if not await self._check_destructive_permission(event):
                yield event.plain_result(
                    "你没有权限清空先攻列表。"
                    + (
                        "（白名单模式已启用，请联系管理员）"
                        if self.enable_whitelist
                        else ""
                    )
                )
                return
            count = await self._initiative.clear(event)
            yield event.plain_result(
                f"已清空先攻列表，共移除 {count} 个单位。"
                if count
                else "先攻列表本来就是空的。"
            )
            return

        yield event.plain_result(
            f"用法：{display_prefix}init 查看列表；{display_prefix}init end 推进回合；"
            f"{display_prefix}init del <名称> 移除单位；{display_prefix}init clr 清空本场战斗。"
        )

    @filter.command("bag", alias={"inventory", "背包"})
    async def bag_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        个人背包与队伍背包管理。

        用法:
          /bag                            查看自己的背包
          /bag list @某人                 查看他人背包（只读）
          /bag add <名称> <数量> [w=] [v=] [note=]  放入物品
          /bag rm <名称> [数量]           取出物品
          /bag edit <名称> [w=|-] [v=|-] [note=|-]  修改物品属性（- 清除）
          /bag give @某人 <名称> [数量]   赠送物品
          /bag party                      查看队伍背包
          /bag party edit <名称> [w=|-] [v=|-] [note=|-]  修改队伍背包物品
          /bag put / take <名称> [数量]   存入 / 取出队伍背包
          /bag clear                      清空自己的背包
          /bag party clear                清空队伍背包（白名单权限）
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_bag(event, arg, display_prefix="/"):
            yield msg

    async def _handle_bag(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """
        /bag 命令核心逻辑，由 bag_cmd 和 custom_prefix_route 统一调用。

        Args:
            event: 消息事件。
            arg: 去除命令名后的参数字符串（空字符串表示查看自己的背包）。
            display_prefix: 回复提示中显示的前缀符号（如 '/' 或自定义符号）。
        """
        tokens = _tokenize(arg)
        if tokens is None:
            yield event.plain_result(
                "引号未配对，物品名含空格时请用英文双引号包裹。"
            )
            return

        # 子命令只取第一个 token（同 _handle_init 的解析模式）。
        sub = tokens[0].lower() if tokens else ""
        rest = tokens[1:]
        is_private = event.is_private_chat()

        # --- 查看背包 ---
        if not tokens or sub in ("list", "ls", "查看"):
            target_id: str | None = None
            if tokens and rest:
                # .bag list @某人：只读查看他人背包
                if is_private:
                    yield event.plain_result("私聊中只能查看自己的背包。")
                    return
                target_id = _extract_at_target(event)
                if target_id is None:
                    yield event.plain_result(
                        "未能识别 @目标，请在群聊中 @ 对方后再试。"
                    )
                    return
            inv = await self._inventory.get_personal(event, target_id)
            if not inv.items:
                if target_id is not None:
                    yield event.plain_result(f"🎒 玩家 {target_id} 的背包是空的。")
                else:
                    yield event.plain_result(
                        f"🎒 背包是空的。用 {display_prefix}bag add <名称> <数量> 放入物品。"
                    )
                return
            owner = (
                str(event.get_sender_name()).strip() or "你"
            ) if target_id is None else f"玩家 {target_id}"
            yield event.plain_result(
                InventoryManager.format_inventory(inv, f"🎒 {owner} 的背包")
            )
            return

        # --- 放入物品（单件或批量） ---
        if sub in ("add", "放入", "添加"):
            parsed = _parse_batch_bag_add(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}bag add <名称> <数量> "
                    f"[w=单件重量] [v=单件价值] [note=备注]"
                )
                return
            if len(parsed) == 1:
                # 单件：回落原 _parse_add_tokens 路径（保留数量必填语义，零回归）。
                single = _parse_add_tokens(rest)
                if isinstance(single, str):
                    yield event.plain_result(
                        f"{single}\n用法：{display_prefix}bag add <名称> <数量> "
                        f"[w=单件重量] [v=单件价值] [note=备注]"
                    )
                    return
                entry, _ = await self._inventory.add_item(
                    event,
                    single["name"],
                    single["qty"],
                    weight=single["weight"],
                    value=single["value"],
                    note=single["note"],
                )
                yield event.plain_result(
                    f"➕ 已放入 {entry.name} ×{single['qty']}（现有 {entry.qty} 个）。"
                )
                return
            # 批量：逐件原子，失败列明、其余继续（同 /商店 批量模式）。
            ok_count = 0
            lines: list[str] = []
            for spec in parsed:
                try:
                    entry, _ = await self._inventory.add_item(
                        event,
                        spec["name"],
                        spec["qty"],
                        weight=spec["weight"],
                        value=spec["value"],
                        note=spec["note"],
                    )
                except ValueError as e:
                    lines.append(f"❌ {spec['name']}：{e}")
                    continue
                ok_count += 1
                lines.append(f"✅ {entry.name} ×{spec['qty']}（现有 {entry.qty} 个）")
            fail_count = len(parsed) - ok_count
            head = f"➕ 批量放入：成功 {ok_count} 件" + (
                f"，失败 {fail_count} 件。" if fail_count else "。"
            )
            yield event.plain_result(head + "\n" + "\n".join(lines))
            return

        # --- 取出物品（单件或批量） ---
        if sub in ("rm", "remove", "drop", "取出", "丢弃"):
            parsed = _parse_batch_name_qty(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}bag rm <名称> [数量]"
                )
                return
            if len(parsed) == 1:
                name, qty = parsed[0]
                result = await self._inventory.remove_item(event, name, qty)
                if not result.found:
                    yield event.plain_result(f"背包里没有「{name}」。")
                    return
                if result.removed_qty == 0:
                    yield event.plain_result(
                        f"背包里只有 {result.remaining} 个「{name}」，无法取出 {qty} 个。"
                    )
                    return
                if result.deleted:
                    yield event.plain_result(
                        f"➖ 已取出 {name} ×{result.removed_qty}，背包中已无此物品。"
                    )
                else:
                    yield event.plain_result(
                        f"➖ 已取出 {name} ×{result.removed_qty}"
                        f"（剩余 {result.remaining} 个）。"
                    )
                return
            ok_count = 0
            lines: list[str] = []
            for name, qty in parsed:
                result = await self._inventory.remove_item(event, name, qty)
                if not result.found:
                    lines.append(f"❌ 背包里没有「{name}」。")
                    continue
                if result.removed_qty == 0:
                    lines.append(
                        f"❌ 背包里只有 {result.remaining} 个「{name}」，"
                        f"无法取出 {qty} 个。"
                    )
                    continue
                ok_count += 1
                if result.deleted:
                    lines.append(
                        f"✅ 已取出 {name} ×{result.removed_qty}，背包中已无此物品"
                    )
                else:
                    lines.append(
                        f"✅ 已取出 {name} ×{result.removed_qty}"
                        f"（剩余 {result.remaining} 个）"
                    )
            fail_count = len(parsed) - ok_count
            head = f"➖ 批量取出：成功 {ok_count} 件" + (
                f"，失败 {fail_count} 件。" if fail_count else "。"
            )
            yield event.plain_result(head + "\n" + "\n".join(lines))
            return

        # --- 编辑物品属性（个人背包） ---
        if sub in ("edit", "编辑", "修改"):
            parsed = _parse_edit_tokens(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}bag edit <名称> "
                    f"[w=单件重量|-] [v=单件价值|-] [note=备注|-]"
                )
                return
            entry = await self._inventory.edit_item(
                event,
                parsed["name"],
                weight=parsed.get("weight", _UNSET),
                value=parsed.get("value", _UNSET),
                note=parsed.get("note", _UNSET),
            )
            if entry is None:
                yield event.plain_result(f"背包里没有「{parsed['name']}」。")
                return
            yield event.plain_result(
                f"✏️ 已更新 {InventoryManager.format_item_line(entry)}"
            )
            return

        # --- 队伍背包：编辑物品属性 ---
        if sub in ("party", "队伍", "队伍背包"):
            if is_private:
                yield event.plain_result(
                    "私聊没有队伍背包，这里只有你自己的物品。"
                )
                return
            if rest and rest[0].lower() in ("clear", "clr", "清空"):
                if not await self._check_destructive_permission(event):
                    yield event.plain_result(
                        "你没有权限清空队伍背包。"
                        + (
                            "（白名单模式已启用，请联系管理员）"
                            if self.enable_whitelist
                            else ""
                        )
                    )
                    return
                count = await self._inventory.clear_party(event)
                yield event.plain_result(
                    f"📦 已清空队伍背包，共移除 {count} 种物品。"
                    if count
                    else "📦 队伍背包本来就是空的。"
                )
                return
            if rest and rest[0].lower() in ("edit", "编辑", "修改"):
                parsed = _parse_edit_tokens(rest[1:])
                if isinstance(parsed, str):
                    yield event.plain_result(
                        f"{parsed}\n用法：{display_prefix}bag party edit <名称> "
                        f"[w=单件重量|-] [v=单件价值|-] [note=备注|-]"
                    )
                    return
                entry = await self._inventory.edit_item(
                    event,
                    parsed["name"],
                    weight=parsed.get("weight", _UNSET),
                    value=parsed.get("value", _UNSET),
                    note=parsed.get("note", _UNSET),
                    in_party=True,
                )
                if entry is None:
                    yield event.plain_result(
                        f"队伍背包里没有「{parsed['name']}」。"
                    )
                    return
                yield event.plain_result(
                    f"✏️ 已更新 {InventoryManager.format_item_line(entry)}"
                )
                return
            inv = await self._inventory.get_party(event)
            if not inv.items:
                yield event.plain_result(
                    f"📦 队伍背包是空的。用 {display_prefix}bag put <名称> <数量> 存入公共物资。"
                )
                return
            yield event.plain_result(
                InventoryManager.format_inventory(inv, "📦 队伍背包")
            )
            return

        # --- 存入队伍背包（单件或批量） ---
        if sub in ("put", "存入"):
            if is_private:
                yield event.plain_result(
                    "私聊没有队伍背包，这里只有你自己的物品。"
                )
                return
            parsed = _parse_batch_name_qty(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}bag put <名称> [数量]"
                )
                return
            if len(parsed) == 1:
                name, qty = parsed[0]
                result = await self._inventory.put_to_party(event, name, qty)
                if not result.ok:
                    if result.reason == "not_found":
                        yield event.plain_result(f"背包里没有「{name}」。")
                    else:
                        yield event.plain_result(
                            f"背包里只有 {result.available} 个「{name}」，"
                            f"无法存入 {qty} 个。"
                        )
                    return
                yield event.plain_result(
                    f"📦 已将 {result.item_name} ×{result.qty} 存入队伍背包。"
                )
                return
            ok_count = 0
            lines: list[str] = []
            for name, qty in parsed:
                result = await self._inventory.put_to_party(event, name, qty)
                if not result.ok:
                    if result.reason == "not_found":
                        lines.append(f"❌ 背包里没有「{name}」。")
                    else:
                        lines.append(
                            f"❌ 背包里只有 {result.available} 个「{name}」，"
                            f"无法存入 {qty} 个。"
                        )
                    continue
                ok_count += 1
                lines.append(f"✅ {result.item_name} ×{result.qty} 已存入队伍背包")
            fail_count = len(parsed) - ok_count
            head = f"📦 批量存入：成功 {ok_count} 件" + (
                f"，失败 {fail_count} 件。" if fail_count else "。"
            )
            yield event.plain_result(head + "\n" + "\n".join(lines))
            return

        # --- 从队伍背包取出（单件或批量） ---
        if sub in ("take", "取出公共", "拿取"):
            if is_private:
                yield event.plain_result(
                    "私聊没有队伍背包，这里只有你自己的物品。"
                )
                return
            parsed = _parse_batch_name_qty(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}bag take <名称> [数量]"
                )
                return
            if len(parsed) == 1:
                name, qty = parsed[0]
                result = await self._inventory.take_from_party(event, name, qty)
                if not result.ok:
                    if result.reason == "not_found":
                        yield event.plain_result(f"队伍背包里没有「{name}」。")
                    else:
                        yield event.plain_result(
                            f"队伍背包里只有 {result.available} 个「{name}」。"
                        )
                    return
                yield event.plain_result(
                    f"📦 已从队伍背包取出 {result.item_name} ×{result.qty}。"
                )
                return
            ok_count = 0
            lines: list[str] = []
            for name, qty in parsed:
                result = await self._inventory.take_from_party(event, name, qty)
                if not result.ok:
                    if result.reason == "not_found":
                        lines.append(f"❌ 队伍背包里没有「{name}」。")
                    else:
                        lines.append(
                            f"❌ 队伍背包里只有 {result.available} 个「{name}」。"
                        )
                    continue
                ok_count += 1
                lines.append(f"✅ 已从队伍背包取出 {result.item_name} ×{result.qty}")
            fail_count = len(parsed) - ok_count
            head = f"📦 批量取出：成功 {ok_count} 件" + (
                f"，失败 {fail_count} 件。" if fail_count else "。"
            )
            yield event.plain_result(head + "\n" + "\n".join(lines))
            return

        # --- 赠送物品 ---
        if sub in ("give", "赠送", "给予"):
            if is_private:
                yield event.plain_result("私聊中没有其他玩家可以赠送。")
                return
            # 部分平台的 message_str 会保留 @文本（如 "@某人"），过滤掉再解析；
            # 目标用户一律从消息链 At 组件提取，不依赖文本。
            parsed = _parse_name_qty([t for t in rest if not t.startswith("@")])
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}bag give @某人 <名称> [数量]"
                )
                return
            target_id = _extract_at_target(event)
            if target_id is None:
                yield event.plain_result(
                    f"用法：{display_prefix}bag give @某人 <名称> [数量]"
                )
                return
            if target_id == str(event.get_sender_id()):
                yield event.plain_result("不能赠送给自己。")
                return
            name, qty = parsed
            result = await self._inventory.give(event, target_id, name, qty)
            if not result.ok:
                if result.reason == "not_found":
                    yield event.plain_result(f"背包里没有「{name}」。")
                else:
                    yield event.plain_result(
                        f"背包里只有 {result.available} 个「{name}」，"
                        f"无法赠送 {qty} 个。"
                    )
                return
            yield event.plain_result(
                f"🎁 已将 {result.item_name} ×{result.qty} 交给 {target_id}。"
            )
            return

        # --- 清空自己的背包 ---
        if sub in ("clear", "clr", "清空"):
            count = await self._inventory.clear_personal(event)
            yield event.plain_result(
                f"🎒 已清空背包，共移除 {count} 种物品。"
                if count
                else "🎒 背包本来就是空的。"
            )
            return

        # --- help / 未知子命令 ---
        yield event.plain_result(_BAG_HELP.format(p=display_prefix))

    # ------------------------------------------------------------------
    # /发放 /收回 指令：DM 批量发放/撤回团队战利品（v0.42.0）
    # ------------------------------------------------------------------

    @filter.command("grant", alias={"发放"})
    async def grant_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        向团队背包批量发放战利品（DM 便捷指令，全员可用）。

        用法:
          /发放 <名称> [数量] [重=单件重量] [价=单件价值] [备注=备注] [<名称> …]
          示例：/发放 治疗药水 3 价=5银 火球术卷轴 1
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_party_items(
            event, arg, display_prefix="/", action="grant"
        ):
            yield msg

    @filter.command("revoke", alias={"收回"})
    async def revoke_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        从团队背包批量收回物品（管理员/白名单权限）。

        用法:
          /收回 <名称> [数量] [<名称> [数量] …]
          示例：/收回 火球术卷轴 1
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_party_items(
            event, arg, display_prefix="/", action="revoke"
        ):
            yield msg

    async def _handle_party_items(
        self,
        event: AstrMessageEvent,
        arg: str,
        display_prefix: str = "/",
        action: str = "grant",
    ) -> AsyncGenerator:
        """/发放 /收回 命令核心逻辑，由 grant_cmd/revoke_cmd 与 custom_prefix_route 统一调用。

        直接操作团队背包（inventory:party:{origin}）。整个 arg 就是物品列表
        （无子命令）。grant=发放（全员放行）；revoke=收回（走
        _check_destructive_permission，同 /bag party clear 口径）。
        私聊无队伍背包，一律拒绝。
        """
        tokens = _tokenize(arg)
        if tokens is None:
            yield event.plain_result(
                "引号未配对，物品名含空格时请用英文双引号包裹。"
            )
            return
        if event.is_private_chat():
            yield event.plain_result(
                "私聊没有队伍背包，这里只有你自己的物品。"
            )
            return
        if action == "revoke" and not await self._check_destructive_permission(event):
            yield event.plain_result(
                "你没有权限收回队伍背包物品。"
                + (
                    "（白名单模式已启用，请联系管理员）"
                    if self.enable_whitelist
                    else ""
                )
            )
            return

        if action == "grant":
            parsed = _parse_batch_bag_add(tokens)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}发放 <名称> [数量] "
                    f"[重=单件重量] [价=单件价值] [备注=备注]"
                )
                return
            if len(parsed) == 1:
                spec = parsed[0]
                entry, _ = await self._inventory.add_item(
                    event,
                    spec["name"],
                    spec["qty"],
                    weight=spec["weight"],
                    value=spec["value"],
                    note=spec["note"],
                    to_party=True,
                )
                yield event.plain_result(
                    f"➕ 已发放到队伍背包：{entry.name} ×{spec['qty']}"
                    f"（现有 {entry.qty} 个）。"
                )
                return
            ok_count = 0
            lines: list[str] = []
            for spec in parsed:
                try:
                    entry, _ = await self._inventory.add_item(
                        event,
                        spec["name"],
                        spec["qty"],
                        weight=spec["weight"],
                        value=spec["value"],
                        note=spec["note"],
                        to_party=True,
                    )
                except ValueError as e:
                    lines.append(f"❌ {spec['name']}：{e}")
                    continue
                ok_count += 1
                lines.append(f"✅ {entry.name} ×{spec['qty']}（现有 {entry.qty} 个）")
            fail_count = len(parsed) - ok_count
            head = f"➕ 批量发放：成功 {ok_count} 件" + (
                f"，失败 {fail_count} 件。" if fail_count else "。"
            )
            yield event.plain_result(head + "\n" + "\n".join(lines))
            return

        # --- revoke ---
        parsed = _parse_batch_name_qty(tokens)
        if isinstance(parsed, str):
            yield event.plain_result(
                f"{parsed}\n用法：{display_prefix}收回 <名称> [数量]"
            )
            return
        if len(parsed) == 1:
            name, qty = parsed[0]
            result = await self._inventory.remove_item(
                event, name, qty, from_party=True
            )
            if not result.found:
                yield event.plain_result(f"队伍背包里没有「{name}」。")
                return
            if result.removed_qty == 0:
                yield event.plain_result(
                    f"队伍背包里只有 {result.remaining} 个「{name}」，"
                    f"无法收回 {qty} 个。"
                )
                return
            if result.deleted:
                yield event.plain_result(
                    f"➖ 已从队伍背包收回 {name} ×{result.removed_qty}，"
                    f"队伍背包中已无此物品。"
                )
            else:
                yield event.plain_result(
                    f"➖ 已从队伍背包收回 {name} ×{result.removed_qty}"
                    f"（剩余 {result.remaining} 个）。"
                )
            return
        ok_count = 0
        lines: list[str] = []
        for name, qty in parsed:
            result = await self._inventory.remove_item(
                event, name, qty, from_party=True
            )
            if not result.found:
                lines.append(f"❌ 队伍背包里没有「{name}」。")
                continue
            if result.removed_qty == 0:
                lines.append(
                    f"❌ 队伍背包里只有 {result.remaining} 个「{name}」，"
                    f"无法收回 {qty} 个。"
                )
                continue
            ok_count += 1
            if result.deleted:
                lines.append(
                    f"✅ 已收回 {name} ×{result.removed_qty}，队伍背包中已无此物品"
                )
            else:
                lines.append(
                    f"✅ 已收回 {name} ×{result.removed_qty}"
                    f"（剩余 {result.remaining} 个）"
                )
        fail_count = len(parsed) - ok_count
        head = f"➖ 批量收回：成功 {ok_count} 件" + (
            f"，失败 {fail_count} 件。" if fail_count else "。"
        )
        yield event.plain_result(head + "\n" + "\n".join(lines))

    # ------------------------------------------------------------------
    # /商店 指令：商店管理（v0.20.0）
    # ------------------------------------------------------------------

    @filter.command("shop", alias={"商店", "店铺"})
    async def shop_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        会话商店：查看商品、购买/卖回、DM 配置商品列表。

        用法:
          /商店                           查看商品列表
          /商店 买 <名称> [数量] [<名称> [数量] …]   购买，可批量（自动扣背包货币并找零）
          /商店 卖 <名称> [数量] [<名称> [数量] …]   卖回商店，可批量（只收在架商品）
          /商店 初始化                     用 PHB/XPHB 非魔法物品重建商店（管理员）
          /商店 上架 <名称> [价=金额] [库存=数量] [<名称> …]   可批量上架（管理员）
          /商店 下架 <名称> [<名称> …]     可批量下架（管理员）
          /商店 设价 <名称> <金额|自动>    覆盖价格（自动=恢复库价；管理员）
          /商店 设库存 <名称> <数量|无限>  设置库存（管理员）
          /商店 回购率 <系数>              设置回购系数（管理员）
          /商店 清空                       清空整店全部商品（管理员；回购系数保留）
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_shop(event, arg, display_prefix="/"):
            yield msg

    async def _handle_shop(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """/商店 命令核心逻辑，由 shop_cmd 和 custom_prefix_route 统一调用。"""
        tokens = _tokenize(arg)
        if tokens is None:
            yield event.plain_result(
                "引号未配对，物品名含空格时请用英文双引号包裹。"
            )
            return

        sub = tokens[0].lower() if tokens else ""
        rest = tokens[1:]
        origin = event.unified_msg_origin
        manager = self.shop_manager

        # --- 查看商品列表（支持翻页：/商店 2 或 /商店 页 2） ---
        if (
            not tokens
            or sub in ("list", "ls", "查看", "列表")
            or sub.isdigit()
            or sub in ("页", "page", "p")
        ):
            page = 1
            if sub.isdigit():
                page = int(sub)
            elif sub in ("页", "page", "p"):
                if not rest or not rest[0].isdigit():
                    yield event.plain_result(
                        f"用法：{display_prefix}商店 页 <页码>（或直接 {display_prefix}商店 <页码>）"
                    )
                    return
                page = int(rest[0])
            shop = await manager.get(origin)
            if not shop.entries:
                yield event.plain_result(
                    f"🏪 本店还没有商品。管理员可用 {display_prefix}商店 初始化"
                    " 一键上架 PHB/XPHB 非魔法物品，或 上架 逐条添加。"
                )
                return
            total_pages = max(
                1, (len(shop.entries) + ShopManager.list_limit() - 1)
                // ShopManager.list_limit()
            )
            if page < 1:
                page = 1
            elif page > total_pages:
                page = total_pages
            yield event.plain_result(
                ShopManager.format_shop(
                    shop, page=page, price_resolver=manager.resolve_price
                )
            )
            return

        # --- 购买 ---
        if sub in ("buy", "买", "购买"):
            parsed = _parse_batch_name_qty(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}商店 买 <名称> [数量]"
                    " [<名称> [数量] …]"
                )
                return
            if len(parsed) == 1:
                # 单件购买：保留详细文案（历史行为）
                name, qty = parsed[0]
                result = await manager.buy(event, origin, name, qty)
                if not result.ok:
                    if result.reason == "not_found":
                        yield event.plain_result(
                            f"商店里没有「{name}」。用 {display_prefix}商店 上架 添加，"
                            f"或 {display_prefix}商店 查看 在售商品。"
                        )
                    elif result.reason == "sold_out":
                        yield event.plain_result(
                            f"「{name}」库存不足（现有 {result.stock_left} 个），"
                            "无法购买。"
                        )
                    elif result.reason == "no_price":
                        yield event.plain_result(
                            f"「{name}」没有定价（知识库无库价）。"
                            f"请管理员用 {display_prefix}商店 设价 <名称> <金额> 覆盖。"
                        )
                    else:  # insufficient_money
                        yield event.plain_result(
                            f"钱不够：购买需 {format_cp(result.total_cp)}"
                            f"（{format_cp(result.price_cp)}/件 ×{result.qty}），"
                            f"还差 {format_cp(result.shortfall_cp)}。"
                            "背包里用 /bag add 金币 等条目存钱（1金币=100铜币）。"
                        )
                    return
                stock_note = (
                    f"，余 {result.stock_left} 件" if result.stock_left is not None else ""
                )
                yield event.plain_result(
                    f"🛒 已购买 {result.item_name} ×{result.qty}，"
                    f"花费 {format_cp(result.total_cp)}"
                    f"（{format_cp(result.price_cp)}/件）{stock_note}。"
                    "已自动入包并附带库重/价值。"
                )
                return
            # 批量购买（逐件原子，失败件列明原因、其余继续）
            lines: list[str] = []
            ok_count = 0
            for name, qty in parsed:
                result = await manager.buy(event, origin, name, qty)
                if result.ok:
                    ok_count += 1
                lines.append(_format_buy_result(result))
            fail_count = len(parsed) - ok_count
            summary = f"🛒 批量购买：成功 {ok_count} 件"
            if fail_count:
                summary += f"，失败 {fail_count} 件"
            yield event.plain_result(summary + "。\n" + "\n".join(lines))
            return

        # --- 卖回商店 ---
        if sub in ("sell", "卖", "出售"):
            parsed = _parse_batch_name_qty(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}商店 卖 <名称> [数量]"
                    " [<名称> [数量] …]"
                )
                return
            if len(parsed) == 1:
                # 单件卖回：保留详细文案（历史行为）
                name, qty = parsed[0]
                result = await manager.sell(event, origin, name, qty)
                if not result.ok:
                    if result.reason == "not_found":
                        yield event.plain_result(
                            f"商店不收「{name}」（只回收在架商品，"
                            f"且按售价×回购系数计价）。"
                        )
                    elif result.reason == "no_price":
                        yield event.plain_result(
                            f"「{name}」没有定价（知识库无库价），暂无法回收。"
                        )
                    else:  # insufficient / 背包无货
                        yield event.plain_result(
                            f"背包里没有足够的「{name}」可以出售。"
                        )
                    return
                stock_note = (
                    f"，商店余 {result.stock_left} 件" if result.stock_left is not None else ""
                )
                yield event.plain_result(
                    f"💰 已卖出 {result.item_name} ×{result.qty}，"
                    f"获得 {format_cp(result.pay_cp)}"
                    f"（{format_cp(result.price_cp)}/件×回购系数）{stock_note}。"
                )
                return
            # 批量卖回（逐件原子，失败件列明原因、其余继续）
            lines: list[str] = []
            ok_count = 0
            for name, qty in parsed:
                result = await manager.sell(event, origin, name, qty)
                if result.ok:
                    ok_count += 1
                lines.append(_format_sell_result(result))
            fail_count = len(parsed) - ok_count
            summary = f"💰 批量卖出：成功 {ok_count} 件"
            if fail_count:
                summary += f"，失败 {fail_count} 件"
            yield event.plain_result(summary + "。\n" + "\n".join(lines))
            return

        # --- 管理类子命令：全部需要破坏性权限 ---
        if sub in ("init", "初始化", "add", "上架", "remove", "rm", "下架",
                   "price", "设价", "stock", "设库存", "rate", "回购率",
                   "clear", "clr", "清空", "清除"):
            if not await self._check_destructive_permission(event):
                yield event.plain_result(
                    "你没有权限配置商店（上架/下架/设价/设库存/初始化/回购率/清空）。"
                    + (
                        "（白名单模式已启用，请联系管理员）"
                        if self.enable_whitelist
                        else ""
                    )
                )
                return

        # --- 清空商店（管理员） ---
        if sub in ("clear", "clr", "清空", "清除"):
            count = await manager.clear(origin)
            yield event.plain_result(
                f"🏪 已清空商店，共移除 {count} 种商品。"
                if count else "商店本来就是空的。"
            )
            return

        # --- 初始化商店 ---
        if sub in ("init", "初始化"):
            if not self.kb_manager.available:
                yield event.plain_result("知识库不可用，无法初始化。")
                return
            try:
                seeds = self.kb_manager.list_init_shop_items()
            except Exception as e:  # noqa: BLE001 — 旧库缺列时给友好提示
                logger.warning(f"[trpg_assistant] 初始商店候选查询失败: {e}")
                yield event.plain_result(
                    "初始化失败：知识库版本过低（缺少价格数据，需要 schema v5 内置库）。"
                    "请重新安装插件或等待知识库更新。"
                )
                return
            if not seeds:
                yield event.plain_result(
                    "没有可用的初始商品（知识库中无 PHB/XPHB 非魔法有价物品）。"
                )
                return
            count = await manager.init_from_kb(origin, seeds)
            yield event.plain_result(
                f"🏪 已重建商店：上架 {count} 种 PHB/XPHB 非魔法物品"
                "（默认库价、无限库存）。可再用 设价/设库存/下架 调整。"
            )
            return

        # --- 上架 ---
        if sub in ("add", "上架"):
            parsed = self._parse_batch_shop_add(rest)
            if isinstance(parsed, str):
                yield event.plain_result(
                    f"{parsed}\n用法：{display_prefix}商店 上架 <名称> "
                    "[价=金额] [库存=数量|无限] [<名称> [价=…] [库存=…] …]"
                )
                return
            if len(parsed) == 1:
                # 单件上架：保留原逻辑
                spec = parsed[0]
                weight_lb = self._shop_add_weight(spec["name"])
                ok, reason = await manager.add_entry(
                    origin,
                    spec["name"],
                    price_cp=spec["price"],
                    stock=spec["stock"],
                    weight_lb=weight_lb,
                )
                if not ok:
                    yield event.plain_result(reason)
                    return
                price_note = (
                    format_cp(spec["price"]) if spec["price"] is not None else "库价"
                )
                stock_note = (
                    "无限" if spec["stock"] is None else f"库存 {spec['stock']}"
                )
                yield event.plain_result(
                    f"➕ 已上架 {spec['name']}（{price_note}，{stock_note}）。"
                )
                return
            # 批量上架（逐件原子，失败件列明原因、其余继续）
            lines: list[str] = []
            ok_count = 0
            for spec in parsed:
                weight_lb = self._shop_add_weight(spec["name"])
                ok, reason = await manager.add_entry(
                    origin,
                    spec["name"],
                    price_cp=spec["price"],
                    stock=spec["stock"],
                    weight_lb=weight_lb,
                )
                if not ok:
                    lines.append(f"❌ {reason}")
                    continue
                ok_count += 1
                price_note = (
                    format_cp(spec["price"]) if spec["price"] is not None else "库价"
                )
                stock_note = (
                    "无限" if spec["stock"] is None else f"库存 {spec['stock']}"
                )
                lines.append(
                    f"➕ 已上架 {spec['name']}（{price_note}，{stock_note}）。"
                )
            fail_count = len(parsed) - ok_count
            summary = f"➕ 批量上架：成功 {ok_count} 件"
            if fail_count:
                summary += f"，失败 {fail_count} 件"
            yield event.plain_result(summary + "。\n" + "\n".join(lines))
            return

        # --- 下架 ---
        if sub in ("remove", "rm", "下架"):
            if not rest:
                yield event.plain_result(
                    f"用法：{display_prefix}商店 下架 <名称> [<名称> …]"
                )
                return
            removed: list[str] = []
            missing: list[str] = []
            for name in rest:
                if await manager.remove_entry(origin, name):
                    removed.append(name)
                else:
                    missing.append(name)
            if len(rest) == 1:
                # 单件下架：保留原文案
                if missing:
                    yield event.plain_result(f"商店里没有「{missing[0]}」。")
                else:
                    yield event.plain_result(f"➖ 已下架 {removed[0]}。")
                return
            # 批量下架
            text_parts: list[str] = []
            if removed:
                text_parts.append(
                    f"➖ 已下架：{'、'.join(removed)}（共 {len(removed)} 件）。"
                )
            for n in missing:
                text_parts.append(f"❌ 商店里没有「{n}」。")
            yield event.plain_result(
                "\n".join(text_parts) if text_parts else "没有下架任何商品。"
            )
            return

        # --- 设价 ---
        if sub in ("price", "设价"):
            if not rest:
                yield event.plain_result(
                    f"用法：{display_prefix}商店 设价 <名称> <金额|自动>"
                )
                return
            name = rest[0]
            if len(rest) < 2:
                yield event.plain_result(
                    f"用法：{display_prefix}商店 设价 <名称> <金额|自动>"
                )
                return
            amount = rest[1]
            if amount in ("自动", "auto", "库价"):
                price_cp = None
                price_note = "恢复库价"
            else:
                price_cp = parse_money(amount)
                if price_cp is None:
                    yield event.plain_result(
                        f"无法解析价格「{amount}」（支持 2金5银 / 150 / 自动）。"
                    )
                    return
                price_note = format_cp(price_cp)
            if not await manager.set_price(origin, name, price_cp):
                yield event.plain_result(f"商店里没有「{name}」。")
                return
            yield event.plain_result(f"✏️ 已设置 {name} 价格为 {price_note}。")
            return

        # --- 设库存 ---
        if sub in ("stock", "设库存"):
            if len(rest) < 2:
                yield event.plain_result(
                    f"用法：{display_prefix}商店 设库存 <名称> <数量|无限>"
                )
                return
            name = rest[0]
            amount = rest[1]
            if amount in ("无限", "inf", "∞"):
                stock = None
                stock_note = "无限"
            elif amount.isdigit():
                stock = int(amount)
                stock_note = f"{stock}（{'售罄' if stock == 0 else ''}）"
            else:
                yield event.plain_result(
                    f"无法解析库存「{amount}」（支持数字或 无限）。"
                )
                return
            if not await manager.set_stock(origin, name, stock):
                yield event.plain_result(f"商店里没有「{name}」。")
                return
            yield event.plain_result(
                f"✏️ 已设置 {name} 库存为 {stock_note}。"
            )
            return

        # --- 回购率 ---
        if sub in ("rate", "回购率"):
            if not rest:
                yield event.plain_result(
                    f"用法：{display_prefix}商店 回购率 <系数>（如 0.5 = 半价）"
                )
                return
            try:
                rate = float(rest[0])
            except ValueError:
                yield event.plain_result(f"无法解析系数「{rest[0]}」。")
                return
            if rate < 0 or rate > 2:
                yield event.plain_result("回购系数需在 0 到 2 之间。")
                return
            clamped = await manager.set_rate(origin, rate)
            yield event.plain_result(
                f"✏️ 回购系数已设为 {clamped:g}（卖出价 = 售价 × 系数）。"
            )
            return

        # --- help / 未知子命令 ---
        yield event.plain_result(_SHOP_HELP.format(p=display_prefix))

    def _shop_add_weight(self, name: str) -> float | None:
        """上架时从知识库带出单件重量（库不可用/异常时降级 None）。"""
        if not self.kb_manager.available:
            return None
        try:
            stats = self.kb_manager.item_price(name)
            if stats is not None:
                return stats[1]
        except Exception as e:  # noqa: BLE001 — 库异常不阻断上架
            logger.warning(f"[trpg_assistant] 上架查库重失败: {e}")
        return None

    @staticmethod
    def _parse_shop_add(tokens: list[str]) -> dict | str:
        """解析上架参数：名称 + 可选 [价=金额] [库存=数量|无限]。

        返回 dict（name/price/stock）或错误提示字符串。
        """
        if not tokens:
            return "缺少商品名称"
        name = tokens[0]
        price: int | None = None
        stock: int | None = None
        for t in tokens[1:]:
            low = t.lower()
            if low.startswith("价="):
                m = parse_money(t[2:])
                if m is None:
                    return f"无法解析价格「{t[2:]}」（支持 2金5银 / 150）。"
                price = m
            elif low.startswith("库存="):
                s = t[3:]
                if s in ("无限", "inf", "∞"):
                    stock = None
                elif s.isdigit():
                    stock = int(s)
                else:
                    return f"无法解析库存「{s}」（支持数字或 无限）。"
            else:
                return f"无法识别的参数「{t}」（支持 价=金额 库存=数量|无限）。"
        return {"name": name, "price": price, "stock": stock}

    @staticmethod
    def _parse_batch_shop_add(tokens: list[str]) -> list[dict] | str:
        """解析批量上架参数：「名称 [价=X] [库存=Y] 名称 [价=X] …」。

        非「价=/库存=」前缀 token = 下一个物品名；属性 token 归当前物品
        （逐项属性归属）。返回 [{"name","price","stock"}, ...] 或错误文案。
        """
        if not tokens:
            return "缺少商品名称"
        items: list[dict] = []
        for t in tokens:
            low = t.lower()
            if low.startswith("价="):
                if not items:
                    return f"属性「{t}」前缺少商品名称。"
                m = parse_money(t[2:])
                if m is None:
                    return f"无法解析价格「{t[2:]}」（支持 2金5银 / 150）。"
                items[-1]["price"] = m
            elif low.startswith("库存="):
                if not items:
                    return f"属性「{t}」前缺少商品名称。"
                s = t[3:]
                if s in ("无限", "inf", "∞"):
                    items[-1]["stock"] = None
                elif s.isdigit():
                    items[-1]["stock"] = int(s)
                else:
                    return f"无法解析库存「{s}」（支持数字或 无限）。"
            else:
                items.append({"name": t, "price": None, "stock": None})
        return items

    # ------------------------------------------------------------------
    # /卡 指令：角色卡管理
    # ------------------------------------------------------------------

    # /卡 设 的字段白名单：命令 token → update_fields key。
    _CARD_SET_FIELD = {
        "hp": "hp", "血量": "hp",
        "ac": "ac", "护甲值": "ac",
        "速度": "speed", "speed": "speed", "移动速度": "speed",
        "slot1": "slot1", "slot2": "slot2", "slot3": "slot3",
        "slot4": "slot4", "slot5": "slot5", "slot6": "slot6",
        "slot7": "slot7", "slot8": "slot8", "slot9": "slot9",
        "法术位1": "slot1", "法术位2": "slot2", "法术位3": "slot3",
        "法术位4": "slot4", "法术位5": "slot5", "法术位6": "slot6",
        "法术位7": "slot7", "法术位8": "slot8", "法术位9": "slot9",
        "攻击": "attack", "attack": "attack",
        "主手": "main_hand", "main_hand": "main_hand",
        "副手": "off_hand", "off_hand": "off_hand",
        "护甲": "armor", "armor": "armor",
        "生平": "backstory", "backstory": "backstory",
        "背景故事": "backstory",
        "背景": "background", "background": "background",
        "阵营": "alignment", "alignment": "alignment",
        "专精": "expertise", "expertise": "expertise",
        "专长": "feats", "feats": "feats",
        "工具熟练": "tools", "tools": "tools",
        "武器熟练": "weapons", "weapons": "weapons",
        "防具熟练": "armors", "armors": "armors",
        "语言": "languages", "languages": "languages", "lang": "languages",
        # v0.30.0：人物基础信息 / 资源 / 先攻 / 已知法术
        "信仰": "deity", "deity": "deity",
        "年龄": "age", "age": "age",
        "性别": "gender", "gender": "gender",
        "身高": "height", "height": "height",
        "体重": "weight", "weight": "weight",
        "生命骰已用": "hit_dice_used", "hit_dice_used": "hit_dice_used",
        "短休已用": "hit_dice_used",
        "激励": "inspiration", "inspiration": "inspiration",
        "先攻": "initiative", "initiative": "initiative",
        "法术": "spells", "spells": "spells", "已知法术": "spells",
        # v0.41.0：六维属性 / 种族 / 职业 / 规则版本（均可单独设置）
        "力量": "str", "str": "str", "strength": "str", "力": "str",
        "敏捷": "dex", "dex": "dex", "dexterity": "dex", "敏": "dex",
        "体质": "con", "con": "con", "constitution": "con", "体": "con",
        "智力": "int", "int": "int", "intelligence": "int", "智": "int",
        "感知": "wis", "wis": "wis", "wisdom": "wis", "感": "wis",
        "魅力": "cha", "cha": "cha", "charisma": "cha", "魅": "cha",
        "种族": "race", "race": "race",
        "职业": "classes", "classes": "classes", "class": "classes",
        "版本": "edition", "edition": "edition", "规则版本": "edition",
    }

    # v0.41.0：/卡 设 之后需要触发规则引擎重算的字段。
    # 六维属性变化联动先攻（敏捷修正）/HP（体质修正）/AC（轻甲敏捷）/攻击加值 base；
    # 职业/版本变化直接影响全部战斗字段；装备槽变化影响 AC/攻击加值。
    _CARD_RECALC_FIELDS = frozenset(
        {"main_hand", "off_hand", "armor", "classes", "edition"}
        | set(ABILITY_NAMES)
    )

    @filter.command("卡", alias={"char", "角色卡"})
    async def char_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        角色卡管理（多卡 + 活跃切换）。

        用法:
          /卡                    查看自己的活跃角色卡
          /卡 列表               列出全部角色卡（⭐ 为活跃卡）
          /卡 用 <卡名>          切换活跃卡
          /卡 删 <卡名>          删除角色卡
          /卡 改名 <旧名> <新名>  重命名
          /卡 设 <字段> <值>     设置字段（hp/ac/速度/法术位N/攻击/主手/副手/护甲/背景/种族/职业/版本/六维属性（力量/敏捷/体质/智力/感知/魅力）/生平/背景故事/阵营/语言/信仰/年龄/性别/身高/体重/生命骰已用/激励/先攻/法术）
          /卡 详情 [字段]       查看完整字段：生平/人物信息/专长/特性（种族+职业）/攻击/熟练/语言/装备/法术（卡面折叠的全文）
          /卡 升级 [职业名]      指定职业 +1 级（默认主职业），战斗字段自动重算
          /卡 降级 [职业名]      指定职业 -1 级（默认主职业，最低 1 级），战斗字段自动重算
          /卡 熟练 技能 +察觉 -隐匿   增减技能熟练（+加 -减）
          /卡 熟练 豁免 +力 -敏        增减豁免熟练
          /卡 法术 加 <环阶> <法术名>  已知法术单条加入（环阶：戏法/一环/1环/1）
          /卡 法术 删 <环阶> <法术名>  已知法术单条删除
          /卡 骰 <名称> <表达式> 命名掷骰（登记后 /r <名称> 直接用该表达式；/卡 骰 <名称> - 删除）
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_character(event, arg, display_prefix="/"):
            yield msg

    async def _handle_character(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """
        /卡 命令核心逻辑，由 char_cmd 和 custom_prefix_route 统一调用。

        Args:
            event: 消息事件。
            arg: 去除命令名后的参数字符串（空字符串表示查看活跃卡）。
            display_prefix: 回复提示中显示的前缀符号。
        """
        tokens = _tokenize(arg)
        if tokens is None:
            yield event.plain_result("引号未配对，卡名含空格时请用英文双引号包裹。")
            return
        sub = tokens[0].lower() if tokens else ""
        rest = tokens[1:]
        cm = self.character_manager

        # --- 查看活跃卡 / 列表 ---
        if not tokens or sub in ("list", "ls", "列表", "查看"):
            if sub in ("list", "ls", "列表") or (tokens and sub != ""):
                names = await cm.list_cards(event)
                active = await cm.get_active_name(event)
                yield event.plain_result(cm.format_card_list(names, active))
                return
            card = await cm.get_card(event)
            if card is None:
                yield event.plain_result(
                    f"还没有角色卡。用 {display_prefix}车卡 开始引导创建，"
                    f"或 {display_prefix}帮助 角色卡 查看用法。"
                )
                return
            yield event.plain_result(cm.format_sheet(card, self.kb_manager))
            return

        # --- 切换活跃卡 ---
        if sub in ("use", "用", "切换"):
            if not rest:
                yield event.plain_result(f"用法：{display_prefix}卡 用 <卡名>")
                return
            name = " ".join(rest)
            ok = await cm.set_active(event, name)
            yield event.plain_result(
                f"已将活跃卡切换为「{name}」。" if ok else f"没有名为「{name}」的卡。"
            )
            return

        # --- 删除角色卡 ---
        if sub in ("delete", "del", "删", "删除"):
            if not rest:
                yield event.plain_result(f"用法：{display_prefix}卡 删 <卡名>")
                return
            name = " ".join(rest)
            ok = await cm.delete_card(event, name)
            yield event.plain_result(
                f"已删除角色卡「{name}」。活跃卡已回退到列表第一张。"
                if ok
                else f"没有名为「{name}」的卡。"
            )
            return

        # --- 重命名 ---
        if sub in ("rename", "改名"):
            if len(rest) < 2:
                yield event.plain_result(f"用法：{display_prefix}卡 改名 <旧名> <新名>")
                return
            old = rest[0]
            new = " ".join(rest[1:])
            ok, msg = await cm.rename_card(event, old, new)
            yield event.plain_result(msg if not ok else msg)
            return

        # --- 设置字段 ---
        if sub in ("set", "设"):
            if len(rest) < 2:
                yield event.plain_result(
                    f"用法：{display_prefix}卡 设 <字段> <值>"
                    "（字段：hp/ac/速度/法术位N/攻击/主手/副手/护甲/背景/种族/职业/版本/六维属性（力量/敏捷/体质/智力/感知/魅力）/生平/背景故事/阵营/语言/信仰/年龄/性别/身高/体重/生命骰已用/激励/先攻/法术）"
                )
                return
            key = self._CARD_SET_FIELD.get(rest[0].strip().lower())
            if key is None:
                yield event.plain_result(
                    f"未知字段「{rest[0]}」。可用：hp/ac/速度/法术位N/攻击/主手/副手/护甲/背景/种族/职业/版本/六维属性（力量/敏捷/体质/智力/感知/魅力）/生平/背景故事/阵营/语言/信仰/年龄/性别/身高/体重/生命骰已用/激励/先攻/法术。"
                )
                return
            value = " ".join(rest[1:])
            card, applied = await cm.update_fields(event, None, {key: value})
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            extra = ""
            if set(applied) & self._CARD_RECALC_FIELDS:
                report = self._recalc_card(card)
                if report is not None:
                    # update_fields 已落库，重算后 base 变化需再保存一次
                    err = await cm.save_card(event, card)
                    if report.text:
                        extra = "\n自动重算：" + report.text
                    if err is not None:
                        extra = f"\n自动重算后保存失败：{err}"
            if key == "attack" and re.search(r"=\s*[－\-]\s*$", value):
                # v0.31.0：删除形态「名称=-」——未应用说明条目不存在
                if "attack" not in applied:
                    extra = "\n未找到该攻击条目（可用 /卡 详情 攻击 查看现有条目）。"
                else:
                    target = value.split("=", 1)[0].strip()
                    if self._is_generated_attack(card, target):
                        extra = "\n注意：该条目由规则引擎按装备/职业自动生成，重算后会恢复；要彻底移除请先清空对应装备槽。"
            yield event.plain_result(
                f"已更新 {','.join(applied)}。{extra}\n" + cm.format_sheet(card, self.kb_manager)
            )
            return

        # --- 已知法术单条增删（v0.31.0：/卡 法术 加|删 <环阶> <法术名>） ---
        if sub in ("spell", "spells", "法术"):
            if (
                len(rest) < 3
                or rest[0].lower() not in ("加", "add", "+", "删", "del", "remove", "-")
            ):
                yield event.plain_result(
                    f"用法：{display_prefix}卡 法术 加 <环阶> <法术名> ｜ "
                    f"{display_prefix}卡 法术 删 <环阶> <法术名>\n"
                    "环阶支持：戏法/0环/cantrip、一环/1环/1 … 九环/9环。"
                )
                return
            op = rest[0].lower()
            add = op in ("加", "add", "+")
            ring_raw = rest[1]
            sname = " ".join(rest[2:])
            card = await cm.get_card(event)
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            ok = card.add_spell(ring_raw, sname) if add else card.remove_spell(
                ring_raw, sname
            )
            if not ok:
                if add:
                    yield event.plain_result(
                        "环阶无法识别（支持 戏法/0环/cantrip、一环/1环/1 … 九环/9环）或法术名为空。"
                    )
                else:
                    rings = "、".join(_spell_ring_label(r) for r in card.spells) or "无"
                    yield event.plain_result(
                        f"未找到「{sname}」在 {ring_raw} 下的记录（现有环阶：{rings}）。"
                    )
                return
            err = await cm.save_card(event, card)
            ring = _norm_spell_ring(ring_raw)
            label = _spell_ring_label(ring) if ring else "已知法术"
            msg = f"已{'加入' if add else '删除'}法术「{sname}」（{label}）。"
            if err is not None:
                msg += f" 保存失败：{err}"
            msg += "\n" + cm.format_sheet(card, self.kb_manager)
            yield event.plain_result(msg)
            return

        # --- 详情（v0.22.2：查看卡面上被折叠的完整字段） ---
        if sub in ("detail", "详情", "完整"):
            card = await cm.get_card(event)
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            field = rest[0].strip().lower() if rest else ""
            if not field:
                yield event.plain_result(
                    f"「{card.name}」可查看的完整字段：\n"
                    "· 背景\n"
                    "· 生平（背景故事）\n"
                    "· 人物信息（信仰/年龄/性别/身高/体重）\n"
                    "· 专长\n"
                    "· 特性（种族+职业）\n"
                    "· 攻击\n"
                    "· 熟练（技能/豁免/工具/武器/防具）\n"
                    "· 语言\n"
                    "· 装备\n"
                    "· 法术（已知法术全文）\n"
                    "· 掷骰（命名掷骰，v0.32.0）\n"
                    f"用法：{display_prefix}卡 详情 <字段>"
                )
                return
            if field in ("背景", "background", "bg"):
                yield event.plain_result(
                    f"📜 {card.name} · 背景\n" + (card.background or "（未填写）")
                )
                return
            if field in ("生平", "backstory", "背景故事", "story"):
                yield event.plain_result(
                    f"📜 {card.name} · 生平\n" + (card.backstory or "（未填写）")
                )
                return
            if field in ("人物信息", "人物", "info", "profile"):
                parts: list[str] = []
                if card.gender:
                    parts.append(f"性别 {card.gender}")
                if card.age:
                    parts.append(f"年龄 {card.age}")
                if card.height:
                    parts.append(f"身高 {card.height}")
                if card.weight:
                    parts.append(f"体重 {card.weight}")
                if card.deity:
                    parts.append(f"信仰 {card.deity}")
                lines = [f"📜 {card.name} · 人物信息"]
                lines.append("　".join(parts) if parts else "（未填写）")
                yield event.plain_result("\n".join(lines))
                return
            if field in ("掷骰", "named", "named_roll", "命名掷骰", "骰"):
                if not card.named_rolls:
                    yield event.plain_result(f"「{card.name}」没有登记命名掷骰。")
                    return
                lines = [f"📜 {card.name} · 命名掷骰（{len(card.named_rolls)} 项）"]
                for n, e in sorted(card.named_rolls.items()):
                    lines.append(f"· {n}：{e}")
                yield event.plain_result("\n".join(lines))
                return
            if field in ("专长", "feats", "feat"):
                if not card.feats:
                    yield event.plain_result(f"「{card.name}」没有专长。")
                    return
                yield event.plain_result(
                    f"📜 {card.name} · 专长（{len(card.feats)} 项）\n"
                    + "\n".join(f"{i}. {f}" for i, f in enumerate(card.feats, 1))
                )
                return
            if field in ("特性", "features", "feature", "职业特性"):
                lines = [f"📜 {card.name} · 特性"]
                # 种族特性（v0.23.0）
                if card.race and self.kb_manager is not None:
                    try:
                        race_feats = self.kb_manager.race_features(
                            card.race, card.edition
                        )  # type: ignore[attr-defined]
                    except Exception:  # noqa: BLE001 — kb 不可用不影响
                        race_feats = []
                    if race_feats:
                        lines.append("· 种族（" + card.race + "）：" + "、".join(race_feats))
                # 职业特性
                if not card.classes:
                    lines.append("（未设定职业）")
                elif self.kb_manager is None:
                    lines.append("（知识库不可用）")
                else:
                    for c in card.classes:
                        try:
                            result = self.kb_manager.class_features(
                                c.class_name, c.subclass or None
                            )  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001 — kb 不可用不影响
                            continue
                        rows: list[tuple[int, str]] = []
                        seen: set[str] = set()
                        for row in list(result.base_rows) + list(result.subclass_rows):
                            if row.level <= c.level and row.name not in seen:
                                seen.add(row.name)
                                rows.append((row.level, row.name))
                        if rows:
                            label = (
                                c.class_name
                                + (f"（{c.subclass}）" if c.subclass else "")
                            )
                            lines.append(
                                f"· {label} {c.level} 级："
                                + "、".join(f"[{lv}]{name}" for lv, name in rows)
                            )
                yield event.plain_result("\n".join(lines))
                return
            if field in ("攻击", "attack", "atk"):
                if not card.attack_bonuses:
                    yield event.plain_result(f"「{card.name}」没有攻击条目。")
                    return
                lines = [f"📜 {card.name} · 攻击（{len(card.attack_bonuses)} 项）"]
                for name, v in sorted(card.attack_bonuses.items()):
                    lines.append(f"· {name}: {CharacterManager._fmt_stat(v)}")
                yield event.plain_result("\n".join(lines))
                return
            if field in ("熟练", "prof", "proficiency"):
                parts: list[str] = []
                if card.save_proficiencies:
                    parts.append(
                        "豁免 " + "、".join(
                            ABILITY_CN[a] for a in sorted(card.save_proficiencies)
                        )
                    )
                if card.skill_proficiencies:
                    parts.append(
                        "技能 " + "、".join(
                            SKILL_CN_REV.get(s, s)
                            for s in sorted(card.skill_proficiencies)
                        )
                    )
                if card.tool_proficiencies:
                    parts.append("工具 " + "、".join(sorted(card.tool_proficiencies)))
                if card.weapon_proficiencies:
                    parts.append("武器 " + "、".join(sorted(card.weapon_proficiencies)))
                if card.armor_proficiencies:
                    parts.append("防具 " + "、".join(sorted(card.armor_proficiencies)))
                if not parts:
                    yield event.plain_result(f"「{card.name}」没有任何熟练。")
                    return
                yield event.plain_result(
                    f"📜 {card.name} · 熟练\n" + "\n".join(f"· {p}" for p in parts)
                )
                return
            if field in ("语言", "languages", "lang"):
                if not card.languages:
                    yield event.plain_result(f"「{card.name}」没有登记语言。")
                    return
                yield event.plain_result(
                    f"📜 {card.name} · 语言（{len(card.languages)} 门）\n"
                    + "\n".join(f"· {lang}" for lang in sorted(card.languages))
                )
                return
            if field in ("装备", "equipment", "eq"):
                eq = card.equipment
                if not (eq.main_hand or eq.off_hand or eq.armor):
                    yield event.plain_result(f"「{card.name}」没有装备任何物品。")
                    return
                lines = [f"📜 {card.name} · 装备"]
                if eq.main_hand:
                    lines.append(f"· 主手 {eq.main_hand}")
                if eq.off_hand:
                    lines.append(f"· 副手 {eq.off_hand}")
                if eq.armor:
                    lines.append(f"· 护甲 {eq.armor}")
                yield event.plain_result("\n".join(lines))
                return
            if field in ("法术", "spells", "spell", "已知法术"):
                if not card.spells:
                    yield event.plain_result(f"「{card.name}」没有登记已知法术。")
                    return
                lines = [f"📜 {card.name} · 已知法术"]
                for ring, names in card.spells.items():
                    label = "戏法" if ring == "戏法" else f"{ring}环"
                    lines.append(f"· {label}（{len(names)} 个）：" + "、".join(names))
                yield event.plain_result("\n".join(lines))
                return
            yield event.plain_result(
                f"未知字段「{rest[0]}」。可查看：生平/人物信息/专长/特性（种族+职业）/攻击/熟练/语言/装备/法术/掷骰。"
            )
            return

        # --- 升级（v0.18：指定职业 +1 级，整卡重算 base） ---
        if sub in ("levelup", "升级", "升"):
            target = " ".join(rest) if rest else ""
            card, report, err = await cm.level_up(
                event, None, target, recalc_fn=self._recalc_card
            )
            if err is not None:
                yield event.plain_result(err)
                return
            lines = [f"「{card.name}」已升级！"]
            if report is not None and report.text:
                lines.append("自动重算：" + report.text)
            lines.append(cm.format_sheet(card, self.kb_manager))
            yield event.plain_result("\n".join(lines))
            return

        # --- 降级（v0.24.0：指定职业 -1 级，整卡重算 base） ---
        if sub in ("leveldown", "降级", "降", "down"):
            target = " ".join(rest) if rest else ""
            card, report, err = await cm.level_down(
                event, None, target, recalc_fn=self._recalc_card
            )
            if err is not None:
                yield event.plain_result(err)
                return
            lines = [f"「{card.name}」已降级！"]
            if report is not None and report.text:
                lines.append("自动重算：" + report.text)
            lines.append(cm.format_sheet(card, self.kb_manager))
            yield event.plain_result("\n".join(lines))
            return

        # --- 熟练：技能 / 豁免 / 工具 / 武器 / 防具（+加 -减，整体覆盖式更新） ---
        if sub in ("prof", "熟练", "proficiency"):
            prof_kind_map = {
                "技能": ("skills", "skill_proficiencies", True),
                "skill": ("skills", "skill_proficiencies", True),
                "豁免": ("saves", "save_proficiencies", True),
                "save": ("saves", "save_proficiencies", True),
                "saves": ("saves", "save_proficiencies", True),
                "工具": ("tools", "tool_proficiencies", False),
                "tool": ("tools", "tool_proficiencies", False),
                "武器": ("weapons", "weapon_proficiencies", False),
                "weapon": ("weapons", "weapon_proficiencies", False),
                "防具": ("armors", "armor_proficiencies", False),
                "armor": ("armors", "armor_proficiencies", False),
            }
            if len(rest) < 2 or rest[0].lower() not in prof_kind_map:
                yield event.plain_result(
                    f"用法：{display_prefix}卡 熟练 技能 +察觉 -隐匿 ｜ "
                    f"{display_prefix}卡 熟练 豁免 +力 -敏 ｜ "
                    f"{display_prefix}卡 熟练 工具|武器|防具 +名 -名\n"
                    "武器类别可填「简易武器」「军用武器」或具体武器名。"
                )
                return
            prof_key, prof_attr, use_alias = prof_kind_map[rest[0].lower()]
            mods = rest[1:]
            card = await cm.get_card(event)
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            current = set(getattr(card, prof_attr))
            unknown: list[str] = []
            for tok in mods:
                add = tok.startswith("+")
                remove = tok.startswith("-")
                if not add and not remove:
                    unknown.append(tok)
                    continue
                item = tok[1:].strip()
                if use_alias:
                    canon = (SKILL_ALIAS if prof_key == "skills" else ABILITY_ALIAS).get(
                        item.lower()
                    )
                    if canon is None:
                        unknown.append(tok)
                        continue
                else:
                    canon = item
                    if not canon:
                        unknown.append(tok)
                        continue
                if add:
                    current.add(canon)
                else:
                    current.discard(canon)
            card, applied = await cm.update_fields(
                event, None, {prof_key: ",".join(sorted(current))}
            )
            msg = f"已更新{rest[0]}熟练。"
            if unknown:
                msg += f" 无法识别：{'、'.join(unknown)}"
            # 武器熟练影响攻击加值 → 变更后重算 base
            if card is not None and prof_key == "weapons":
                report = self._recalc_card(card)
                if report is not None:
                    err = await cm.save_card(event, card)
                    if report.text:
                        msg += "\n自动重算：" + report.text
                    if err is not None:
                        msg += f"\n自动重算后保存失败：{err}"
            if card is not None:
                msg += "\n" + cm.format_sheet(card, self.kb_manager)
            yield event.plain_result(msg)
            return

        # --- 技能专精（v0.21：双倍熟练） ---
        if sub in ("expertise", "专精", "exp"):
            if len(rest) < 1:
                yield event.plain_result(
                    f"用法：{display_prefix}卡 专精 +察觉 -隐匿\n"
                    "专精使该技能检定获得双倍熟练加值（规则上通常先需熟练）。"
                )
                return
            card = await cm.get_card(event)
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            current = set(card.skill_expertise)
            unknown: list[str] = []
            not_proficient: list[str] = []
            for tok in rest:
                add = tok.startswith("+")
                remove = tok.startswith("-")
                if not add and not remove:
                    unknown.append(tok)
                    continue
                item = tok[1:].strip()
                canon = SKILL_ALIAS.get(item.lower())
                if canon is None:
                    unknown.append(tok)
                    continue
                if add:
                    current.add(canon)
                    if canon not in card.skill_proficiencies:
                        not_proficient.append(SKILL_CN_REV.get(canon, canon))
                else:
                    current.discard(canon)
            card, applied = await cm.update_fields(
                event, None, {"expertise": ",".join(sorted(current))}
            )
            msg = "已更新技能专精。"
            if not_proficient:
                msg += f" 注意：{'、'.join(not_proficient)}尚未熟练"
            if unknown:
                msg += f" 无法识别：{'、'.join(unknown)}"
            if card is not None:
                msg += "\n" + cm.format_sheet(card, self.kb_manager)
            yield event.plain_result(msg)
            return

        # --- 专长（v0.21：手动维护列表，知识库存在性提示不阻断） ---
        if sub in ("feat", "feats", "专长"):
            if len(rest) < 1:
                yield event.plain_result(
                    f"用法：{display_prefix}卡 专长 +巨武器大师 -幸运\n"
                    f"（整体替换：{display_prefix}卡 设 专长 <完整列表>，如「巨武器大师,幸运」）"
                )
                return
            card = await cm.get_card(event)
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            current = list(card.feats)
            unknown: list[str] = []
            not_found: list[str] = []
            for tok in rest:
                add = tok.startswith("+")
                remove = tok.startswith("-")
                if not add and not remove:
                    unknown.append(tok)
                    continue
                item = tok[1:].strip()
                if not item:
                    unknown.append(tok)
                    continue
                if add:
                    if item not in current:
                        current.append(item)
                    try:
                        if self.kb_manager.available and not self.kb_manager.search(
                            item, kind="feat"
                        ):
                            not_found.append(item)
                    except Exception:  # noqa: BLE001 — kb 不可用不阻断
                        pass
                else:
                    current = [f for f in current if f != item]
            card, applied = await cm.update_fields(
                event, None, {"feats": ",".join(current)}
            )
            msg = "已更新专长。"
            if not_found:
                msg += f" 知识库未收录（已保存）：{'、'.join(not_found)}"
            if unknown:
                msg += f" 无法识别：{'、'.join(unknown)}"
            if card is not None:
                msg += "\n" + cm.format_sheet(card, self.kb_manager)
            yield event.plain_result(msg)
            return

        # --- 命名掷骰（v0.32.0：增改「名称 表达式」、删「名称 -」；/r 联动掷） ---
        if sub in ("roll", "named", "骰", "掷骰"):
            if len(rest) < 1:
                yield event.plain_result(
                    f"用法：{display_prefix}卡 骰 <名称> <表达式> ｜ "
                    f"{display_prefix}卡 骰 <名称> -（删除）\n"
                    f"登记后 {display_prefix}r <名称> 直接用该表达式掷骰。"
                )
                return
            rname = rest[0]
            rexpr = " ".join(rest[1:])
            deleting = rexpr.strip() in ("-", "－", "删", "删除")
            card, applied = await cm.update_fields(
                event, None, {"named_roll": f"{rname}={rexpr or '-'}"}
            )
            if card is None:
                yield event.plain_result("还没有角色卡，请先 /车卡 创建。")
                return
            if deleting:
                if "named_roll" not in applied:
                    yield event.plain_result(
                        f"没有名为「{rname}」的命名掷骰（/卡 详情 掷骰 可查看全部）。"
                    )
                    return
                msg = f"已删除命名掷骰「{rname}」。"
            else:
                msg = f"已记录命名掷骰「{rname}」→ {rexpr}。"
            yield event.plain_result(
                msg + "\n" + cm.format_sheet(card, self.kb_manager)
            )
            return

        # --- 导入文本角色卡（v0.19：LLM 生成的 txt 卡直接落库） ---
        if sub in ("import", "导入"):
            parts = arg.split(None, 1)
            body = parts[1].strip() if len(parts) > 1 else ""
            if not body:
                yield event.plain_result(
                    f"用法：{display_prefix}卡 导入 <角色卡文本>\n"
                    "把角色卡文本贴在命令后（多行可直接换行粘贴），"
                    "战斗字段会自动重算。"
                )
                return
            async for msg in self._import_card_text(event, body, display_prefix):
                yield msg
            return

        # --- 未知子命令 ---
        yield event.plain_result(
            f"角色卡用法：\n"
            f"  {display_prefix}卡              查看活跃角色卡\n"
            f"  {display_prefix}卡 列表         列出全部角色卡\n"
            f"  {display_prefix}卡 用 <卡名>    切换活跃卡\n"
            f"  {display_prefix}卡 删 <卡名>    删除角色卡\n"
            f"  {display_prefix}卡 改名 <旧> <新>  重命名\n"
            f"  {display_prefix}卡 设 <字段> <值>  设置 hp/ac/速度/法术位N/攻击/主手/副手/护甲/背景/生平/背景故事/阵营/语言\n"
            f"  {display_prefix}卡 详情 [字段]  查看完整字段：生平/专长/职业特性/攻击/熟练/语言/装备\n"
            f"  {display_prefix}卡 熟练 技能|豁免|工具|武器|防具 +名 -名\n"
            f"  {display_prefix}卡 专精 +察觉 -隐匿   技能双倍熟练\n"
            f"  {display_prefix}卡 专长 +巨武器大师 -幸运  维护专长列表\n"
            f"  {display_prefix}卡 骰 <名称> <表达式>\n"
            f"  {display_prefix}卡 导入 <卡文本>  把文本角色卡直接落库\n"
            f"引导创建：{display_prefix}车卡"
        )

    # ------------------------------------------------------------------
    # /车卡 指令：LLM 引导（命令层为无 LLM 时的兜底入口）
    # ------------------------------------------------------------------

    @filter.command("车卡", alias={"chargen"})
    async def chargen_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        车卡引导（混合式：LLM 经工具驱动为主，本命令为兜底入口）。

        用法:
          /车卡                开始引导；已有草稿则查看进度
          /车卡 状态           查看当前进度与要回答的问题
          /车卡 答 <答案>      提交当前步骤的答案
          /车卡 导入 <卡文本>  把已有的文本角色卡直接解析落库（战斗字段自动重算）
          /车卡 取消           中止引导并丢弃草稿
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_chargen(event, arg, display_prefix="/"):
            yield msg

    async def _handle_chargen(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """/车卡 命令核心逻辑。"""
        tokens = _tokenize(arg)
        if tokens is None:
            yield event.plain_result("引号未配对。")
            return
        sub = tokens[0].lower() if tokens else ""
        rest = tokens[1:]
        cg = self.chargen_manager

        # --- 开始 / 继续 ---
        if not tokens or sub in ("start", "开始", "继续", "go"):
            draft = await cg.get_draft(event)
            reply = await (cg.status(event) if draft is not None else cg.start(event))
            yield event.plain_result(reply.format())
            return

        # --- 状态 ---
        if sub in ("status", "状态", "进度"):
            reply = await cg.status(event)
            yield event.plain_result(reply.format())
            return

        # --- 取消 ---
        if sub in ("cancel", "取消", "abort"):
            reply = await cg.cancel(event)
            yield event.plain_result(reply.format())
            return

        # --- 导入文本角色卡（v0.19：LLM 生成的 txt 卡直接落库） ---
        if sub in ("import", "导入"):
            parts = arg.split(None, 1)
            body = parts[1].strip() if len(parts) > 1 else ""
            if not body:
                yield event.plain_result(
                    f"用法：{display_prefix}车卡 导入 <角色卡文本>\n"
                    "把角色卡文本贴在命令后（多行可直接换行粘贴），"
                    "战斗字段会自动重算。"
                )
                return
            async for msg in self._import_card_text(event, body, display_prefix):
                yield msg
            return

        # --- 提交答案（无 LLM 时的兜底路径） ---
        if sub in ("answer", "答", "回复", "提交"):
            answer = " ".join(rest)
            if not answer:
                yield event.plain_result(f"用法：{display_prefix}车卡 答 <当前问题的答案>")
                return
            reply = await cg.advance(event, answer)
            await self._log_chargen_rolls(event, reply)
            text = reply.format()
            if reply.done:
                card = await self.character_manager.get_card(event)
                if card is not None:
                    text += "\n\n" + self.character_manager.format_sheet(card, self.kb_manager)
            yield event.plain_result(text)
            return

        # --- 直接提交（省略「答」字：/车卡 15 14 13 12 10 8） ---
        reply = await cg.advance(event, arg)
        await self._log_chargen_rolls(event, reply)
        text = reply.format()
        if reply.done:
            card = await self.character_manager.get_card(event)
            if card is not None:
                text += "\n\n" + self.character_manager.format_sheet(card, self.kb_manager)
        yield event.plain_result(text)

    async def _log_chargen_rolls(self, event: AstrMessageEvent, reply: StepReply) -> None:
        """代骰回复（含「已代骰」）写入投掷历史。"""
        if "已代骰" in reply.check and self.enable_history:
            await self._history.add(event, "开卡（掷骰法）", reply.check)

    async def _import_card_text(
        self, event: AstrMessageEvent, text: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """把玩家贴出的文本角色卡解析并落库（/卡 导入、/车卡 导入 共用）。

        宽松策略：解析成功即落库（「宁缺毋滥」）；战斗字段由规则引擎
        recalc_base 重算（失败仅提示不阻断）；同名卡沿用 save_card 覆盖
        语义；导入成功删除该玩家的车卡草稿（防残留）。
        """
        from .card_import import parse_card_text

        try:
            result = parse_card_text(text)
        except ValueError as e:
            yield event.plain_result(
                f"导入失败：{e}\n"
                f"把角色卡文本贴在命令后（多行可直接换行粘贴），支持 "
                f"{display_prefix}车卡 引导产出的文本或 名字/职业/属性 等 "
                "key:value 行。"
            )
            return
        sheet = result.sheet
        cm = self.character_manager
        notes = list(result.notes)

        # 战斗字段由规则引擎重算 base（复用 _recalc_card 容错）
        report = self._recalc_card(sheet)
        if report is not None and report.text:
            notes.append("自动重算：" + report.text)

        # 同名覆盖提示（save_card 沿用覆盖语义，索引/活跃指针不变）
        names = await cm.list_cards(event)
        overwritten = sheet.name in (names or [])
        err = await cm.save_card(event, sheet)
        if err is not None:
            yield event.plain_result(f"落库失败：{err}")
            return

        # 删除该玩家的车卡草稿（防残留；失败仅警告）
        try:
            draft = await self.chargen_manager.get_draft(event)
            if draft is not None:
                await self.chargen_manager.cancel(event)
                notes.append("已丢弃未完成的引导草稿。")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 导入后清理草稿失败: {e}")

        lines = [
            f"已导入角色卡「{sheet.name}」！" + ("（覆盖了同名旧卡）" if overwritten else "")
        ]
        for n in notes:
            lines.append("· " + n)
        lines.append(cm.format_sheet(sheet, self.kb_manager))
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------
    # /车卡规则 指令：群级开卡规则（DM 设置）
    # ------------------------------------------------------------------

    @filter.command("车卡规则", alias={"车规", "chargenrule"})
    async def chargen_rule_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        群级开卡规则设置（版本/属性生成方式/子职时机/起始等级/起始金币）。

        用法:
          /车卡规则                   查看当前群规则
          /车卡规则 版本 2014|2024    （也支持紧凑写法：版本2024）
          /车卡规则 属性 27buy|32buy|dnd5|标准数组
          /车卡规则 属性 购点 池=32 [下限=8] [上限=15]
          /车卡规则 属性 掷骰 4d6kh3 [6]
          /车卡规则 子职时机 开|关|按规则   （也支持紧凑写法：子职时机开）
          /车卡规则 起始等级 1-20    （也支持紧凑写法：起始等级3）
          /车卡规则 起始金币 <自动|金额|骰式>
          /车卡规则 重置
        写入需白名单/管理员权限（群聊）。
        """
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_chargen_rule(event, arg, display_prefix="/"):
            yield msg

    async def _handle_chargen_rule(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """/车卡规则 命令核心逻辑。"""
        cg = self.chargen_manager
        rule = await cg.get_rule(event)

        # 查询（任何人可查）
        if not arg.strip():
            yield event.plain_result(
                f"{rule.format()}\n"
                f"修改需管理权限。用法：{display_prefix}车卡规则 "
                "版本/属性/子职时机/起始等级/起始金币/重置"
            )
            return

        # 写入需权限（群聊白名单/管理员，私聊放行）
        if not await self._check_destructive_permission(event):
            yield event.plain_result(
                "你没有权限修改群开卡规则。"
                + (
                    "（白名单模式已启用，请联系管理员）"
                    if self.enable_whitelist
                    else ""
                )
            )
            return

        tokens = _tokenize(arg)
        if tokens is None:
            yield event.plain_result("引号未配对。")
            return
        new_rule, msg = parse_rule_edit(
            rule, tokens, validate_expr=self._validate_dice_expr
        )
        if new_rule is None:
            yield event.plain_result(msg)
            return
        await cg.set_rule(event, new_rule)
        yield event.plain_result("✅ 已更新群开卡规则。\n" + new_rule.format())

    # ------------------------------------------------------------------
    # /查X 指令：DND 知识库查询
    # ------------------------------------------------------------------

    def _kb_detail_text(self, kind: str, entries: list) -> str:
        """知识库详情文本：物品追加「价值/重量」行（v0.20.0，查询期格式化）。"""
        text = KnowledgeBaseManager.format_detail(entries)
        if kind == "item":
            extra = self.kb_manager.item_stats_lines(entries)
            if extra:
                text += "\n" + "\n".join(extra)
        return text

    async def _handle_kb_lookup(
        self,
        event: AstrMessageEvent,
        arg: str,
        kind: str,
        display_prefix: str = "/",
    ) -> AsyncGenerator:
        """知识库名称查询核心逻辑（查法术/查怪/查物品/查专长/查背景共用）。

        流程：精确名 → 直接返回全版本；无精确命中 → 模糊搜索：
        单一候选直接展示，多候选列出编号供进一步指名。
        """
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        arg = arg.strip()
        if not arg:
            yield event.plain_result(
                f"用法：{display_prefix}查{_KB_KIND_LABEL[kind]} <名称>\n"
                f"示例：{display_prefix}查{_KB_KIND_LABEL[kind]} 火球术"
            )
            return

        entries = self.kb_manager.detail(arg, kind=kind)
        if entries:
            yield event.plain_result(self._kb_detail_text(kind, entries))
            # 物品增强：以该基础物品为原型的魔法武器反查（/查物品 长剑 → +30 件变体）
            if kind == "item":
                base_hits = self.kb_manager.filter(
                    "item", tags=[("base_item", entries[0].name)], limit=10
                )
                if base_hits.entries:
                    lines = [
                        f"📎 以「{entries[0].name}」为基础的魔法物品"
                        f"（共 {base_hits.total} 件，仅显示前 {len(base_hits.entries)} 件）："
                    ]
                    for i, e in enumerate(base_hits.entries, 1):
                        meta = KnowledgeBaseManager._entry_meta(e)
                        flag = f" {MACHINE_FLAG}" if e.is_machine else ""
                        lines.append(
                            f"{i}. {e.name}{meta}{flag} —"
                            f" {KnowledgeBaseManager._summary_of(e.body)}"
                        )
                    lines.append("回复 /查物品 <名称> 查看详情。")
                    yield event.plain_result("\n".join(lines))
            return

        hits = self.kb_manager.search(arg, kind=kind)
        if len(hits) == 1:
            entries = self.kb_manager.detail(hits[0].name, kind=kind)
            yield event.plain_result(self._kb_detail_text(kind, entries))
            return
        if hits:
            yield event.plain_result(KnowledgeBaseManager.format_hits(hits))
            return
        yield event.plain_result(f"未找到「{arg}」相关条目。")

    async def _handle_kb_opt_lookup(
        self,
        event: AstrMessageEvent,
        arg: str,
        ftype: str,
        display_prefix: str = "/",
    ) -> AsyncGenerator:
        """可定制职业选项查询核心逻辑（/查祈唤 /查战技 /查修法 /查风格 共用，v0.50.0）。

        ftype：EI（魔能祈唤）/ MV（战技）/ MM（超魔法）/ FS（战斗风格）。
        精确名 → 返回该类型全版本；无命中 → 模糊搜索候选。
        """
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        arg = arg.strip()
        ft_cn = OPTIONAL_FEATURE_TYPE_CN.get(ftype, ftype)
        if not arg:
            yield event.plain_result(
                f"用法：{display_prefix}查{ft_cn} <名称>\n"
                f"示例：{display_prefix}查{ft_cn} 苦痛魔爆\n"
                f"      {display_prefix}筛选项 类型 {ft_cn}"
            )
            return
        entries = self.kb_manager.detail(arg, kind="optionalfeature")
        typed = [e for e in entries if (e.opt_type_label or "").startswith(ft_cn)]
        # v0.50.3：ftype 过滤为空时回退全部类型（禁令恩惠/血咒等新类型
        # 没有独立命令，/查祈唤 等也能查到并标注类型）。
        if typed:
            entries = typed
        if entries:
            yield event.plain_result(self._kb_detail_text("optionalfeature", entries))
            return
        hits = self.kb_manager.search(arg, kind="optionalfeature")
        if len(hits) == 1:
            entries = self.kb_manager.detail(hits[0].name, kind="optionalfeature")
            typed = [
                e for e in entries if (e.opt_type_label or "").startswith(ft_cn)
            ]
            if typed:
                entries = typed
            if entries:
                yield event.plain_result(
                    self._kb_detail_text("optionalfeature", entries)
                )
                return
        if hits:
            yield event.plain_result(KnowledgeBaseManager.format_hits(hits))
            return
        yield event.plain_result(f"未找到{ft_cn}「{arg}」。")

    async def _handle_kb_opt_filter(
        self,
        event: AstrMessageEvent,
        arg: str,
        display_prefix: str = "/",
    ) -> AsyncGenerator:
        """职业选项反查（/筛选项，v0.50.0）。

        语法：/筛选项 <类型|先决> <词>；无修饰词时整词先按类型匹配再按先决。
        例：/筛选项 祈唤、/筛选项 类型 战技、/筛选项 先决 第5级。
        """
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        arg = arg.strip()
        if not arg:
            yield event.plain_result(
                f"用法：{display_prefix}筛选项 <类型|先决> <词>\n"
                f"示例：{display_prefix}筛选项 类型 祈唤\n"
                f"      {display_prefix}筛选项 先决 第5级\n"
                f"      {display_prefix}筛选项 战技"
            )
            return
        parts = arg.split(None, 1)
        head = parts[0].strip()
        rest = parts[1].strip() if len(parts) > 1 else ""
        tags: list[tuple[str, str]] = []
        ft_cn_to_code = {v: k for k, v in OPTIONAL_FEATURE_TYPE_CN.items()}

        def _resolve_opt_type(s: str) -> str:
            """选项类型中文解析：完整中文名 / 简称（祈唤→魔能祈唤）→ canonical。"""
            s = (s or "").strip()
            if s in ft_cn_to_code:
                return s
            for cn in OPTIONAL_FEATURE_TYPE_CN.values():
                if s and (s in cn or cn.startswith(s)):
                    return cn
            return s

        if head in ("先决", "prereq", "先决条件", "前置"):
            tags.append(("prerequisite", f"%{rest}%"))
        elif head in ("类型", "type"):
            tags.append(("feature_type", _resolve_opt_type(rest)))
        else:
            # 无修饰词：先按类型（祈唤/战技…）匹配，再按先决模糊
            resolved = _resolve_opt_type(head)
            if resolved in ft_cn_to_code:
                tags.append(("feature_type", resolved))
            else:
                tags.append(("prerequisite", f"%{head}%"))
        result = self.kb_manager.filter("optionalfeature", tags=tags, limit=20)
        lines = [
            f"🔎 职业选项反查「{' '.join(f'{f}:{v}' for f, v in tags)}」："
            f"共 {result.total} 条，显示前 {len(result.entries)} 条"
        ]
        for i, e in enumerate(result.entries, 1):
            meta = KnowledgeBaseManager._entry_meta(e)
            lines.append(f"{i}. 【{e.opt_type_label or '选项'}】{e.name}{meta}")
        lines.append("回复 /查祈唤|战技|修法|风格 <名称> 查看详情。")
        yield event.plain_result("\n".join(lines))

    async def _handle_kb_class(
        self,
        event: AstrMessageEvent,
        arg: str,
        display_prefix: str = "/",
    ) -> AsyncGenerator:
        """职业查询核心逻辑：查职业 <职业名> [子职名|版本|等级段|特性 [特性名]]。

        第二参数优先级解析链（v0.48.0，ADR-0023）：
          - 「特性/详情/detail…」→ 本职特性细化：全量按层级段分条，单特性跨版本；
          - 「2014/2024」→ 版本覆盖（其余模式仍按群规则/最新版）；
          - 「第N层」/「N级」/「N-M级」→ 等级段钻取（全文）；
          - 其他 → 子职名精确匹配（显示名或短名均可）。
        默认版本 = 群级开卡规则（chargen_rule 的 edition）→ 私聊/无规则取最新版。
        """
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        arg = arg.strip()
        if not arg:
            yield event.plain_result(
                f"用法：{display_prefix}查职业 <职业名> [子职名|版本|等级段]\n"
                f"示例：{display_prefix}查职业 战士\n"
                f"      {display_prefix}查职业 战士 勇士\n"
                f"      {display_prefix}查职业 战士 2024\n"
                f"      {display_prefix}查职业 战士 第2层\n"
                f"      {display_prefix}查职业 战士 5级\n"
                f"      {display_prefix}查职业 战士 特性  （本职特性完整说明，按层级分条）"
            )
            return
        parts = arg.split(None, 2)
        cls_name = parts[0]
        second = parts[1].strip() if len(parts) > 1 else ""
        third = parts[2].strip() if len(parts) > 2 else ""

        # ---- 第二参数解析（优先级链）----
        feature: str | None = None
        edition: str | None = None
        lo = hi = 0  # 等级段钻取区间（0=不限）
        subclass: str | None = None
        if second in ("特性", "详情", "详细", "detail", "feature", "features"):
            feature = third if third else "*"
        elif second in ("2014", "2024"):
            edition = second
        elif (m := re.fullmatch(r"第([1-4])层", second)):
            lo, hi, _label = CLASS_TIERS[int(m.group(1)) - 1]
        elif (m := re.fullmatch(r"(\d{1,2})级", second)):
            lo = hi = int(m.group(1))
        elif (m := re.fullmatch(r"(\d{1,2})\s*[-~—～]\s*(\d{1,2})级", second)):
            lo, hi = int(m.group(1)), int(m.group(2))
        elif second:
            subclass = second

        # 先做一次无参查询：校验职业存在 + 拿 editions / 子职候选
        base = self.kb_manager.class_features(cls_name)
        if not base.base_rows and not base.subclass_candidates:
            hits = self.kb_manager.search(cls_name, kind="class")
            if len(hits) == 1:
                cls_name = hits[0].name
                base = self.kb_manager.class_features(cls_name)
            elif hits:
                yield event.plain_result(
                    KnowledgeBaseManager.format_hits(hits)
                )
                return
            else:
                yield event.plain_result(f"未找到职业「{cls_name}」。")
                return

        # 默认版本：群规则感知 → 无规则/私聊取最新版（显式覆盖优先）
        if edition is None:
            edition = await self._resolve_class_edition(event, base)

        # 特性细化：全量按层级段分条；单特性跨版本（不按版本过滤）
        if feature is not None:
            f_edition = None if (feature and feature != "*") else edition
            result = self.kb_manager.class_features(
                cls_name, feature=feature, edition=f_edition
            )
            for msg in KnowledgeBaseManager.class_display_messages(
                result, full=True
            ):
                yield event.plain_result(msg)
            return

        # 子职：一次给齐（应用版本策略）
        if subclass:
            result = self.kb_manager.class_features(
                cls_name, subclass=subclass, edition=edition
            )
            if not result.subclass_rows:
                msg = f"未找到「{cls_name}」的子职「{subclass}」。"
                if base.subclass_candidates:
                    msg += "\n可选子职：" + "、".join(base.subclass_candidates)
                yield event.plain_result(msg)
                return
            yield event.plain_result(
                KnowledgeBaseManager.format_class_features(result)
            )
            return

        # 等级段/层级钻取：全文，按层级分条
        if lo or hi:
            result = self.kb_manager.class_features(
                cls_name, level_min=lo, level_max=hi, edition=edition
            )
            if not result.base_rows:
                yield event.plain_result(
                    f"「{cls_name}」在 {edition} 版没有「{second}」相关的特性数据。"
                )
                return
            for msg in KnowledgeBaseManager.class_display_messages(
                result, full=True
            ):
                yield event.plain_result(msg)
            return

        # 默认概要总表（分层 + 一句话概要 + 子职候选 + 提示）
        result = self.kb_manager.class_features(cls_name, edition=edition)
        # 回退判断只看本职特性（子职候选查询不带 edition 过滤，不能作为版本有无依据）
        if not result.base_rows:
            alt = [e for e in base.editions if e != edition]
            if alt:
                yield event.plain_result(
                    f"「{cls_name}」在 {edition} 版无特性数据，已改展示 {alt[0]} 版。"
                )
                result = self.kb_manager.class_features(
                    cls_name, edition=alt[0]
                )
                if not result.base_rows:
                    yield event.plain_result("（未找到该职业的特性数据）")
                    return
            else:
                yield event.plain_result("（未找到该职业的特性数据）")
                return
        for msg in KnowledgeBaseManager.class_display_messages(result):
            yield event.plain_result(msg)

    async def _resolve_class_edition(
        self,
        event: AstrMessageEvent,
        result,
    ) -> str:
        """职业查询默认版本（v0.48.0）：群级开卡规则优先，否则最新版。"""
        try:
            rule_ed = await self.chargen_manager.get_rule_edition(event)
            if rule_ed in ("2014", "2024"):
                return rule_ed
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[trpg_assistant] 读取群规则版本失败: {e}")
        editions = result.editions or []
        return editions[0] if editions else "2014"

    async def _handle_kb(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """/kb 命令核心逻辑：查询知识库版本信息。"""
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        sub = arg.strip().split(None, 1)[0].lower() if arg.strip() else ""
        if sub in ("version", "ver", "版本", "v"):
            yield event.plain_result(
                KnowledgeBaseManager.format_version(self.kb_manager.version())
            )
            return
        if sub in ("update", "更新", "升级", "rollback", "回滚"):
            yield event.plain_result(
                "在线更新尚未开放：当前通过安装新版插件 zip 更新知识库。"
            )
            return
        if sub in ("reload", "重载", "重读", "私设重载"):
            result = self.kb_manager.reload_homebrew()
            lines = ["✅ 私设已重载。"]
            if result.files or result.entries:
                lines.append(
                    f"扫描 {result.files} 个文件，加载 {result.entries} 条私设"
                    f"（覆盖官方 {result.overrides} 条）。"
                )
            else:
                lines.append(
                    "未找到私设文件：请把 *.json 放入 AstrBot 数据目录的"
                    " trpg_homebrew/ 文件夹后重试。"
                )
            for warn in result.warnings[:5]:
                lines.append(f"⚠️ {warn}")
            for err in result.errors[:5]:
                lines.append(f"❌ {err}")
            if len(result.errors) > 5:
                lines.append(f"…另有 {len(result.errors) - 5} 个文件错误（见日志）。")
            yield event.plain_result("\n".join(lines))
            return
        if sub in ("私设", "homebrew", "hb"):
            stats = self.kb_manager.homebrew_stats()
            lines = ["🏠 当前私设（房规）概况："]
            if stats.entries:
                lines.append(
                    f"已加载 {stats.entries} 条私设，来自 {stats.files} 个文件，"
                    f"其中覆盖官方 {stats.overrides} 条。"
                )
                lines.append(
                    f"目录：{self.kb_manager._homebrew().directory}"  # noqa: SLF001
                    "（新增/修改文件后执行 /kb reload）"
                )
            else:
                lines.append("暂无私设。把 *.json 放入 AstrBot 数据目录的 "
                             "trpg_homebrew/ 后执行 /kb reload。")
            yield event.plain_result("\n".join(lines))
            return
        yield event.plain_result(
            f"用法：{display_prefix}kb version  查看知识库版本信息\n"
            f"{display_prefix}kb reload   重载私设（房规）目录\n"
            f"{display_prefix}kb 私设     查看当前私设概况"
        )

    async def _handle_kb_search(
        self, event: AstrMessageEvent, arg: str, display_prefix: str = "/"
    ) -> AsyncGenerator:
        """/查询 核心逻辑：跨全部条目类别按名称广搜（-全文 可搜正文）。"""
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        arg = arg.strip()
        fulltext = False
        for flag in ("-全文", "-ft", "-fulltext"):
            if arg.startswith(flag):
                fulltext = True
                arg = arg[len(flag):].strip()
                break
        if not arg:
            yield event.plain_result(
                f"用法：{display_prefix}查询 <关键词> [-全文]\n"
                f"跨法术/怪物/物品/专长/背景/职业搜索名称；"
                f"加 -全文 额外搜索条目正文。\n"
                f"示例：{display_prefix}查询 火焰、{display_prefix}查询 -全文 火焰伤害"
            )
            return
        hits = self.kb_manager.search(arg, fulltext=fulltext, limit=20)
        if hits:
            yield event.plain_result(
                KnowledgeBaseManager.format_hits_grouped(hits, query=arg, limit=20)
            )
            return
        # 名称无命中兜底：法术学派词（/查询 惑控 → 列出惑控学派法术）
        school_code = resolve_school(arg)
        if school_code:
            result = self.kb_manager.filter("spell", school=school_code)
            yield event.plain_result(
                KnowledgeBaseManager.format_filter_result(
                    result, "法术", []
                )
            )
            return
        yield event.plain_result(f"未找到与「{arg}」相关的条目。")

    async def _handle_kb_filter(
        self,
        event: AstrMessageEvent,
        arg: str,
        kind: str,
        display_prefix: str = "/",
    ) -> AsyncGenerator:
        """/筛X 核心逻辑：特性反查（伤害/状态/环境/属性/形状/距离…多条件 AND）。"""
        if not self.kb_manager.available:
            yield event.plain_result("知识库不可用（数据文件缺失，请重装插件）。")
            return
        arg = arg.strip()
        if not arg or arg in ("help", "帮助"):
            yield event.plain_result(_FILTER_HELP[kind].format(p=display_prefix))
            return
        tokens = arg.split()
        cond, unknown = _parse_filter_tokens(
            tokens, kind,
            feat_free_lookup=(
                self.kb_manager.resolve_feat_free_term if kind == "feat" else None
            ),
            spell_free_lookup=(
                self.kb_manager.resolve_spell_free_term if kind == "spell" else None
            ),
            class_free_lookup=(
                self.kb_manager.resolve_class_free_term if kind == "class" else None
            ),
            subclass_free_lookup=(
                self.kb_manager.resolve_subclass_free_term
                if kind == "subclass" else None
            ),
            race_free_lookup=(
                self.kb_manager.resolve_race_free_term if kind == "race" else None
            ),
            background_free_lookup=(
                self.kb_manager.resolve_background_free_term
                if kind == "background" else None
            ),
            monster_free_lookup=(
                self.kb_manager.resolve_monster_free_term
                if kind == "monster" else None
            ),
        )
        # 物品兜底：未识别 token 若精确命中物品条目名，当作「基础物品」条件
        # （/筛物品 长剑 → 列出以长剑为基础的魔法武器）。
        if kind == "item" and unknown:
            kept_unknown: list[str] = []
            for tok in unknown:
                entries = self.kb_manager.detail(tok, kind="item")
                if entries:
                    cond["tags"].append(("base_item", entries[0].name))
                else:
                    kept_unknown.append(tok)
            unknown = kept_unknown
        # 种族兜底：未识别 token 若精确命中法术名，当作「天生施法」条件
        # （/筛种族 迷踪步 → 天生能施展迷踪步的种族）。
        if kind == "race" and unknown:
            kept_unknown = []
            for tok in unknown:
                entries = self.kb_manager.detail(tok, kind="spell")
                if entries:
                    cond["tags"].append(("innate_spell", entries[0].name))
                else:
                    kept_unknown.append(tok)
            unknown = kept_unknown
        # 法术「职业」前缀词（v0.35.0）：职业名解析为库内中文名；
        # 解析失败（如英文名不存在）按未知条件提示。
        if kind == "spell" and cond.get("spell_class"):
            cls_cn = self._resolve_class_cn(cond["spell_class"])
            if cls_cn:
                cond["spell_class"] = cls_cn
            else:
                unknown.append(f"职业 {cond['spell_class']}")
                cond["spell_class"] = None
        if len(unknown) == len(tokens) and not cond["tags"]:
            yield event.plain_result(
                f"没有识别出任何筛选条件。\n{_FILTER_HELP[kind].format(p=display_prefix)}"
            )
            return
        result = self.kb_manager.filter(
            kind=kind,
            level=cond["level"],
            school=cond["school"],
            cr_min=cond["cr_min"],
            cr_max=cond["cr_max"],
            mtype=cond["mtype"],
            rarity=cond["rarity"],
            concentration=cond["concentration"],
            attunement=cond["attunement"],
            range_type=cond["range_type"],
            range_max=cond["range_max"],
            range_min=cond["range_min"],
            speed_type=cond["speed_type"],
            speed_min=cond["speed_min"],
            speed_max=cond["speed_max"],
            darkvision_min=cond["darkvision_min"],
            tags=cond["tags"] or None,
            spell_class=cond.get("spell_class") or None,
        )
        yield event.plain_result(
            KnowledgeBaseManager.format_filter_result(
                result, _FILTER_KIND_LABEL[kind], unknown
            )
        )

    @filter.command("查法术", alias={"spell"})
    async def kb_spell_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询法术：/查法术 <名称>，返回同名全部版本的效果。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "spell", "/"):
            yield msg

    @filter.command("查怪", alias={"monster", "怪物"})
    async def kb_monster_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询怪物：/查怪 <名称>。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "monster", "/"):
            yield msg

    @filter.command("查物品", alias={"item", "物品"})
    async def kb_item_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询魔法物品：/查物品 <名称>。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "item", "/"):
            yield msg

    @filter.command("查专长", alias={"feat", "专长"})
    async def kb_feat_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询专长：/查专长 <名称>。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "feat", "/"):
            yield msg

    @filter.command("查背景", alias={"background", "背景"})
    async def kb_background_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询背景：/查背景 <名称>。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "background", "/"):
            yield msg

    @filter.command("查状态", alias={"condition", "状态"})
    async def kb_condition_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询状态：/查状态 <名称>（2014/2024 双版本）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "condition", "/"):
            yield msg

    @filter.command("查种族", alias={"race", "种族"})
    async def kb_race_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询种族：/查种族 <名称>（体型/速度/黑暗视觉/抗性/天生施法）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_lookup(event, arg, "race", "/"):
            yield msg

    @filter.command("查职业", alias={"class", "职业"})
    async def kb_class_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询职业与子职：/查职业 <职业名> [子职名]。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_class(event, arg, "/"):
            yield msg

    # ---- v0.50.0：可定制职业选项（魔能祈唤/战技/超魔法/战斗风格）----
    @filter.command("查祈唤", alias={"invocation", "祈唤"})
    async def kb_invocation_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询魔能祈唤：/查祈唤 <名称>（2014/2024 多版本）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_opt_lookup(event, arg, "EI", "/"):
            yield msg

    @filter.command("查战技", alias={"maneuver", "战技"})
    async def kb_maneuver_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询战技：/查战技 <名称>（战斗大师/战技专家可选）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_opt_lookup(event, arg, "MV", "/"):
            yield msg

    @filter.command("查修法", alias={"metamagic", "超魔法", "修法"})
    async def kb_metamagic_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询超魔法：/查修法 <名称>（术士法术修法选项）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_opt_lookup(event, arg, "MM", "/"):
            yield msg

    @filter.command("查风格", alias={"style", "战斗风格", "风格"})
    async def kb_fighting_style_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """查询战斗风格：/查风格 <名称>（2024 战斗风格专长）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_opt_lookup(event, arg, "FS", "/"):
            yield msg

    @filter.command("筛选项", alias={"ofilter", "选项"})
    async def kb_filter_opt_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """反查职业选项：/筛选项 类型 祈唤（类型/先决/来源）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_opt_filter(event, arg, "/"):
            yield msg

    @filter.command("kb")
    async def kb_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """知识库信息：/kb version 查看版本与来源。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb(event, arg, "/"):
            yield msg

    @filter.command("查询", alias={"search", "搜", "q"})
    async def kb_search_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """跨库广搜：/查询 <关键词>，加 -全文 可搜条目正文。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_search(event, arg, "/"):
            yield msg

    @filter.command("筛怪", alias={"mfilter", "筛怪物"})
    async def kb_filter_monster_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查怪物：/筛怪 火焰 CR5以下（伤害/状态/速度/感官/阵营/特性/CR）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "monster", "/"):
            yield msg

    @filter.command("筛法术", alias={"sfilter", "筛魔法"})
    async def kb_filter_spell_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查法术：/筛法术 专注 3环 火焰（伤害/状态/成分/距离/形状/目标）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "spell", "/"):
            yield msg

    @filter.command("筛物品", alias={"ifilter", "筛道具"})
    async def kb_filter_item_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查物品：/筛物品 暗蚀 灵巧（伤害/状态/武器属性/稀有度/同调）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "item", "/"):
            yield msg

    @filter.command("筛种族", alias={"rfilter", "筛血统"})
    async def kb_filter_race_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查种族：/筛种族 飞行 60尺（抗性/体型/生物类型/速度/黑暗视觉/天生施法）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "race", "/"):
            yield msg

    @filter.command("筛专长", alias={"ffilter", "专长筛"})
    async def kb_filter_feat_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查专长：/筛专长 战斗风格（类型/属性提升/先决种族·属性·专长·特性）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "feat", "/"):
            yield msg

    @filter.command("筛职业", alias={"cfilter", "职业筛"})
    async def kb_filter_class_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查职业：/筛职业 武者（定位/能力标签/属性依赖）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "class", "/"):
            yield msg

    @filter.command("筛子职", alias={"sublass_filter", "子职筛"})
    async def kb_filter_subclass_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查子职：/筛子职 治疗 神圣（定位倾向/主题风味/特色机制）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "subclass", "/"):
            yield msg

    @filter.command("筛背景", alias={"bfilter", "背景筛"})
    async def kb_filter_background_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """特性反查背景：/筛背景 隐匿 盗贼工具（技能/身份/工具/起始专长标签）。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_kb_filter(event, arg, "background", "/"):
            yield msg

    async def _handle_help(
        self,
        event: AstrMessageEvent,
        arg: str,
        display_prefix: str = "/",
    ) -> AsyncGenerator:
        """/帮助 命令核心逻辑：概览或某组详细语法。"""
        topic_key = _HELP_TOPIC_ALIAS.get((arg or "").strip().lower())
        if not arg or not topic_key:
            yield event.plain_result(_format_help_overview(display_prefix))
            return
        yield event.plain_result(_format_help_topic(topic_key, display_prefix))

    @filter.command("帮助", alias={"menu", "菜单", "commands", "cmds"})
    async def help_cmd(self, event: AstrMessageEvent) -> AsyncGenerator:
        """指令大全：/帮助 查看全部命令，/帮助 <组名> 查看详细语法。"""
        raw_msg: str = event.message_str.strip()
        parts = raw_msg.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        async for msg in self._handle_help(event, arg, "/"):
            yield msg

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def custom_prefix_route(self, event: AstrMessageEvent) -> AsyncGenerator:
        """
        自定义触发前缀消息路由。

        读取会话或全局配置的自定义前缀，将 {prefix}r / {prefix}roll / {prefix}dset 等
        消息路由到对应的掷骰或骰面设置逻辑。
        """
        # 非文本或空消息无触发前缀可言，先过滤，避免对每条消息都查询缓存/KV。
        text = event.message_str.strip()
        if not text:
            return

        prefix = await self._get_effective_prefix(event)
        if not prefix:
            return
        # 当有效前缀等于 AstrBot 系统命令前缀（"/"）时，
        # @filter.command 装饰器已处理这些消息，此处不再路由，
        # 否则会触发重复响应。
        if prefix == "/":
            return

        text_lower = text.lower()
        p = prefix.lower()

        # --- 骰池指令匹配 ---
        for cmd_key in (f"{p}roll", f"{p}r"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg_part = text[len(cmd_key) :].strip()
                effective_sides = await self._get_effective_sides(event)
                expression_str = arg_part if arg_part else f"d{effective_sides}"
                # 角色卡联动：.r 力量 / .r 察觉 / .r 敏捷豁免
                hit = await self._try_character_roll(event, expression_str)
                if hit is not None:
                    output, hist_expr = hit
                    if hist_expr and not output.startswith(ROLL_ERROR_PREFIXES):
                        await self._history.add(event, hist_expr, output)
                    yield event.plain_result(output)
                    event.stop_event()
                    return
                output = self._do_roll(expression_str, default_sides=effective_sides)
                if not output.startswith(ROLL_ERROR_PREFIXES):
                    await self._history.add(event, expression_str, output)
                yield event.plain_result(output)
                event.stop_event()
                return

        # --- 属性生成指令匹配 ---
        for cmd_key in (f"{p}dnd",):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_dnd(event, arg, display_prefix=prefix):
                    yield msg
                event.stop_event()
                return

        # --- 骰面设置指令匹配 ---
        for cmd_key in (f"{p}dice_set", f"{p}dset"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_dset(event, arg, display_prefix=prefix):
                    yield msg
                event.stop_event()
                return
        # --- 历史记录指令匹配 ---
        for cmd_key in (f"{p}rhistory", f"{p}rh"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_rhistory(event, arg):
                    yield msg
                event.stop_event()
                return
        # --- 先攻指令匹配 ---
        # 注意：{p}ri 需先于 {p}r 判定（上面骰池指令块只匹配 .r/.roll 后接空白，
        # 不会吞掉 .ri/.ritual 之类，但显式排序更稳妥——实际由匹配顺序保证）。
        for cmd_key in (f"{p}initiative", f"{p}init", f"{p}ri"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                if cmd_key == f"{p}ri":
                    sender_name = str(event.get_sender_name()).strip() or "未知单位"
                    kind, number, name = _parse_ri_arg(arg, sender_name)
                    if kind == "invalid":
                        yield event.plain_result(
                            "用法：ri [+调整值] [名称] 或 ri <固定值> [名称]"
                        )
                        event.stop_event()
                        return
                    if kind == "roll":
                        die = self._roll_d20()
                        if die is None:
                            yield event.plain_result("先攻掷骰失败，请稍后再试。")
                            event.stop_event()
                            return
                        value = die + number
                        _, entry = await self._initiative.add(
                            event,
                            name=name,
                            value=value,
                            modifier=number,
                            user_id=str(event.get_sender_id()),
                            is_fixed=False,
                        )
                        yield event.plain_result(
                            f"{InitiativeManager.format_entry_confirmation(entry)}，已加入先攻列表。"
                        )
                    else:
                        _, entry = await self._initiative.add(
                            event,
                            name=name,
                            value=number,
                            user_id=str(event.get_sender_id()),
                            is_fixed=True,
                        )
                        yield event.plain_result(
                            f"{InitiativeManager.format_entry_confirmation(entry)}，已加入先攻列表。"
                        )
                else:
                    async for msg in self._handle_init(event, arg, display_prefix=prefix):
                        yield msg
                event.stop_event()
                return
        # --- 背包指令匹配 ---
        # 长 token 优先：inventory > 背包 > bag（互不为前缀，顺序仅为规范）。
        for cmd_key in (f"{p}inventory", f"{p}背包", f"{p}bag"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_bag(event, arg, display_prefix=prefix):
                    yield msg
                event.stop_event()
                return
        # --- 发放 / 收回指令匹配（长 token 优先，直接操作队伍背包） ---
        for cmd_key, act in ((f"{p}发放", "grant"), (f"{p}grant", "grant"),
                             (f"{p}收回", "revoke"), (f"{p}revoke", "revoke")):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_party_items(
                    event, arg, display_prefix=prefix, action=act
                ):
                    yield msg
                event.stop_event()
                return
        # --- 商店指令匹配 ---
        for cmd_key in (f"{p}商店", f"{p}shop", f"{p}店铺"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_shop(event, arg, display_prefix=prefix):
                    yield msg
                event.stop_event()
                return
        # --- 开卡规则指令匹配 ---
        # 注意：{p}车卡规则 必须先于 {p}车卡 判定（长 token 优先），
        # 否则「车卡规则 属性 …」会被车卡块吞掉。
        for cmd_key in (f"{p}车卡规则", f"{p}chargenrule", f"{p}车规"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_chargen_rule(
                    event, arg, display_prefix=prefix
                ):
                    yield msg
                event.stop_event()
                return
        # --- 车卡引导指令匹配 ---
        for cmd_key in (f"{p}车卡", f"{p}chargen"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_chargen(event, arg, display_prefix=prefix):
                    yield msg
                event.stop_event()
                return
        # --- 角色卡指令匹配 ---
        for cmd_key in (f"{p}卡", f"{p}char", f"{p}角色卡"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_character(
                    event, arg, display_prefix=prefix
                ):
                    yield msg
                event.stop_event()
                return
        # --- 知识库指令匹配 ---
        kb_lookup_cmds = {
            f"{p}查法术": "spell",
            f"{p}spell": "spell",
            f"{p}查怪": "monster",
            f"{p}monster": "monster",
            f"{p}怪物": "monster",
            f"{p}查物品": "item",
            f"{p}item": "item",
            f"{p}物品": "item",
            f"{p}查专长": "feat",
            f"{p}feat": "feat",
            f"{p}专长": "feat",
            f"{p}查背景": "background",
            f"{p}background": "background",
            f"{p}背景": "background",
            f"{p}查状态": "condition",
            f"{p}condition": "condition",
            f"{p}状态": "condition",
            f"{p}查种族": "race",
            f"{p}race": "race",
            f"{p}种族": "race",
        }
        for cmd_key, kkind in kb_lookup_cmds.items():
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_kb_lookup(
                    event, arg, kkind, display_prefix=prefix
                ):
                    yield msg
                event.stop_event()
                return
        # --- 广搜 / 特性筛选指令匹配 ---
        kb_extra_cmds = {
            f"{p}查询": "search",
            f"{p}search": "search",
            f"{p}搜": "search",
            f"{p}q": "search",
            f"{p}筛怪": "monster",
            f"{p}mfilter": "monster",
            f"{p}筛怪物": "monster",
            f"{p}筛法术": "spell",
            f"{p}sfilter": "spell",
            f"{p}筛魔法": "spell",
            f"{p}筛物品": "item",
            f"{p}ifilter": "item",
            f"{p}筛道具": "item",
            f"{p}筛种族": "race",
            f"{p}rfilter": "race",
            f"{p}筛血统": "race",
            f"{p}筛专长": "feat",
            f"{p}ffilter": "feat",
            f"{p}专长筛": "feat",
            f"{p}筛职业": "class",
            f"{p}cfilter": "class",
            f"{p}职业筛": "class",
            f"{p}筛子职": "subclass",
            f"{p}sublass_filter": "subclass",
            f"{p}子职筛": "subclass",
            f"{p}筛背景": "background",
            f"{p}bfilter": "background",
            f"{p}背景筛": "background",
        }
        for cmd_key, kkind in kb_extra_cmds.items():
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                if kkind == "search":
                    async for msg in self._handle_kb_search(
                        event, arg, display_prefix=prefix
                    ):
                        yield msg
                else:
                    async for msg in self._handle_kb_filter(
                        event, arg, kkind, display_prefix=prefix
                    ):
                        yield msg
                event.stop_event()
                return
        for cmd_key in (f"{p}查职业", f"{p}class", f"{p}职业"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_kb_class(
                    event, arg, display_prefix=prefix
                ):
                    yield msg
                event.stop_event()
                return
        for cmd_key in (f"{p}kb",):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_kb(event, arg, display_prefix=prefix):
                    yield msg
                event.stop_event()
                return
        # --- 帮助指令匹配 ---
        for cmd_key in (f"{p}帮助", f"{p}menu", f"{p}菜单", f"{p}commands", f"{p}cmds"):
            if (
                text_lower == cmd_key
                or text_lower.startswith(cmd_key + " ")
                or text_lower.startswith(cmd_key + "\n")
            ):
                arg = text[len(cmd_key) :].strip()
                async for msg in self._handle_help(
                    event, arg, display_prefix=prefix
                ):
                    yield msg
                event.stop_event()
                return

    # ------------------------------------------------------------------
    # LLM 函数工具
    # ------------------------------------------------------------------

    @filter.llm_tool(name="roll_dice")
    async def roll_dice_tool(
        self,
        event: AstrMessageEvent,
        expression: str = "",
        label: str = "",
        dc: float | None = None,
    ) -> str:
        """
        在 TRPG/DnD 游戏中掷骰子。当需要进行攻击骰、伤害骰、属性检定、豁免或任何
        需要随机结果的场合时调用此工具。返回掷骰结果，由你将结果融入叙事后回复给用户。

        Args:
            expression(string): DnD/Roll20 标准骰池表达式，不含标签和 DC。
                常用格式：d20、1d20+5、4d6kh3、2d20kl1（劣势）、4dF（FATE 骰）、
                d6!（爆炸骰）、3d6>3（目标数成功计数）。留空则掷当前会话默认骰。
                支持多重投掷（3#d20+d6 = 重复 3 次，上限 20，不支持 dc）与
                四则运算/括号（3d6*(2+4)d12、(2+3)d6、3d(2*4)）。
                也可直接写角色卡联动别名：「力量/感知/敏捷豁免/攻击/攻击 长剑」，
                将自动使用玩家活跃卡上的修正掷 d20。
            label(string): 本次投掷的说明，不需要标签时传空字符串。
            dc(number): 难度等级（DC），掷骰总计 >= DC 判定成功；无需判定时不传。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        # 将标签、DC 拼入表达式，交给解析器处理。
        effective_sides = await self._get_effective_sides(event)
        # 角色卡联动：LLM 说「骰个感知检定」时 expression 可能直接是别名。
        hit = await self._try_character_roll(event, (expression or "").strip())
        if hit is not None:
            output, hist_expr = hit
            if hist_expr and not output.startswith(ROLL_ERROR_PREFIXES):
                await self._history.add(event, hist_expr, output)
            return output
        full_expr = _compose_tool_expr(expression, label, dc, effective_sides)
        output = self._do_roll(full_expr, default_sides=effective_sides)

        # 将结果返回给 LLM，由 LLM 将骰点结果融入叙事后回复用户。
        if not output.startswith(ROLL_ERROR_PREFIXES):
            await self._history.add(event, full_expr, output)
        return output

    @filter.llm_tool(name="manage_initiative")
    async def manage_initiative_tool(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        name: str = "",
        value: float | None = None,
        modifier: float | None = None,
    ) -> str:
        """
        管理 TRPG/DnD 战斗中的先攻（Initiative）列表。当战斗开始、需要为参战单位
        掷先攻、查看当前行动顺序、推进回合或移除已倒下的单位时调用此工具。

        Args:
            action(string): 要执行的操作。取值：roll=为指定 name 掷先攻入列（可带
                modifier 调整值，如 +2）；fixed=以指定 value 作为该单位的先攻固定值
                入列（如怪物抄书值）；list=查看当前先攻列表（默认动作）；end=推进到
                下一个单位的回合（轮数自动计数）；remove=将指定 name 的单位移出列表
                （单位倒下/离场）；clear=清空整个先攻列表（战斗结束）。
            name(string): 单位名称（玩家名或怪物名）。roll/fixed/remove 必须提供。
            value(number): 先攻固定值，仅 action="fixed" 时使用。
            modifier(number): 先攻调整值，仅 action="roll" 时使用，可省略（默认 0）。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        action = (action or "list").strip().lower()

        if action in ("list", "查看", "列表", ""):
            state = await self._initiative.get_state(event)
            return InitiativeManager.format_list(state)

        if action in ("clear", "清空", "结束战斗"):
            count = await self._initiative.clear(event)
            return f"已清空先攻列表，共移除 {count} 个单位。" if count else "先攻列表本来就是空的。"

        if action in ("end", "next", "推进", "下一位"):
            result = await self._initiative.advance(event)
            return InitiativeManager.format_advance(result)

        if action in ("remove", "del", "移除", "删除"):
            target = (name or "").strip()
            if not target:
                return "请提供要移除的单位名称（name 参数）。"
            result = await self._initiative.remove(event, target)
            if result.removed is None:
                return f"先攻列表中未找到「{target}」。"
            lines = [f"☠️ 已移除 {result.removed.name}（先攻 {result.removed.value}）。"]
            if result.next_current is not None:
                lines.append(
                    f"现在轮到 {result.next_current.name}"
                    f"（先攻 {result.next_current.value}）行动。"
                )
            lines.append(f"剩余 {len(result.state.entries)} 个单位。")
            return "\n".join(lines)

        if action in ("roll", "掷", "掷先攻"):
            target = (name or "").strip() or "未知单位"
            mod = _safe_int(modifier, 0)
            die = self._roll_d20()
            if die is None:
                return "先攻掷骰失败，请稍后再试。"
            value_total = die + mod
            _, entry = await self._initiative.add(
                event,
                name=target,
                value=value_total,
                modifier=mod,
                is_fixed=False,
            )
            return (
                f"{InitiativeManager.format_entry_confirmation(entry)}，已加入先攻列表。"
            )

        if action in ("fixed", "固定", "录入"):
            target = (name or "").strip() or "未知单位"
            if value is None:
                return "录入固定先攻需要提供 value 参数。"
            fixed = _safe_int(value, 0)
            _, entry = await self._initiative.add(
                event,
                name=target,
                value=fixed,
                is_fixed=True,
            )
            return (
                f"{InitiativeManager.format_entry_confirmation(entry)}，已加入先攻列表。"
            )

        return (
            "未知的 action。可用值：roll / fixed / list / end / remove / clear。"
        )

    @filter.llm_tool(name="manage_inventory")
    async def manage_inventory_tool(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        item: str = "",
        qty: float | None = None,
        weight: float | None = None,
        value: float | None = None,
        note: str = "",
        to_party: bool = False,
        items: list | None = None,
    ) -> str:
        """
        管理 TRPG/DnD 玩家的背包（Inventory）。当玩家获得战利品、使用/消耗物品、
        查看自己或队伍的物资、在个人背包与队伍背包之间转移物品时调用此工具。
        支持一次操作多种物品（items 数组批量发放/撤回/流转）。

        注意：本工具不支持玩家间赠送（give），用户要求赠送时请引导其使用
        「/bag give @某人 <名称> [数量]」命令。

        Args:
            action(string): 要执行的操作。取值：list=查看背包（默认动作，
                to_party=true 查看队伍背包）；add=放入物品（默认入本人背包，
                to_party=true 入队伍背包）；remove=取出/消耗物品，数量归零自动删除
                （默认从本人背包取出，to_party=true 从队伍背包取出——撤回
                战利品需要管理员/白名单权限）；put=将物品从本人背包存入队伍
                背包；take=将物品从队伍背包取到本人背包；edit=修改物品的属性
                （重量/价值/备注），不改变数量与名称；clear=清空本人背包
                （仅个人；队伍背包不支持经此工具清空）。
            item(string): 物品名称。单件操作（add/remove/put/take/edit）时提供；
                使用 items 批量时忽略。
            qty(number): 数量，省略时默认为 1。使用 items 批量时忽略。
            weight(number): 单件重量。add 时直接设置；edit 时不传=不修改，
                传 -1=清除该属性，传 >=0=覆盖。使用 items 批量时在元素内设置。
            value(number): 单件价值。add 时直接设置；edit 时不传=不修改，
                传 -1=清除该属性，传 >=0=覆盖。使用 items 批量时在元素内设置。
            note(string): 备注。add 时直接设置；edit 时不传或空串=不修改，
                传 "-"=清除备注，其他=覆盖。使用 items 批量时在元素内设置。
            to_party(boolean): 目标/来源是否为队伍背包（见各 action 说明）。
            items(array): 批量物品列表（可选）。元素为对象：item 必填（物品
                名称），qty 可选（数量，默认 1）；action=add 时元素还可含
                weight（单件重量）、value（单件价值，铜币，支持"2金5银"）、
                note（备注）；action=put/take 时元素仅用 item 与 qty（流转
                携带源条目属性）。提供 items 时忽略 item/qty 单件参数。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        action = (action or "list").strip().lower()
        is_private = event.is_private_chat()

        # items 批量解析：None/空 → []（走单件）；非法 → 错误文案。
        batch = _normalize_tool_inventory_items(items)
        if isinstance(batch, str):
            return batch

        if action in ("list", "查看", "列表", ""):
            if to_party:
                if is_private:
                    return "私聊没有队伍背包，这里只有用户自己的物品。"
                inv = await self._inventory.get_party(event)
                if not inv.items:
                    return "📦 队伍背包是空的。"
                return InventoryManager.format_inventory(inv, "📦 队伍背包")
            inv = await self._inventory.get_personal(event)
            if not inv.items:
                return "🎒 背包是空的。"
            owner = str(event.get_sender_name()).strip() or "你"
            return InventoryManager.format_inventory(inv, f"🎒 {owner} 的背包")

        if action in ("add", "放入", "添加"):
            if to_party and is_private:
                return "私聊没有队伍背包，无法放入公共物资。"
            if batch:
                ok_count = 0
                lines: list[str] = []
                for spec in batch:
                    try:
                        entry, _ = await self._inventory.add_item(
                            event,
                            spec["item"],
                            spec["qty"],
                            weight=spec.get("weight"),
                            value=spec.get("value"),
                            note=spec.get("note"),
                            to_party=to_party,
                        )
                    except ValueError as e:
                        lines.append(f"❌ {spec['item']}：{e}")
                        continue
                    ok_count += 1
                    lines.append(f"✅ {entry.name} ×{spec['qty']}（现有 {entry.qty} 个）")
                fail_count = len(batch) - ok_count
                head = f"➕ 批量放入：成功 {ok_count} 件" + (
                    f"，失败 {fail_count} 件。" if fail_count else "。"
                )
                return head + "\n" + "\n".join(lines)
            target = (item or "").strip()
            if not target:
                return "请提供物品名称（item 参数）。"
            n = _safe_int(qty, 1, min_val=1, max_val=99999)
            try:
                entry, _ = await self._inventory.add_item(
                    event,
                    target,
                    n,
                    weight=weight,
                    value=value,
                    note=note or None,
                    to_party=to_party,
                )
            except ValueError as e:
                return f"放入失败：{e}"
            where = "队伍背包" if to_party else "背包"
            return f"➕ 已放入 {entry.name} ×{n}（{where}现有 {entry.qty} 个）。"

        if action in ("remove", "rm", "取出", "消耗", "丢弃"):
            if to_party and is_private:
                return "私聊没有队伍背包。"
            # 从队伍背包撤回战利品 = 破坏性操作，与命令侧 /收回 同口径鉴权。
            if to_party and not await self._check_destructive_permission(event):
                return (
                    "你没有权限从队伍背包取出物品（撤回战利品需管理员/白名单权限）。"
                    + ("（白名单模式已启用，请联系管理员）" if self.enable_whitelist else "")
                )
            if batch:
                ok_count = 0
                lines: list[str] = []
                for spec in batch:
                    result = await self._inventory.remove_item(
                        event, spec["item"], spec["qty"], from_party=to_party
                    )
                    where = "队伍背包" if to_party else "背包"
                    if not result.found:
                        lines.append(f"❌ {where}里没有「{spec['item']}」。")
                        continue
                    if result.removed_qty == 0:
                        lines.append(
                            f"❌ {where}里只有 {result.remaining} 个「{spec['item']}」，"
                            f"无法取出 {spec['qty']} 个。"
                        )
                        continue
                    ok_count += 1
                    if result.deleted:
                        lines.append(
                            f"✅ 已取出 {spec['item']} ×{result.removed_qty}，"
                            f"{where}中已无此物品"
                        )
                    else:
                        lines.append(
                            f"✅ 已取出 {spec['item']} ×{result.removed_qty}"
                            f"（{where}剩余 {result.remaining} 个）"
                        )
                fail_count = len(batch) - ok_count
                head = f"➖ 批量取出：成功 {ok_count} 件" + (
                    f"，失败 {fail_count} 件。" if fail_count else "。"
                )
                return head + "\n" + "\n".join(lines)
            target = (item or "").strip()
            if not target:
                return "请提供物品名称（item 参数）。"
            n = _safe_int(qty, 1, min_val=1, max_val=99999)
            result = await self._inventory.remove_item(
                event, target, n, from_party=to_party
            )
            where = "队伍背包" if to_party else "背包"
            if not result.found:
                return f"{where}里没有「{target}」。"
            if result.removed_qty == 0:
                return (
                    f"{where}里只有 {result.remaining} 个「{target}」，"
                    f"无法取出 {n} 个。"
                )
            if result.deleted:
                return f"➖ 已取出 {target} ×{result.removed_qty}，{where}中已无此物品。"
            return (
                f"➖ 已取出 {target} ×{result.removed_qty}"
                f"（{where}剩余 {result.remaining} 个）。"
            )

        if action in ("put", "存入"):
            if is_private:
                return "私聊没有队伍背包，无法存入公共物资。"
            if batch:
                ok_count = 0
                lines: list[str] = []
                for spec in batch:
                    result = await self._inventory.put_to_party(
                        event, spec["item"], spec["qty"]
                    )
                    if not result.ok:
                        if result.reason == "not_found":
                            lines.append(f"❌ 背包里没有「{spec['item']}」。")
                        else:
                            lines.append(
                                f"❌ 背包里只有 {result.available} 个"
                                f"「{spec['item']}」，无法存入 {spec['qty']} 个。"
                            )
                        continue
                    ok_count += 1
                    lines.append(
                        f"✅ {result.item_name} ×{result.qty} 已存入队伍背包"
                    )
                fail_count = len(batch) - ok_count
                head = f"📦 批量存入：成功 {ok_count} 件" + (
                    f"，失败 {fail_count} 件。" if fail_count else "。"
                )
                return head + "\n" + "\n".join(lines)
            target = (item or "").strip()
            if not target:
                return "请提供物品名称（item 参数）。"
            n = _safe_int(qty, 1, min_val=1, max_val=99999)
            result = await self._inventory.put_to_party(event, target, n)
            if not result.ok:
                if result.reason == "not_found":
                    return f"背包里没有「{target}」。"
                return (
                    f"背包里只有 {result.available} 个「{target}」，无法存入 {n} 个。"
                )
            return f"📦 已将 {result.item_name} ×{result.qty} 存入队伍背包。"

        if action in ("take", "拿取"):
            if is_private:
                return "私聊没有队伍背包。"
            if batch:
                ok_count = 0
                lines: list[str] = []
                for spec in batch:
                    result = await self._inventory.take_from_party(
                        event, spec["item"], spec["qty"]
                    )
                    if not result.ok:
                        if result.reason == "not_found":
                            lines.append(f"❌ 队伍背包里没有「{spec['item']}」。")
                        else:
                            lines.append(
                                f"❌ 队伍背包里只有 {result.available} 个"
                                f"「{spec['item']}」。"
                            )
                        continue
                    ok_count += 1
                    lines.append(
                        f"✅ 已从队伍背包取出 {result.item_name} ×{result.qty}"
                    )
                fail_count = len(batch) - ok_count
                head = f"📦 批量取出：成功 {ok_count} 件" + (
                    f"，失败 {fail_count} 件。" if fail_count else "。"
                )
                return head + "\n" + "\n".join(lines)
            target = (item or "").strip()
            if not target:
                return "请提供物品名称（item 参数）。"
            n = _safe_int(qty, 1, min_val=1, max_val=99999)
            result = await self._inventory.take_from_party(event, target, n)
            if not result.ok:
                if result.reason == "not_found":
                    return f"队伍背包里没有「{target}」。"
                return f"队伍背包里只有 {result.available} 个「{target}」。"
            return f"📦 已从队伍背包取出 {result.item_name} ×{result.qty}。"

        if action in ("edit", "修改", "编辑"):
            target = (item or "").strip()
            if not target:
                return "请提供物品名称（item 参数）。"
            if to_party and is_private:
                return "私聊没有队伍背包。"
            # 三态：不传 = 不修改；-1 / "-" = 清除；其余 = 覆盖。
            w = _UNSET if weight is None else weight
            v = _UNSET if value is None else value
            n = _UNSET if not note else (None if note == "-" else note)
            entry = await self._inventory.edit_item(
                event,
                target,
                weight=w,
                value=v,
                note=n,
                in_party=to_party,
            )
            where = "队伍背包" if to_party else "背包"
            if entry is None:
                return f"{where}里没有「{target}」。"
            return f"✏️ 已更新 {InventoryManager.format_item_line(entry)}"

        if action in ("clear", "清空"):
            count = await self._inventory.clear_personal(event)
            return (
                f"🎒 已清空背包，共移除 {count} 种物品。"
                if count
                else "🎒 背包本来就是空的。"
            )

        return (
            "未知的 action。可用值：add / remove / list / put / take / edit / clear。"
            "（不支持 give，请引导用户使用 .bag give 命令）"
        )

    @filter.llm_tool(name="manage_shop")
    async def manage_shop_tool(
        self,
        event: AstrMessageEvent,
        action: str = "list",
        item: str = "",
        qty: float | None = None,
        page: float | None = None,
        items: list | None = None,
    ) -> str:
        """
        管理 TRPG/DnD 会话商店（Shop）的购买/卖出/上架/下架/清空。当玩家在
        商店购买物品（自动从背包扣除金币/银币/铜币并找零）、把背包物品卖回
        商店（只收在架商品，按售价×回购系数付款）、DM 配置商店商品（上架/
        下架/清空），或想查看本会话商店的商品列表时调用此工具。

        注意：
        - 上架/下架/清空是管理操作，调用者需为 DM（白名单/管理员），否则
          会被拒绝并引导找 DM 操作；设价/设库存/初始化/回购率仍由 DM 通过
          「/商店」命令配置，本工具不开放。
        - 支持批量：一次可处理多件商品（items 数组）。批量逐件结算：某项
          失败（不在架/库存不足/钱不够）不影响其他项，结果逐行列出。
        - 货币结算遵循「货币即物品」：背包需有 金币/银币/铜币 条目
          （1金币=10银币=100铜币），购买时自动折铜扣款并找零。
        - list 每页最多 30 条：商品较多时列表末尾会给出总页数，如需查看
          后续页请用 page 参数再次调用本工具（页码越界自动夹取到合法范围）。

        Args:
            action(string): 要执行的操作。取值：list=查看本会话商店的商品列表
                （默认动作）；buy=购买物品（按商店售价从背包扣款并自动入包）；
                sell=把背包物品卖回商店（只收在架商品，按售价×回购系数付款）；
                add=上架商品（管理操作，需白名单/管理员）；remove=下架商品
                （管理操作）；clear=清空整店、移除全部商品（管理操作，保留
                回购系数）。
            item(string): 物品名称（单件模式，未提供 items 时使用）。buy/sell
                需与商店列表中的名称一致；add/remove 为待上下架的商品名。
            qty(number): 数量（单件模式），省略时默认为 1。
            page(number): 列表页码（仅 action=list 使用），从 1 开始，省略时为
                第 1 页；越界自动夹取到首/末页。
            items(array): 批量物品列表（v0.39.0），元素为对象，必含 item 字段
                （物品名称），可选 qty（数量，默认 1）。action=add 时元素还可
                含 price（售价，如 "2金" 或铜币整数，省略=用库价）与 stock
                （库存，数字或 "无限"，省略=无限）。提供 items 时忽略 item/qty
                单件参数。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        action = (action or "list").strip().lower()
        origin = event.unified_msg_origin
        manager = self.shop_manager

        if action in ("list", "查看", "列表", ""):
            shop = await manager.get(origin)
            if not shop.entries:
                return "🏪 本店还没有商品，可找 DM 用「/商店 初始化」或「/商店 上架」配置。"
            p = _safe_int(page, 1, min_val=1, max_val=100000)
            text = ShopManager.format_shop(
                shop, page=p, price_resolver=manager.resolve_price
            )
            if len(shop.entries) > ShopManager.list_limit():
                text += (
                    "\n（商品较多，如需查看后续页，请用本工具的 page 参数"
                    "再次调用，如 page=2。）"
                )
            return text

        if action in ("buy", "买", "购买"):
            specs = _normalize_tool_items(items)
            if isinstance(specs, str):
                return specs
            if not specs:
                # 单件回退（旧参数兼容）
                target = (item or "").strip()
                if not target:
                    return "请提供物品名称（item 参数，或 items 数组）。"
                n = _safe_int(qty, 1, min_val=1, max_val=99999)
                result = await manager.buy(event, origin, target, n)
                if not result.ok:
                    if result.reason == "not_found":
                        return f"商店里没有「{target}」，可先 list 查看在售商品。"
                    if result.reason == "sold_out":
                        return f"「{target}」库存不足（现有 {result.stock_left} 个）。"
                    if result.reason == "no_price":
                        return (
                            f"「{target}」没有定价，需 DM 用「/商店 设价」覆盖价格。"
                        )
                    return (
                        f"钱不够：购买需 {format_cp(result.total_cp)}"
                        f"（{format_cp(result.price_cp)}/件 ×{result.qty}），"
                        f"还差 {format_cp(result.shortfall_cp)}。"
                    )
                stock_note = (
                    f"，余 {result.stock_left} 件" if result.stock_left is not None else ""
                )
                return (
                    f"🛒 已购买 {result.item_name} ×{result.qty}，"
                    f"花费 {format_cp(result.total_cp)}"
                    f"（{format_cp(result.price_cp)}/件）{stock_note}。"
                )
            # 批量购买（逐件原子）
            lines: list[str] = []
            ok_count = 0
            for spec in specs:
                result = await manager.buy(event, origin, spec["item"], spec["qty"])
                if result.ok:
                    ok_count += 1
                lines.append(_format_buy_result(result))
            fail_count = len(specs) - ok_count
            summary = f"🛒 批量购买：成功 {ok_count} 件"
            if fail_count:
                summary += f"，失败 {fail_count} 件"
            return summary + "。\n" + "\n".join(lines)

        if action in ("sell", "卖", "出售"):
            specs = _normalize_tool_items(items)
            if isinstance(specs, str):
                return specs
            if not specs:
                # 单件回退（旧参数兼容）
                target = (item or "").strip()
                if not target:
                    return "请提供物品名称（item 参数，需为在架商品）。"
                n = _safe_int(qty, 1, min_val=1, max_val=99999)
                result = await manager.sell(event, origin, target, n)
                if not result.ok:
                    if result.reason == "not_found":
                        return (
                            f"商店不收「{target}」（只回收在架商品）。"
                            "可先 list 查看商店收哪些物品。"
                        )
                    if result.reason == "no_price":
                        return f"「{target}」没有定价，暂无法回收。"
                    return f"背包里没有足够的「{target}」可以出售。"
                stock_note = (
                    f"，商店余 {result.stock_left} 件" if result.stock_left is not None else ""
                )
                return (
                    f"💰 已卖出 {result.item_name} ×{result.qty}，"
                    f"获得 {format_cp(result.pay_cp)}"
                    f"（{format_cp(result.price_cp)}/件×回购系数）{stock_note}。"
                )
            # 批量卖回（逐件原子）
            lines: list[str] = []
            ok_count = 0
            for spec in specs:
                result = await manager.sell(event, origin, spec["item"], spec["qty"])
                if result.ok:
                    ok_count += 1
                lines.append(_format_sell_result(result))
            fail_count = len(specs) - ok_count
            summary = f"💰 批量卖出：成功 {ok_count} 件"
            if fail_count:
                summary += f"，失败 {fail_count} 件"
            return summary + "。\n" + "\n".join(lines)

        if action in ("add", "上架", "上新"):
            if not await self._check_destructive_permission(event):
                return "你没有权限上架商品（需要白名单/管理员），请找 DM 操作。"
            specs = _normalize_tool_items(items)
            if isinstance(specs, str):
                return specs
            if not specs:
                target = (item or "").strip()
                if not target:
                    return "请提供物品名称（item 参数，或 items 数组）。"
                specs = [{"item": target, "qty": 1}]
            lines: list[str] = []
            ok_count = 0
            for spec in specs:
                weight_lb = self._shop_add_weight(spec["item"])
                ok, reason = await manager.add_entry(
                    origin,
                    spec["item"],
                    price_cp=spec.get("price"),
                    stock=spec.get("stock"),
                    weight_lb=weight_lb,
                )
                if not ok:
                    lines.append(f"❌ {reason}")
                    continue
                ok_count += 1
                price_note = (
                    format_cp(spec["price"]) if spec.get("price") is not None else "库价"
                )
                stock_note = (
                    "无限" if spec.get("stock") is None else f"库存 {spec['stock']}"
                )
                lines.append(
                    f"➕ 已上架 {spec['item']}（{price_note}，{stock_note}）。"
                )
            fail_count = len(specs) - ok_count
            summary = f"➕ 批量上架：成功 {ok_count} 件"
            if fail_count:
                summary += f"，失败 {fail_count} 件"
            return summary + "。\n" + "\n".join(lines)

        if action in ("remove", "下架", "rm"):
            if not await self._check_destructive_permission(event):
                return "你没有权限下架商品（需要白名单/管理员），请找 DM 操作。"
            specs = _normalize_tool_items(items)
            if isinstance(specs, str):
                return specs
            if specs:
                names = [s["item"] for s in specs]
            else:
                target = (item or "").strip()
                names = [target] if target else []
            if not names:
                return "请提供物品名称（item 参数，或 items 数组）。"
            removed: list[str] = []
            missing: list[str] = []
            for name in names:
                if await manager.remove_entry(origin, name):
                    removed.append(name)
                else:
                    missing.append(name)
            parts: list[str] = []
            if removed:
                parts.append(f"➖ 已下架：{'、'.join(removed)}（共 {len(removed)} 件）。")
            for n in missing:
                parts.append(f"❌ 商店里没有「{n}」。")
            return "\n".join(parts) if parts else "没有下架任何商品。"

        if action in ("clear", "清空"):
            if not await self._check_destructive_permission(event):
                return "你没有权限清空商店（需要白名单/管理员），请找 DM 操作。"
            count = await manager.clear(origin)
            return (
                f"🏪 已清空商店，共移除 {count} 种商品。"
                if count else "商店本来就是空的。"
            )

        return (
            "未知的 action。可用值：list / buy / sell / add / remove / clear。"
            "（设价/设库存/初始化/回购率请引导用户找 DM 使用 /商店 命令）"
        )

    @filter.llm_tool(name="manage_character")
    async def manage_character_tool(
        self,
        event: AstrMessageEvent,
        action: str = "show",
        name: str = "",
        field: str = "",
        value: str = "",
        new_name: str = "",
    ) -> str:
        """
        管理 TRPG/DnD 玩家的角色卡（Character Sheet）。当玩家查看自己的角色卡、
        列出/切换/删除/重命名卡片、修正六维属性（力量/敏捷/体质/智力/感知/魅力）、
        调整种族/职业/规则版本、手动调整战斗字段（HP/AC/法术位/攻击加值）、
        增减熟练技能与豁免、登记命名掷骰、角色升级时调用此工具。角色卡按
        「玩家+会话」多卡存储，name 为空时作用于活跃卡。引导创建请用 guide_chargen
        工具。注意：六维属性/种族/职业/版本设置会直接覆盖并自动触发战斗字段重算；
        HP/AC/速度/法术位/攻击/先攻的 base 值由规则引擎按职业/装备自动计算，
        set 只应用于房规 bonus 调整；设置装备槽（主手/副手/护甲）后插件会自动重算。

        Args:
            action(string): 要执行的操作。取值：show=查看角色卡（默认动作，
                name 为空查看活跃卡）；list=列出该玩家的全部卡；use=切换活跃卡
                （name 为目标卡名）；delete=删除卡（name）；rename=重命名
                （name=旧名，new_name=新名）；set=设置字段（field+value）；
                prof=增减熟练（field 为 技能|豁免|工具|武器|防具，value 形如
                「+察觉 -隐匿」，武器熟练变更后自动重算攻击加值）；
                expertise=增减技能专精（field 为 技能，value 形如「+察觉 -隐匿」，
                专精技能检定获得双倍熟练加值）；
                feat=维护专长列表（value 形如「+巨武器大师 -幸运」，名称自由文本）；
                named_roll=登记命名掷骰（value 形如「名称=表达式」，登记后
                /r <名称> 直接用该表达式掷骰；value 形如「名称=-」删除）；
                del_attack=删除攻击条目（field=攻击名；装备武器/「{职业}法术攻击」
                等引擎生成条目重算后会恢复，需先清空装备槽）；
                add_spell=加入已知法术（field=环阶 如 戏法/1环，value=法术名）；
                del_spell=删除已知法术（field=环阶，value=法术名）；
                level_up=指定职业 +1 级（value=职业名，留空=主职业，整卡自动重算）。
            name(string): 目标卡名。show/set/prof/expertise/feat/named_roll/level_up
                留空=活跃卡。
            field(string): set 时的字段名：六维属性 str/dex/con/int/wis/cha（力量/
                敏捷/体质/智力/感知/魅力，直接覆盖属性值 1-30，自动联动先攻/HP/AC/
                攻击加值重算）/race（种族）/classes（职业整体替换，值形如
                「战士 3 + 法师（塑能） 2」）/edition（版本 2014/2024，兼容 5e/5.5e）/
                hp/ac/slot1..slot9/attack（值形如「长剑=5」）/main_hand/off_hand/
                armor/background/backstory/alignment/
                languages（值形如「通用语,精灵语」，逗号/空格分隔多门语言）/
                deity/age/gender/height/weight（信仰/年龄/性别/身高/体重）/
                hit_dice_used（短休已用生命骰数）/inspiration（激励 0 或 1）/
                initiative（先攻房规额外加值）/spells（已知法术，值形如
                「戏法:火焰箭,光亮术　1环:法师护甲,护盾术」）；
                prof 时为 技能|豁免|工具|武器|防具；expertise 时为 技能。
            value(string): set/prof/expertise/feat/named_roll 的值；level_up 时为
                目标职业名。主手/副手/护甲传 "-" 清除。
            new_name(string): rename 时的新卡名。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        action = (action or "show").strip().lower()
        cm = self.character_manager
        name = (name or "").strip()

        if action in ("show", "查看", ""):
            card = await cm.get_card(event, name or None)
            if card is None:
                return (
                    "还没有角色卡。可以引导玩家用 /车卡 或 guide_chargen 创建；"
                    "若玩家已有一张文本角色卡，可用 /车卡 导入 直接导入落库。"
                )
            return cm.format_sheet(card, self.kb_manager)

        if action in ("list", "列表"):
            names = await cm.list_cards(event)
            active = await cm.get_active_name(event)
            return cm.format_card_list(names, active)

        if action in ("use", "切换"):
            if not name:
                return "请提供目标卡名（name 参数）。"
            ok = await cm.set_active(event, name)
            return f"已将活跃卡切换为「{name}」。" if ok else f"没有名为「{name}」的卡。"

        if action in ("delete", "del", "删除"):
            if not name:
                return "请提供要删除的卡名（name 参数）。"
            ok = await cm.delete_card(event, name)
            return (
                f"已删除角色卡「{name}」。活跃卡已回退到列表第一张。"
                if ok
                else f"没有名为「{name}」的卡。"
            )

        if action in ("rename", "改名"):
            if not name or not new_name:
                return "请提供 name（旧名）与 new_name（新名）。"
            ok, msg = await cm.rename_card(event, name, new_name)
            return msg

        if action in ("set", "设置", "设"):
            if not field:
                return "请提供要设置的字段（field 参数）。"
            key = self._CARD_SET_FIELD.get(field.strip().lower(), field.strip().lower())
            card, applied = await cm.update_fields(event, None, {key: value})
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            extra = ""
            if set(applied) & self._CARD_RECALC_FIELDS:
                report = self._recalc_card(card)
                if report is not None:
                    # update_fields 已落库，重算后 base 变化需再保存一次
                    err = await cm.save_card(event, card)
                    if report.text:
                        extra = "\n自动重算：" + report.text
                    if err is not None:
                        extra = f"\n自动重算后保存失败：{err}"
            return f"已更新 {','.join(applied)}。{extra}\n" + cm.format_sheet(card, self.kb_manager)

        if action in ("del_attack", "删攻击", "delatk"):
            if not field:
                return "请提供要删除的攻击条目名（field 参数）。"
            card = await cm.get_card(event, name or None)
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            target = field.strip()
            existed = target in card.attack_bonuses
            card, applied = await cm.update_fields(
                event, name or None, {"attack": f"{target}=-"}
            )
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            if not existed:
                return (
                    f"没有名为「{target}」的攻击条目。"
                    f"当前：{'、'.join(card.attack_bonuses) or '无'}。"
                )
            msg = f"已删除攻击条目「{target}」。"
            if self._is_generated_attack(card, target):
                msg += " 注意：该条目由规则引擎按装备/职业自动生成，重算后会恢复；要彻底移除请先清空对应装备槽。"
            return msg + "\n" + cm.format_sheet(card, self.kb_manager)

        if action in ("add_spell", "加法术", "addspell"):
            card = await cm.get_card(event, name or None)
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            ok = card.add_spell(field, value)
            if not ok:
                return "环阶无法识别（支持 戏法/0环/cantrip、一环/1环/1 … 九环/9环）或法术名为空。"
            err = await cm.save_card(event, card)
            ring = _norm_spell_ring(field)
            label = _spell_ring_label(ring) if ring else "已知法术"
            msg = f"已加入法术「{value}」（{label}）。"
            if err is not None:
                msg += f" 保存失败：{err}"
            return msg + "\n" + cm.format_sheet(card, self.kb_manager)

        if action in ("del_spell", "删法术", "delspell"):
            card = await cm.get_card(event, name or None)
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            ok = card.remove_spell(field, value)
            if not ok:
                rings = "、".join(_spell_ring_label(r) for r in card.spells) or "无"
                return f"未找到「{value}」在 {field} 下的记录（现有环阶：{rings}）。"
            err = await cm.save_card(event, card)
            msg = f"已删除法术「{value}」。"
            if err is not None:
                msg += f" 保存失败：{err}"
            return msg + "\n" + cm.format_sheet(card, self.kb_manager)

        if action in ("level_up", "升级", "升"):
            card, report, err = await cm.level_up(
                event, name or None, value, recalc_fn=self._recalc_card
            )
            if err is not None:
                return err
            lines = [f"「{card.name}」已升级！"]
            if report is not None and report.text:
                lines.append("自动重算：" + report.text)
            lines.append(cm.format_sheet(card, self.kb_manager))
            return "\n".join(lines)

        if action in ("prof", "熟练", "proficiency"):
            prof_kind_map = {
                "技能": ("skills", "skill_proficiencies", True),
                "skill": ("skills", "skill_proficiencies", True),
                "豁免": ("saves", "save_proficiencies", True),
                "save": ("saves", "save_proficiencies", True),
                "saves": ("saves", "save_proficiencies", True),
                "工具": ("tools", "tool_proficiencies", False),
                "tool": ("tools", "tool_proficiencies", False),
                "武器": ("weapons", "weapon_proficiencies", False),
                "weapon": ("weapons", "weapon_proficiencies", False),
                "防具": ("armors", "armor_proficiencies", False),
                "armor": ("armors", "armor_proficiencies", False),
            }
            kind = (field or "").strip().lower()
            if kind not in prof_kind_map:
                return "请提供 field=技能|豁免|工具|武器|防具，value 形如「+察觉 -隐匿」。"
            prof_key, prof_attr, use_alias = prof_kind_map[kind]
            card = await cm.get_card(event)
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            current = set(getattr(card, prof_attr))
            unknown: list[str] = []
            for tok in (value or "").split():
                add = tok.startswith("+")
                remove = tok.startswith("-")
                if not add and not remove:
                    unknown.append(tok)
                    continue
                item = tok[1:].strip()
                if use_alias:
                    canon = (SKILL_ALIAS if prof_key == "skills" else ABILITY_ALIAS).get(
                        item.lower()
                    )
                    if canon is None:
                        unknown.append(tok)
                        continue
                else:
                    canon = item
                    if not canon:
                        unknown.append(tok)
                        continue
                if add:
                    current.add(canon)
                else:
                    current.discard(canon)
            card, applied = await cm.update_fields(
                event, None, {prof_key: ",".join(sorted(current))}
            )
            msg = f"已更新{kind}熟练。"
            if unknown:
                msg += f" 无法识别：{'、'.join(unknown)}"
            # 武器熟练影响攻击加值 → 变更后重算 base
            if card is not None and prof_key == "weapons":
                report = self._recalc_card(card)
                if report is not None:
                    err = await cm.save_card(event, card)
                    if report.text:
                        msg += "\n自动重算：" + report.text
                    if err is not None:
                        msg += f"\n自动重算后保存失败：{err}"
            if card is not None:
                msg += "\n" + cm.format_sheet(card, self.kb_manager)
            return msg

        if action in ("expertise", "专精", "exp"):
            card = await cm.get_card(event)
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            current = set(card.skill_expertise)
            unknown: list[str] = []
            not_proficient: list[str] = []
            for tok in (value or "").split():
                add = tok.startswith("+")
                remove = tok.startswith("-")
                if not add and not remove:
                    unknown.append(tok)
                    continue
                item = tok[1:].strip()
                canon = SKILL_ALIAS.get(item.lower())
                if canon is None:
                    unknown.append(tok)
                    continue
                if add:
                    current.add(canon)
                    if canon not in card.skill_proficiencies:
                        not_proficient.append(SKILL_CN_REV.get(canon, canon))
                else:
                    current.discard(canon)
            card, applied = await cm.update_fields(
                event, None, {"expertise": ",".join(sorted(current))}
            )
            msg = "已更新技能专精。"
            if not_proficient:
                msg += f" 注意：{'、'.join(not_proficient)}尚未熟练"
            if unknown:
                msg += f" 无法识别：{'、'.join(unknown)}"
            if card is not None:
                msg += "\n" + cm.format_sheet(card, self.kb_manager)
            return msg

        if action in ("feat", "feats", "专长"):
            card = await cm.get_card(event)
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            current = list(card.feats)
            unknown: list[str] = []
            not_found: list[str] = []
            for tok in (value or "").split():
                add = tok.startswith("+")
                remove = tok.startswith("-")
                if not add and not remove:
                    unknown.append(tok)
                    continue
                item = tok[1:].strip()
                if not item:
                    unknown.append(tok)
                    continue
                if add:
                    if item not in current:
                        current.append(item)
                    try:
                        if self.kb_manager.available and not self.kb_manager.search(
                            item, kind="feat"
                        ):
                            not_found.append(item)
                    except Exception:  # noqa: BLE001 — kb 不可用不阻断
                        pass
                else:
                    current = [f for f in current if f != item]
            card, applied = await cm.update_fields(
                event, None, {"feats": ",".join(current)}
            )
            msg = "已更新专长。"
            if not_found:
                msg += f" 知识库未收录（已保存）：{'、'.join(not_found)}"
            if unknown:
                msg += f" 无法识别：{'、'.join(unknown)}"
            if card is not None:
                msg += "\n" + cm.format_sheet(card, self.kb_manager)
            return msg

        if action in ("named_roll", "骰"):
            if not value or "=" not in value:
                return "请提供 value，形如「名称=表达式」（「名称=-」删除）。"
            card, applied = await cm.update_fields(
                event, None, {"named_roll": value}
            )
            if card is None:
                return "还没有角色卡，请先引导玩家车卡。"
            deleting = value.partition("=")[2].strip() in ("-", "－", "删", "删除")
            if deleting:
                if "named_roll" not in applied:
                    nname = value.split("=", 1)[0].strip()
                    return f"没有名为「{nname}」的命名掷骰。"
                return "已删除命名掷骰。\n" + cm.format_sheet(card, self.kb_manager)
            return "已记录命名掷骰。\n" + cm.format_sheet(card, self.kb_manager)

        return (
            "未知的 action。可用值：show / list / use / delete / rename / set / prof / "
            "expertise / feat / named_roll / del_attack / add_spell / del_spell / "
            "level_up。"
        )

    @filter.llm_tool(name="guide_chargen")
    async def guide_chargen_tool(
        self,
        event: AstrMessageEvent,
        action: str = "status",
        answer: str = "",
        assign: str = "",
        race: str = "",
        class_name: str = "",
        background: str = "",
    ) -> str:
        """
        逐步引导新手玩家创建 DnD 角色卡（车卡）。这是车卡的引导入口：插件维护
        车卡状态机（草稿），你作为 DM 助手按工具返回的指示向玩家提问，一次只问
        一个问题。每次调用返回三段式：【进度】当前步骤、【校验】本步结果（含
        硬拒绝原因）、【下一问】你接下来要问玩家的唯一一个问题。禁止替玩家作答、
        禁止跳步。属性生成（购点/标准数组/掷骰代骰）与规则校验全部由插件完成，
        你不要自行计算购点花费。

        【守则】禁止在对话中输出/展示完整角色卡文本——角色卡只能通过调用本
        工具逐步完成并落库，玩家要求「直接生成一张卡」时也必须继续调用本工具
        逐步提交信息，不得自行编卡。若玩家已经有一张文本卡（如对话中生成的卡
        文本），请提示其使用「/车卡 导入 <卡文本>」命令导入落库。

        【构筑联动（v0.35.0）】如果之前用 advise_build 给出过构筑方案且玩家
        确认采用，action=start 时可携带 race/class_name/background 预填参数
        （取值必须来自 advise_build 返回的档案，禁止凭记忆填），插件校验通过
        后自动跳过对应步骤；非法值会被忽略并回到正常询问。

        Args:
            action(string): 要执行的操作。取值：start=开始车卡（返回群规则摘要
                与第一问）；answer=提交玩家对当前问题的回答（answer 参数）；
                status=查看当前进度与待问问题（默认动作）；cancel=取消并丢弃草稿。
            answer(string): action=answer 时玩家对当前问题的回答原文。属性分配步
                接受「15 14 13 12 10 8」（按 力/敏/体/智/感/魅 顺序）或
                「力15 敏14 体13 智12 感10 魅8」两种格式；加值选择步（种族/背景
                自选加值）接受「力+2 敏+1」格式。
            assign(string): 属性分配步的显式映射（可选），格式同 answer 的
                「力15 敏14…」；提供时优先于 answer 用于属性分配。
            race(string): 预填种族名（可选，仅 action=start 时生效；2024 路径
                写入物种）。值必须来自 advise_build 档案。
            class_name(string): 预填职业名（可选，仅 action=start 时生效）。
                值必须来自 advise_build 档案。
            background(string): 预填背景名（可选，仅 action=start 时生效）。
                值必须来自 advise_build 档案。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        action = (action or "status").strip().lower()
        cg = self.chargen_manager

        if action in ("start", "开始"):
            prefill: dict[str, str] = {}
            for k, v in (
                ("race", race), ("class_name", class_name),
                ("background", background),
            ):
                if (v or "").strip():
                    prefill[k] = v.strip()
            reply = await cg.start(event, prefill=prefill or None)
            return reply.format() + "\n" + self._chargen_guard_note()

        if action in ("cancel", "取消", "abort"):
            reply = await cg.cancel(event)
            return reply.format()

        if action in ("answer", "提交", "下一步"):
            reply = await cg.advance(event, answer, assign=assign)
            await self._log_chargen_rolls(event, reply)
            text = reply.format()
            if reply.done:
                card = await self.character_manager.get_card(event)
                if card is not None:
                    text += "\n\n" + self.character_manager.format_sheet(card, self.kb_manager)
            else:
                text += "\n" + self._chargen_guard_note()
            return text

        # status（默认）
        reply = await cg.status(event)
        return reply.format() + "\n" + self._chargen_guard_note()

    @staticmethod
    def _chargen_guard_note() -> str:
        """车卡引导守则：追加到 guide_chargen 非完成返回末尾，防 LLM 脱轨。

        提醒 LLM：角色卡只能经工具落库，禁止在对话中直接输出卡文本；
        已有文本卡引导玩家用 /车卡 导入。
        """
        return (
            "【守则】角色卡只能通过继续调用 guide_chargen 逐步完成并落库，"
            "禁止直接在对话中输出完整角色卡文本；玩家要求直接出卡时也必须"
            "继续调用本工具。已有文本卡可用 /车卡 导入 直接导入落库。"
        )

    # ------------------------------------------------------------------
    # LLM 请求前钩子：向 system_prompt 注入全工具守则，防 LLM「扮演/编造」
    # 而不调工具。
    #
    # 背景：AstrBot v4.5+ 插件工具需在 WebUI 工具面板启用后才注入 LLM，且
    # 工具描述只在模型决定调用时才可见。若模型在检定、车卡这类开放式任务上
    # 倾向于直接用自然语言扮演/编造，docstring 里的守则永远不会被看到。
    # 该钩子在每个 LLM 请求的 system_prompt 末尾追加压缩版守则（8 个工具
    # 按场景一句一条 + 破坏性操作约束），让模型在生成回复前就知道必须走
    # 工具，与社区插件（如 live_dashboard）的「@filter.on_llm_request 注入
    # 工具使用规范」做法一致。
    # ------------------------------------------------------------------
    @filter.on_llm_request()
    async def _on_llm_request_guard(self, event, req):
        """向当前 LLM 请求注入「TRPG 数据操作必须走工具」的全工具守则。

        Args:
            event: 触发请求的消息事件（新旧版本均可，本钩子不依赖其字段）。
            req: ProviderRequest 对象，含 system_prompt 字段；在请求送达
                LLM 前原地追加守则，不影响其他插件注入的内容。
        """
        try:
            guard = self._llm_request_guard()
            current = getattr(req, "system_prompt", None) or ""
            if guard not in current:
                req.system_prompt = current + guard
        except Exception as e:  # noqa: BLE001 - 钩子容错，绝不阻断 LLM 流程
            logger.warning(f"[trpg_assistant] on_llm_request 注入工具守则失败: {e}")

    @staticmethod
    def _llm_request_guard() -> str:
        """LLM 系统提示词守则：9 个工具按场景主动调用，禁止编造/扮演代替。

        追加到每个 LLM 请求的 system_prompt 末尾；内容带可检索标记，
        供钩子做防重复追加判断。压缩版：每工具一句，覆盖 T1 必调
        （骰点/规则数据）与 T2 应调（先攻/背包/角色卡/车卡/商店），
        并明确破坏性操作（clear/delete/remove/cancel）等玩家要求。
        只约束相关意图，不影响其他对话场景。
        """
        return (
            "\n\n【跑团助手·工具守则】TRPG 叙事中以下操作必须调用工具、"
            "禁止编造或自行扮演代替：掷骰/检定→roll_dice；查询法术怪物物品"
            "等规则数据→query_dnd_knowledge；战斗先攻入列与推进→"
            "manage_initiative；战利品与消耗→manage_inventory；角色卡查看/"
            "修改/升级→manage_character；车卡建卡→guide_chargen 逐步落库，"
            "禁止直接输出卡文本，已有文本卡引导「/车卡 导入」；买卖→"
            "manage_shop；构筑/升级建议→advise_build（推荐条目必须来自工具"
            "返回，禁止凭记忆补充条目名）；私设转录/写入/点评→"
            "manage_homebrew（write 需配置开启且白名单/管理员；点评前必须"
            "用 query_dnd_knowledge 查同类型条目对照，禁止仅凭记忆点评）。"
            "清空/删除/移除/取消等破坏性操作不要主动执行，等玩家明确要求。"
        )

    @filter.llm_tool(name="query_dnd_knowledge")
    async def query_dnd_knowledge_tool(
        self,
        event: AstrMessageEvent,
        action: str = "detail",
        kind: str = "",
        name: str = "",
        level: float = -1,
        school: str = "",
        cr_min: float = -1.0,
        cr_max: float = -1.0,
        monster_type: str = "",
        rarity: str = "",
        subclass: str = "",
        feature: str = "",
        damage_type: str = "",
        condition: str = "",
        environment: str = "",
        weapon_property: str = "",
        components: str = "",
        concentration: float = -1,
        shape: str = "",
        target: str = "",
        range_type: str = "",
        range_max: float = -1,
        base_item: str = "",
        item_type: str = "",
        speed_type: str = "",
        speed_min: float = -1,
        speed_max: float = -1,
        size: str = "",
        creature_type: str = "",
        darkvision_min: float = -1,
        innate_spell: str = "",
        feat_type: str = "",
        feat_keywords: str = "",
        spell_keywords: str = "",
        class_role: str = "",
        class_keywords: str = "",
        subclass_keywords: str = "",
        race_keywords: str = "",
        background_keywords: str = "",
        spell_class: str = "",
        class_level: float = -1,
        opt_type: str = "",
        opt_prereq: str = "",
    ) -> str:
        """
        查询内置 DND 5e 知识库（法术/怪物/物品/专长/背景/职业/状态/种族）。

        【强制调用规则】只要玩家问及任何 D&D 5e 的规则数据——某个法术/戏法的
        效果、某个怪物的属性或能力、某件魔法物品、某职业或子职的能力、按条件
        筛选条目（如「挑战等级为 3 的龙类」「造成火焰伤害的怪物」「需要专注的
        3 环塑能法术」）——你必须调用本工具查询，禁止凭自己的记忆或推测回答，
        也不要用本工具之外的其他来源。本工具返回知识库原文，你必须严格依据其
        内容作答，不得编造工具未返回的信息；查询无结果时如实说明未找到。

        【禁止自行查询本地文件】本插件的知识库数据只允许通过本工具读取。
        绝对禁止用 python、grep、cat、find、文件搜索等任何方式直接读取或搜索
        本机的数据文件（包括插件的 dnd_kb.db 等）——那些是插件内部文件，
        格式与内容无任何稳定性保证，直接解析会得到过时或错误的答案。本工具
        的返回才是权威且唯一的查询接口。

        用法提示（大多数查询只需填 action/kind/name 三个参数，其余全部可选）：
        - 询问「X 的效果是什么」→ action=detail，kind=条目类型，name=名称。
        - 名称记不全 → action=search，name 传部分名称即可（支持中英文）。
        - 按条件筛选（如「挑战等级为 3 的龙类」「造成暗蚀伤害的武器」）→
          action=filter，kind 必填，条件参数按需填写（可组合，全部 AND）。
        - 询问某职业/子职的能力 → action=class_features，kind=职业。
          默认返回「分层概要总表」：按层级（第1~4层）分段，每行
          「N级 特性名：一句话概要」——足以回答「这个职业有什么能力、各是什么」。
        - 需要某职业本职特性的完整说明（而非概要）→ action=class_features，
          feature="*"（全部全文）或 feature=具体特性名（单个，跨版本全文）；
          只看某等级获得什么 → class_level=N（如「野蛮人 7 级获得什么」）。
        - 询问魔能祈唤/战技/超魔法/战斗风格等职业可定制选项 → action=detail
          （或 search/filter），kind=选项，name=选项名（如「苦痛魔爆」）；按
          类型反查 → action=filter，kind=选项，再加先决/类型条件参数
          （详见对应参数说明，如「先决条件 5 级的祈唤」）。

        Args:
            action(string): 要执行的操作。取值：detail=按 name 精确查询并返回同名
                全部版本（默认动作；同名不同版本的法术/条目会全部返回，标注
                [来源·版本]，2024 版优先展示，回答时须区分版本，勿混用两版数值）；
                search=按 name 模糊搜索返回候选列表（名称记不全时用）；
                filter=结构化筛选（需配合 kind 与筛选条件使用）；
                class_features=查询职业能力（kind=职业，name=职业名，subclass 可选）；
                version=查看知识库版本信息。
            kind(string): 条目类型，中文取值：法术/怪物/物品/专长/背景/职业/状态/种族/选项。
                选项 = 魔能祈唤/战技/超魔法/战斗风格等职业可定制选项（v0.50.0）。
            name(string): 条目名称（中文或英文均可）。detail/search/class_features
                必须提供。
            level(number): 法术环级（0=戏法），仅 filter+法术 时使用，不筛则 -1。
            school(string): 法术学派，中文取值：防护/咒法/预言/惑控/塑能/幻术/死灵/变化。
            cr_min(number): 挑战等级下限，仅 filter+怪物 时使用，不筛则 -1。
            cr_max(number): 挑战等级上限，仅 filter+怪物 时使用，不筛则 -1。
            monster_type(string): 怪物类型，中文取值：龙类/不死生物/异怪/野兽/构造体/
                元素/精类/邪魔/巨人/类人生物/怪物/泥怪/植物/天界生物。
            rarity(string): 物品稀有度，中文取值：普通/非普通/珍稀/极珍稀/传说/神器/
                非魔法物品（基础物品）/魔法物品（整体反查，稀有度非无）。
            subclass(string): 子职名，仅 class_features 时可选。
            feature(string): 本职特性细化，仅 class_features 时可选：传 "*" 返回该职业
                全部本职特性的完整说明（按层级分段）；传具体特性名（如 "施法"）只返回
                该特性跨版本的完整说明。不传则返回分层概要总表（第1~4层，每行
                「N级 特性名：一句话概要」），问「职业有什么能力」用默认即可。
            damage_type(string): 造成的伤害类型（仅 filter+怪物/法术/物品 时），中文取值：
                强酸/钝击/寒冷/火焰/力场/闪电/暗蚀/穿刺/毒素/心灵/光耀/挥砍/雷鸣。
                注意：kind=种族 时该参数表示天生抗性（对某伤害有抗性的种族）。
            condition(string): 施加的状态，中文取值：目盲/魅惑/耳聋/力竭/恐慌/受擒/
                失能/隐形/麻痹/石化/中毒/倒地/束缚/震慑/昏迷/疾病。
            environment(string): 怪物栖息环境，中文取值：极地/海岸/荒漠/森林/草地/
                丘陵/山地/沼泽/幽暗地域/城市/水下/位面/任何。
            weapon_property(string): 武器属性，中文取值：双手/弹药/扫射/灵巧/重型/
                轻型/装填/触及/弹容/特殊/投掷/多用。
            components(string): 法术成分，中文取值：言语/姿势/材料（每次传一个；
                需多个成分时用多次调用组合）。
            concentration(number): 法术专注筛选：1=仅专注法术，0=仅非专注，-1=不限。
            shape(string): 法术范围形状，中文取值：球形/锥形/线形/柱形/立方/弥漫/半球/特殊。
            target(string): 法术目标类型，中文取值：单体/多体/自我（按描述文本推断，
                可能有遗漏）。
            range_type(string): 法术距离类型，中文取值：触碰/自身/特殊。
            range_max(number): 法术最大距离（尺），仅筛距离不超过该值的法术，不筛则 -1。
            base_item(string): 基础物品名（仅物品），列出以该物品为原型的魔法物品
                （如 base_item=长剑 → 黎明使者/月刃等全部长剑系）。
            item_type(string): 物品大类（仅物品），中文取值：武器/盾牌/弹药/重甲/中甲/
                轻甲/法器/乐器/冒险装备/工具/赌具/火器/权杖/魔杖/戒指/药水/卷轴/坐骑/
                载具/舰船/飞艇/爆炸物/食物与饮料/骑乘配件/其他/宝石/艺术品/货币/
                贸易货物/贸易金属锭/船员/神器。
            speed_type(string): 种族速度类型（仅 filter+种族），中文取值：步行/攀爬/
                游泳/飞行/掘穴，缺省步行；与 speed_min/speed_max 组合指定该类型速度范围。
            speed_min(number): 速度下限（尺，仅 filter+种族），如「飞行速度至少 60」=
                speed_type=飞行 + speed_min=60；不筛则 -1。
            speed_max(number): 速度上限（尺，仅 filter+种族），不筛则 -1。
            size(string): 体型（仅 filter+种族），中文取值：微型/小型/中型/大型/巨型/超巨型。
            creature_type(string): 生物类型（仅 filter+种族），中文取值：类人生物/精类/
                亡灵/构造体/泥怪/异怪/野兽/天界生物/龙类/元素/邪魔/巨人/怪物/植物。
            darkvision_min(number): 黑暗视觉下限（尺，仅 filter+种族），如「黑暗视觉 60」=
                darkvision_min=60；1=只要有任何黑暗视觉；不筛则 -1。
            innate_spell(string): 天生施法法术名（仅 filter+种族），列出天生能施展该法术
                的种族（如 innate_spell=迷踪步）。
            feat_type(string): 专长类型（仅 filter+专长），中文取值：通用/起源/战斗风格/
                传奇恩惠/黑暗赠礼/龙纹。
            feat_keywords(string): 专长能力标签（仅 filter+专长），逗号分隔多个标签，
                取交集（AND）。标签覆盖攻击方式/输出/防御/机动/控场/施法/技能/探索等维度，
                常用词：近战/远程/徒手/投掷/双持/双手/重型/灵巧/轻型/触及/持盾、伤害/命中/
                重击/击杀/额外攻击/附赠动作/反应/借机攻击/先攻、防御/护甲/生命/治疗/豁免/
                抗性/减伤、速度/位移/跳跃/逃脱/潜行、击倒/束缚/减速/缴械、戏法/法术位/
                专注/法术攻击/仪式、专精/工具/隐匿/说服/侦查/负重/幸运/坐骑/同伴/狂暴等。
                例：找近战输出专长 → feat_keywords="近战,伤害"；找机动专长 → "机动"。
            spell_keywords(string): 法术能力标签（仅 filter+法术），逗号分隔多个标签，
                取交集（AND）。标签覆盖语义大类：控场（束缚/定身/减速/击倒/震慑）、
                伤害、治疗（治疗/回复/复活）、增益（护甲/优势/免疫/抗性/加速/祝福）、
                减益（诅咒/劣势/虚弱）、召唤（召唤/造物/仆从/同伴）、位移（传送/闪现/
                击退）、防护（护盾/结界/庇护）、侦查（侦测/探知/识破隐形）、潜行（隐形/
                隐匿）、社交（魅惑/说服）、探索（漂浮/飞行/开锁/传送门）、幻术（幻象）、
                即死、造物（创造/食物/饮水）、战斗辅助（武器/攻击/命中/豁免）、施法辅助
                （反制/解除/法术位）等。例：找 3 环控场法术 → level=3 +
                spell_keywords="控场"；找治疗法术 → "治疗"。
            class_role(string): 职业定位（仅 filter+职业），中文取值：武者/奥法/
                神职/专家（武者：战士/野蛮人/武僧；奥法：法师/术士/魔契师；神职：
                牧师/圣武士/德鲁伊；专家：游侠/游荡者/吟游诗人/奇械师）。
                例：找定位为武者的职业 → class_role="武者"。
            class_keywords(string): 职业能力标签（仅 filter+职业），逗号分隔多个
                标签，取交集（AND）。标签覆盖战斗方式（近战/远程/徒手/双持/双手/
                重甲/中甲/轻型护甲/持盾/坦克/爆发/先攻）、施法（施法/奥术施法/神术
                施法/自然施法/契约施法/仪式/戏法/专注/法术位/法术攻击/战斗施法）、
                辅助（治疗/增益/减益/防护/驱散/召唤/结界/圣疗/激励）、技能倾向
                （技能/隐匿/社交/探索/追踪/开锁/求生/驯兽/侦查/说服/威吓/巧手/
                奥秘/宗教/医药/自然）、属性依赖（力量/敏捷/体质/智力/感知/魅力）、
                特殊机制（狂暴/气力/偷袭/变形/魔宠/野兽伙伴/魔契/智械/炼金/枪械/
                灵能/龙纹/神术通道）等。例：找近战爆发职业 → class_keywords=
                "近战,爆发"；找治疗职业 → "治疗"。
            subclass_keywords(string): 子职能力标签（仅 filter+子职），逗号分隔
                多个标签，取交集（AND）。标签覆盖定位倾向（近战/远程/施法/坦克/
                治疗/辅助/控场/爆发/隐匿/召唤/变形/宠物/侦查/社交/探索/位移/结界/
                诅咒）、主题风味（神圣/黑暗/元素/自然/奥术/战术/龙/亡灵/恶魔/精类/
                巨人/鲜血/暗影/风暴/火焰/寒冰/闪电/雷鸣/强酸/毒素/光耀/生命/死亡/
                知识/战争/诡术/梦境/星辰/大地/海洋/月亮/孢子/野兽/植物/武器/格斗/
                箭术/瘟疫/鬼火）、特色机制（狂暴/偷袭/气力/灵感/魔宠/野兽伙伴/
                变形/魔契/龙纹/造物/亡者/附体/祝福/元素召唤/神圣通道/奥术传承/
                龙语/机关/枪械/炼金/灵能）等。例：找治疗向神圣子职 →
                subclass_keywords="治疗,神圣"；找法师塑能学派子职 → "塑能"。
            race_keywords(string): 种族能力标签（仅 filter+种族），逗号分隔多个
                标签，取交集（AND）。标签覆盖属性倾向（力量/敏捷/体质/智力/感知/
                魅力）、战斗方式（近战/远程/施法/坦克/爆发/隐匿/先攻/持盾/双持/
                徒手/武器熟练/天生武器）、防御生存（天生护甲/护甲/生命/再生/魔法
                抗性/豁免/坚韧/抗性）、机动（速度/飞行/游泳/攀爬/掘穴/跳跃/传送/
                位移/水陆两栖）、技能倾向（18 技能）、主题风味（元素/自然/龙/亡灵/
                恶魔/精类/巨人/暗影/神圣/黑暗/火焰/寒冰/闪电/雷鸣/强酸/毒素/光耀/
                星辰/大地/海洋/月亮/野兽/植物/真菌/机械/灵能/鲜血/吸血鬼/妖精）、
                特殊机制（变形/狂暴/坐骑/语言/多语言/黑暗视觉/天生施法/强力构筑/
                修整强化/日照敏感/隐形/长寿/不眠/魔宠/石化/诅咒/祝福/血统）等。
                例：找适合魅力角色的种族 → race_keywords="魅力"；找会变形且抗寒
                的种族 → "变形,寒冰"。
            background_keywords(string): 背景能力标签（仅 filter+背景），逗号分隔
                多个标签，取交集（AND）。标签覆盖属性倾向（力量/敏捷/体质/智力/
                感知/魅力）、技能倾向（18 技能，如 隐匿/说服/医药/调查）、身份
                主题（贵族/罪犯/士兵/学者/艺人/工匠/农民/水手/商人/隐士/教士/
                盗贼/佣兵/海盗/骑士/间谍/猎人/医生/律师/政客/赌徒/流浪儿/化外
                之民/冒险家/官员/特工/教徒/朝臣/学徒/守卫/恶棍/渔夫/考古学家/
                调查员/灵媒/幸存者/朝圣者/囚徒）、工具装备（赌具/乐器/工匠工具/
                盗贼工具/易容工具/文书伪造工具/草药工具/领航工具/毒药工具/铁匠
                工具/修补工具/木雕工具…17 工匠+kit）、特殊机制（起始专长/语言/
                多语言/声望/地位/财富/人脉/组织/公会/教团/学院/家族/骑士团/密探/
                伪装/补给/仆从/随从）等。例：找给隐匿熟练和盗贼工具的背景 →
                background_keywords="隐匿,盗贼工具"；找贵族身份背景 → "贵族"。
            spell_class(string): 职业名（仅 filter+法术），按职业法术表反查该职业
                可施展的法术（中英文均可，如 "法师"/"Wizard"）。例：找法师 3 环
                控制法术 → spell_class="法师" + level=3 + spell_keywords="控场"。
            class_level(number): 特性等级（仅 class_features），只返回该等级获得
                的职业特性全文（如想知道「野蛮人 7 级获得什么」→ action=class_features
                + name=野蛮人 + class_level=7）；不筛则 -1。
            opt_type(string): 选项类型（仅 filter+选项），中文取值：
                魔能祈唤/战技/超魔法/战斗风格/禁令恩惠/血咒（v0.50.3 新增
                后两者：邪狱使禁令恩惠、血猎手血咒）；如「战士有哪些战技可选」
                → action=filter + kind=选项 + opt_type=战技。
            opt_prereq(string): 选项先决条件关键词（仅 filter+选项，v0.50.0），
                子串匹配，如「先决 5 级的祈唤」→ opt_prereq=5级。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        action = (action or "detail").strip().lower()
        internal_kind = resolve_kind(kind) if kind else None

        if not self.kb_manager.available:
            return "知识库不可用（数据文件缺失，请重装插件）。"

        # --- version ---
        if action in ("version", "版本", "信息"):
            return KnowledgeBaseManager.format_version(self.kb_manager.version())

        # --- detail / search ---
        if action in ("detail", "search", "查询", "查", ""):
            q = (name or "").strip()
            if not q:
                return "请提供条目名称（name 参数）。"
            if action in ("search", "搜索", "模糊"):
                hits = self.kb_manager.search(q, kind=internal_kind)
                if not hits:
                    return f"未找到与「{q}」相关的条目。"
                return (
                    KnowledgeBaseManager.format_hits(hits)
                    + "\n" + NO_HALLUCINATION_NOTE
                )
            entries = self.kb_manager.detail(q, kind=internal_kind)
            if not entries:
                hits = self.kb_manager.search(q, kind=internal_kind)
                if len(hits) == 1:
                    entries = self.kb_manager.detail(hits[0].name, kind=internal_kind)
                elif hits:
                    return (
                        KnowledgeBaseManager.format_hits(hits)
                        + "\n" + NO_HALLUCINATION_NOTE
                    )
                else:
                    return f"未找到「{q}」相关条目。"
            return (
                self._kb_detail_text(internal_kind, entries)
                + "\n" + NO_HALLUCINATION_NOTE
            )

        # --- filter ---
        if action in ("filter", "筛选", "过滤"):
            if internal_kind is None:
                return "筛选查询需要提供 kind 参数（法术/怪物/物品/专长/背景/职业/状态/种族）。"
            if internal_kind not in ("spell", "monster", "item", "race", "feat",
                                     "class", "subclass", "background"):
                return (
                    f"「{kind}」暂不支持筛选，可用：法术/怪物/物品/种族/专长/"
                    "职业/子职/背景。"
                )
            # 特性标签：伤害/状态/环境/武器属性/形状/目标/成分/物品大类（解析为 canonical）。
            # kind=种族 时 damage_type 语义为「天生抗性」（dmg_resist，非造成伤害），
            # 且仅种族参数（size/creature_type/innate_spell）在 race 语境生效。
            tags: list[tuple[str, str]] = []
            for facet, val in (
                ("dmg_dealt", (
                    resolve_damage_type(damage_type)
                    if internal_kind != "race" else None
                )),
                ("condition_inflict", resolve_condition(condition)),
                ("environment", resolve_environment(environment)),
                ("weapon_property", resolve_property(weapon_property)),
                ("spell_shape", resolve_shape(shape)),
                ("spell_target", resolve_target(target)),
                ("spell_component", resolve_component(components)),
                ("item_type", resolve_item_type(item_type)),
            ):
                if val:
                    tags.append((facet, val))
            if internal_kind == "race":
                for facet, val in (
                    ("dmg_resist", resolve_damage_type(damage_type)),
                    ("size", resolve_size(size)),
                    ("creature_type", resolve_creature_type(creature_type)),
                ):
                    if val:
                        tags.append((facet, val))
            # 基础物品名 / 天生施法法术名是自由文本（不经枚举解析）
            if (base_item or "").strip():
                tags.append(("base_item", base_item.strip()))
            if internal_kind == "race" and (innate_spell or "").strip():
                tags.append(("innate_spell", innate_spell.strip()))
            # 专长：类型 + 能力标签（v0.26.0；标签逗号分隔取交集，别名归一）
            if internal_kind == "feat":
                if (feat_type or "").strip():
                    ft = resolve_feat_type(feat_type.strip())
                    if ft:
                        tags.append(("feat_type", ft))
                for kw in (
                    (kw.strip())
                    for kw in (feat_keywords or "").split(",")
                    if kw.strip()
                ):
                    tags.append(("feat_keyword", resolve_feat_keyword(kw) or kw))
            # 法术：能力标签（v0.27.0；逗号分隔取交集，别名归一）
            if internal_kind == "spell":
                for kw in (
                    (kw.strip())
                    for kw in (spell_keywords or "").split(",")
                    if kw.strip()
                ):
                    tags.append(("spell_keyword", resolve_spell_keyword(kw) or kw))
            # 职业：定位 + 能力标签（v0.33.0；逗号分隔取交集，别名归一）
            if internal_kind == "class":
                if (class_role or "").strip():
                    rl = resolve_class_role(class_role.strip())
                    if rl:
                        tags.append(("class_role", rl))
                for kw in (
                    (kw.strip())
                    for kw in (class_keywords or "").split(",")
                    if kw.strip()
                ):
                    tags.append(("class_keyword", resolve_class_keyword(kw) or kw))
            # 子职：能力标签（v0.33.0；逗号分隔取交集，别名归一）
            if internal_kind == "subclass":
                for kw in (
                    (kw.strip())
                    for kw in (subclass_keywords or "").split(",")
                    if kw.strip()
                ):
                    tags.append((
                        "subclass_keyword",
                        resolve_subclass_keyword(kw) or kw,
                    ))
            # 种族：能力标签（v0.34.0；逗号分隔取交集，别名归一）
            if internal_kind == "race":
                for kw in (
                    (kw.strip())
                    for kw in (race_keywords or "").split(",")
                    if kw.strip()
                ):
                    tags.append(("race_keyword", resolve_race_keyword(kw) or kw))
            # 背景：能力标签（v0.34.0；逗号分隔取交集，别名归一）
            if internal_kind == "background":
                for kw in (
                    (kw.strip())
                    for kw in (background_keywords or "").split(",")
                    if kw.strip()
                ):
                    tags.append((
                        "background_keyword",
                        resolve_background_keyword(kw) or kw,
                    ))
            # 选项：类型/先决条件（v0.50.0）
            if internal_kind == "optionalfeature":
                if (opt_type or "").strip():
                    ot = OPTIONAL_FEATURE_TYPE_CN.get(opt_type.strip())
                    if not ot:
                        ot = next(
                            (v for k, v in OPTIONAL_FEATURE_TYPE_CN.items()
                             if v == opt_type.strip()),
                            "",
                        )
                    if ot:
                        tags.append(("feature_type", ot))
                if (opt_prereq or "").strip():
                    tags.append(("prerequisite", f"%{opt_prereq.strip()}%"))
            # 法术：职业法术表反查（v0.35.0；spell_class 中英文职业名均支持）
            resolved_spell_class = ""
            if internal_kind == "spell" and (spell_class or "").strip():
                resolved_spell_class = self._resolve_class_cn(spell_class)
                if not resolved_spell_class:
                    return (
                        f"知识库中找不到职业「{spell_class}」，"
                        "请用 action=class_features 查询确认职业名。"
                    )
            result = self.kb_manager.filter(
                kind=internal_kind,
                level=int(level) if level is not None and level >= 0 else None,
                school=resolve_school(school) if school else None,
                cr_min=cr_min if cr_min is not None and cr_min >= 0 else None,
                cr_max=cr_max if cr_max is not None and cr_max >= 0 else None,
                mtype=resolve_monster_type(monster_type) if monster_type else None,
                rarity=resolve_rarity(rarity) if rarity else None,
                concentration=(
                    bool(concentration) if concentration in (0, 1) else None
                ),
                range_type=range_type if range_type else None,
                range_max=(
                    int(range_max) if range_max is not None and range_max >= 0 else None
                ),
                speed_type=resolve_speed_type(speed_type) if speed_type else None,
                speed_min=(
                    int(speed_min) if speed_min is not None and speed_min >= 0 else None
                ),
                speed_max=(
                    int(speed_max) if speed_max is not None and speed_max >= 0 else None
                ),
                darkvision_min=(
                    int(darkvision_min)
                    if darkvision_min is not None and darkvision_min >= 0
                    else None
                ),
                tags=tags or None,
                spell_class=resolved_spell_class or None,
            )
            if not result.entries:
                return "未找到符合条件的条目，可放宽筛选条件后再试。"
            blocks = [KnowledgeBaseManager.format_entry(e) for e in result.entries]
            return (
                f"找到 {result.total} 条符合条件的条目，仅显示前"
                f" {len(result.entries)} 条：\n"
                + "\n\n".join(blocks)
                + "\n" + NO_HALLUCINATION_NOTE
            )

        # --- class_features ---
        if action in ("class_features", "职业", "职业能力"):
            cn = (name or "").strip()
            if not cn:
                return "职业查询需要提供职业名（name 参数），如 name=战士。"
            feat = (feature or "").strip() or None
            lvl = int(class_level) if class_level is not None and class_level >= 0 else 0
            if feat is not None:
                result = self.kb_manager.class_features(
                    cn, feature=feat, level_min=lvl, level_max=lvl
                )
                if not result.base_rows:
                    hits = self.kb_manager.search(cn, kind="class")
                    if len(hits) == 1:
                        result = self.kb_manager.class_features(
                            hits[0].name, feature=feat, level_min=lvl, level_max=lvl
                        )
                    elif hits:
                        return (
                            KnowledgeBaseManager.format_hits(hits)
                            + "\n" + NO_HALLUCINATION_NOTE
                        )
                    else:
                        return f"未找到职业「{cn}」。"
                return (
                    KnowledgeBaseManager.format_class_features(result)
                    + "\n" + NO_HALLUCINATION_NOTE
                )
            result = self.kb_manager.class_features(
                cn, subclass or None, level_min=lvl, level_max=lvl
            )
            has_data = (
                bool(result.base_rows)
                or bool(result.subclass_rows)
                or bool(result.subclass_candidates)
            )
            if not has_data:
                hits = self.kb_manager.search(cn, kind="class")
                if len(hits) == 1:
                    result = self.kb_manager.class_features(
                        hits[0].name, subclass or None, level_min=lvl, level_max=lvl
                    )
                elif hits:
                    return (
                        KnowledgeBaseManager.format_hits(hits)
                        + "\n" + NO_HALLUCINATION_NOTE
                    )
                else:
                    return f"未找到职业「{cn}」。"
            return (
                KnowledgeBaseManager.format_class_features(result)
                + "\n" + NO_HALLUCINATION_NOTE
            )

        return (
            "未知的 action。可用值：detail / search / filter / class_features / version。"
        )

    def _resolve_class_cn(self, value: str) -> str:
        """职业名（中文/英文）→ 库内中文职业名（职业法术表 spell_class 用）。"""
        v = (value or "").strip()
        if not v:
            return ""
        hits = self.kb_manager.search(v, kind="class")
        if not hits:
            return ""
        v_low = v.lower()
        for h in hits:
            if h.name == v or (h.eng_name or "").lower() == v_low:
                return h.name
        return hits[0].name

    # ------------------------------------------------------------------
    # v0.35.0 构筑咨询（advise_build）：第 8 个 LLM 工具。
    #
    # 幻觉控制只在输入侧：候选档案全部由代码从知识库确定性组装（build_advisor），
    # LLM 只负责基于档案组织话术；docstring 与 _llm_request_guard 明文禁止
    # 凭记忆补充条目名。level_up 由插件直读活跃角色卡（LLM 零传参）。
    # ------------------------------------------------------------------

    @filter.llm_tool(name="advise_build")
    async def advise_build_tool(
        self,
        event: AstrMessageEvent,
        action: str = "new_build",
        goal: str = "",
        keywords: str = "",
        level: float = -1,
    ) -> str:
        """
        构筑咨询：按玩家目标/角色卡现状，从知识库确定性组装「候选档案」
        （职业/种族/背景/子职/专长/法术/升级特性时间线），供你组织推荐话术。
        适用于三类提问：①「帮我车一个 15 级前排打手」②「以上是我的角色背景，
        帮我想想适合什么构筑」③「我要升到 7 级了，看看我的卡，推荐点什么」。

        【防幻觉守则】档案里出现的所有条目名（种族/职业/专长/法术等）都是
        知识库真实存在的；你的推荐必须完全基于本工具返回的档案，禁止凭记忆
        添加档案中没有的条目名，也禁止编造条目的效果/前置条件。玩家要求查看
        某个条目的全文时，先调 query_dnd_knowledge 的 detail 取原文。

        用法：
        - 从零构筑（含「根据背景推荐」）：action=new_build，goal 写玩家的目标
          短语（如 "前排打手" "治疗辅助"），keywords 可补精确标签（如
          "坦克,近战"），level 为目标等级。根据背景推荐时，先理解背景，把
          主题提炼成 goal/keywords 传入。
        - 升级建议：action=level_up。插件自动读取玩家当前活跃角色卡并计算
          下一级特性时间线、专长候选（带前置条件标注 ✅/❌/⚠️）与法术建议，
          你无需传任何卡面数据。无活跃卡时工具会提示先车卡或切换。

        Args:
            action(string): 要执行的操作。取值：new_build=从零构筑（默认）；
                level_up=基于活跃角色卡的升级建议。
            goal(string): 构筑目标自由文本，如"前排打手""治疗辅助"；代码会做
                别名归一与标签消歧（"前排"→坦克），整词未命中会做中文复合词
                抽取。根据背景推荐时先提炼背景主题传入。
            keywords(string): 精确能力标签补充（逗号分隔，可选），直接映射到
                知识库词表（如 "坦克,近战" "控场,治疗"），与 goal 取并集。
            level(number): 目标等级（仅 new_build，1-20；不填则读群规则起始
                等级）。<19 时自动排除传奇恩惠专长。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        if not self.kb_manager.available:
            return "知识库不可用（数据文件缺失，请重装插件）。"
        act = (action or "new_build").strip().lower()

        if act in ("level_up", "升级", "升级建议"):
            card = await self.character_manager.get_card(event)
            if card is None:
                return (
                    "未找到活跃角色卡。请先引导玩家车卡"
                    "（guide_chargen）或用 /卡 切换到已有角色卡，"
                    "再调用本工具生成升级建议。"
                )
            try:
                dossier = assemble_level_up(card, self.kb_manager)
            except Exception as e:  # noqa: BLE001 - 工具容错，回读卡失败不崩
                logger.warning(f"[trpg_assistant] advise_build level_up 失败: {e}")
                return "读取角色卡失败，请稍后再试或检查角色卡数据。"
            return dossier_to_text(dossier) + "\n" + self._advise_guard_note()

        # new_build（默认）
        rule = await self.chargen_manager.get_rule(event)
        edition = getattr(rule, "edition", "") or ""
        target_level = int(level) if level is not None and level >= 0 else 0
        if not target_level:
            target_level = int(getattr(rule, "starting_level", 1) or 1)
        try:
            dossier = assemble_new_build(
                goal or "", keywords or "", self.kb_manager,
                edition=edition, level=target_level,
            )
        except Exception as e:  # noqa: BLE001 - 工具容错
            logger.warning(f"[trpg_assistant] advise_build new_build 失败: {e}")
            return "构筑档案组装失败，请稍后再试。"
        return dossier_to_text(dossier) + "\n" + self._advise_guard_note()

    @staticmethod
    def _advise_guard_note() -> str:
        """构筑咨询守则：追加到 advise_build 返回末尾，防 LLM 凭记忆补条目。"""
        return (
            "【守则】以上条目全部来自知识库。推荐必须基于本档案，禁止凭记忆"
            "补充档案中没有的条目名或效果；玩家确认构筑后，可用 guide_chargen "
            "的 start 预填职业/种族/背景（race/class_name/background 参数）"
            "直接开始车卡。"
        )

    # ------------------------------------------------------------------
    # 私设助手（manage_homebrew）：转录 / 写入 / 点评（v0.37.0）
    # ------------------------------------------------------------------

    @filter.llm_tool(name="manage_homebrew")
    async def manage_homebrew_tool(
        self,
        event: AstrMessageEvent,
        action: str = "convert",
        json_text: str = "",
        filename: str = "",
        overwrite: bool = False,
        merge: bool = False,
    ) -> str:
        """
        管理 DND 私设（homebrew）内容：把 DM 口述/草稿的私设条目转录为合法
        私设 JSON、校验后写入私设目录、或对私设草稿做对照点评。当 DM 说
        「帮我记一个私设法术/怪物」「把这段房规转成 JSON」「点评一下这个私设」
        时调用。

        【生成格式约定】输出简化格式 JSON（条目带 kind/name/source/body 字段，
        kind ∈ spell/monster/item/feat/background/condition/race/class/subclass，
        中文类型名亦可）；DM 文本中能确定的结构化字段尽量填（法术 level/school、
        物品 rarity、怪物 cr 等），以保留筛选与锚点能力。source 建议用自定义
        来源码（如 DM 名/团名缩写），禁止冒用官方来源码（如 PHB/DMG/XGE）——
        source 与官方相同会覆盖官方条目。

        【写入安全约定】action=write 需要插件配置开启「私设写入」且调用者在
        白名单或为管理员；目标文件已存在时默认拒绝并返回现有条目清单，必须先
        向 DM 确认，再用 overwrite=true（整文件替换）或 merge=true（按
        kind+name+source 合并、同键新盖旧）重试。

        【点评强制规则】action=review 点评前，必须先用 query_dnd_knowledge
        查询与草稿同类型的官方条目做对照（本工具会返回锚点与同名命中提示），
        禁止仅凭记忆点评。

        【长度提示】convert 校验通过后会全文贴回 JSON；文本超过约 1200 字符时，
        建议 DM 开启写入直接落盘，或分条目分批转换。

        Args:
            action(string): 操作。convert=校验并转录 JSON（默认，双程校验的
                第二程：把你生成的 JSON 原样回传，返回 通过/条目数/逐条告警与
                错误）；write=校验并写入私设目录（落盘后自动重载生效）；
                review=解析草稿返回结构化锚点（kind/name/source/环级/稀有度/CR
                等 + 同名条目命中），供你对照官方条目点评。
            json_text(string): 私设 JSON 文本。convert/write 必填；review 传
                草稿全文（允许是不完全合法的 JSON 或纯文本，解析失败时按纯文本
                点评模式处理）。
            filename(string): write 专用。目标文件名（仅允许 .json，含路径
                分隔符会被拒绝）；留空时从条目 source 派生安全文件名。
            overwrite(boolean): write 专用。目标已存在且 DM 已确认时整文件
                替换。与 merge 互斥。
            merge(boolean): write 专用。目标已存在且 DM 已确认时按
                (kind,name,source) 合并，同键新盖旧、不同键追加。与 overwrite
                互斥。
        """
        event = _resolve_event(event)
        if event is None:
            return "工具上下文解析失败：当前 AstrBot 版本注入的事件对象不兼容，请升级插件。"
        act = (action or "convert").strip().lower()
        if act in ("convert", "转录", "转换"):
            return self._homebrew_convert(json_text)
        if act in ("write", "写入"):
            return await self._homebrew_write(
                event, json_text, filename, overwrite, merge
            )
        if act in ("review", "点评"):
            return self._homebrew_review(json_text)
        return "未知的 action。可用值：convert（转录校验）/ write（写入私设目录）/ review（对照点评）。"

    @staticmethod
    def _homebrew_usage() -> str:
        """manage_homebrew 空输入时的用法提示（含最小简化格式示例）。"""
        return (
            "用法：把私设 JSON 作为 json_text 传入。最小简化格式示例：\n"
            '[{"kind": "spell", "name": "示例法术", "source": "我的团", '
            '"level": 1, "body": "1 环塑能系，30 尺内一点火光……"}]\n'
            "kind 支持 spell/monster/item/feat/background/condition/race/"
            "class/subclass（中文类型名亦可）。"
        )

    @staticmethod
    def _format_validation_issues(v) -> list[str]:
        """校验结果的 errors/warnings → 展示行（convert/write 共用）。"""
        lines = [f"错误：{e}" for e in v.errors]
        lines += [f"警告：{w}" for w in v.warnings]
        return lines

    def _format_override_note(self, override_keys) -> list[str]:
        """撞键提示行（与官方 (kind,name,source) 相同的条目会覆盖官方）。"""
        if not override_keys:
            return []
        lines = [
            "注意：以下条目与官方 (类型,名称,来源) 撞键，"
            "写入后将作为房规覆盖官方条目："
        ]
        for kind, name, source in override_keys[:10]:
            lines.append(f"- {KIND_LABEL.get(kind, kind)}「{name}」({source})")
        return lines

    def _homebrew_convert(self, json_text: str) -> str:
        """convert：双程校验第二程（LLM 生成的 JSON 回传 → 权威校验回执）。"""
        text = (json_text or "").strip()
        if not text:
            return self._homebrew_usage()
        v = validate_homebrew_text(text, self.kb_manager.official_key_set())
        if not v.ok or not v.entries:
            lines = ["校验失败：私设 JSON 未通过权威解析（未贴回全文，请修订后重新 convert）。"]
            lines += self._format_validation_issues(v)
            if v.ok and not v.entries:
                lines.append("错误：无可加载条目（条目可能全部缺少 name/kind 或正文为空）。")
            return "\n".join(lines)
        lines = [f"校验通过：{len(v.entries)} 条私设条目（全文 {len(text)} 字符）。"]
        lines += self._format_override_note(v.override_keys)
        lines += self._format_validation_issues(v)
        lines.append("--- JSON 全文 ---")
        lines.append(text)
        if len(text) > 1200:
            lines.append(
                "提示：文本较长，群聊贴出可能被平台截断；建议 DM 开启私设写入"
                "直接落盘，或分条目分批转换。"
            )
        return "\n".join(lines)

    async def _homebrew_write(
        self,
        event: AstrMessageEvent,
        json_text: str,
        filename: str,
        overwrite: bool,
        merge: bool,
    ) -> str:
        """write：配置闸→权限闸→目录闸→校验→冲突协议→原子写→reload。"""
        if not self.homebrew_write_enabled:
            return (
                "私设写入未开启：请在 WebUI 插件配置中开启 "
                "homebrew_write_enabled 后重试（关闭时可用 convert 输出 JSON "
                "由 DM 自行放入 trpg_homebrew/）。"
            )
        if not await self._check_homebrew_write_permission(event):
            return "权限不足：写入私设需要白名单用户或管理员身份（私聊同样受限）。"
        hb_dir = self.kb_manager.homebrew_dir
        if hb_dir is None:
            return "当前环境无私设目录（data_dir 不可用），无法写入；可改用 convert 输出 JSON。"
        if overwrite and merge:
            return "参数错误：overwrite 与 merge 互斥，只能二选一。"
        text = (json_text or "").strip()
        if not text:
            return self._homebrew_usage()
        official = self.kb_manager.official_key_set()
        v = validate_homebrew_text(text, official)
        if not v.ok or not v.entries:
            lines = ["校验失败：私设 JSON 未通过权威解析，未落盘。"]
            lines += self._format_validation_issues(v)
            if v.ok and not v.entries:
                lines.append("错误：无可加载条目（条目可能全部缺少 name/kind 或正文为空）。")
            return "\n".join(lines)
        if filename.strip():
            safe_name = sanitize_filename(filename)
            if safe_name is None:
                return "文件名非法：仅允许 .json 文件名，禁止路径分隔符与「..」。"
        else:
            safe_name = derive_filename([e.source for e in v.entries])
        async with self._homebrew_write_lock:
            target = hb_dir / safe_name
            if target.exists() and not (overwrite or merge):
                try:
                    existing = flatten_raw_entries(
                        json.loads(target.read_text(encoding="utf-8"))
                    )
                except Exception:  # noqa: BLE001 - 旧文件损坏也要能列冲突
                    existing = []
                lines = [f"冲突：文件 {safe_name} 已存在，含以下 {len(existing)} 条私设："]
                for e in existing[:20]:
                    lines.append(
                        f"- {KIND_LABEL.get(e.kind, e.kind)}「{e.name}」({e.source})"
                    )
                if len(existing) > 20:
                    lines.append(f"- …等共 {len(existing)} 条")
                lines.append(
                    "请向 DM 确认后以 overwrite=true（整文件替换）或 "
                    "merge=true（按 kind+name+source 合并，同键新盖旧）重新调用。"
                )
                return "\n".join(lines)
            mode = "created"
            payload = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            entries_n = len(v.entries)
            if merge and target.exists():
                merged = merge_homebrew_texts(target.read_text(encoding="utf-8"), text)
                v2 = validate_homebrew_text(merged, official)
                if not v2.ok or not v2.entries:
                    lines = ["合并后校验失败，未落盘："]
                    lines += self._format_validation_issues(v2)
                    return "\n".join(lines)
                payload, entries_n, mode = merged, len(v2.entries), "merged"
            elif overwrite and target.exists():
                mode = "overwritten"
            atomic_write_text(target, payload)
            result = self.kb_manager.reload_homebrew()
        mode_cn = {"created": "新建", "overwritten": "整文件替换", "merged": "合并"}[mode]
        lines = [f"已写入 {safe_name}（{mode_cn}），本文件 {entries_n} 条私设。"]
        lines += self._format_override_note(v.override_keys)
        lines.append(
            f"私设已重载：共 {result.entries} 条、覆盖官方 {result.overrides} 条。"
        )
        for e in result.errors[:5]:
            lines.append(f"重载错误：{e}")
        for w in result.warnings[:5]:
            lines.append(f"重载警告：{w}")
        return "\n".join(lines)

    # review 锚点摘录时各 kind 关心的侧表字段。
    _REVIEW_SIDE_KEYS = {
        "spell": ("level", "school", "components", "ritual", "concentration"),
        "monster": ("cr", "mtype", "size"),
        "item": ("rarity", "attunement", "value_cp", "weight_lb", "dmg1"),
        "race": ("speed_walk", "speed_climb", "speed_swim", "speed_fly",
                 "speed_burrow", "darkvision"),
        "feat": ("feat_type", "ability_increase"),
        "background": ("ability",),
    }

    # review 返回尾部固定追加的强制查库句（docstring 与守则之外的第三道保险）。
    _REVIEW_QUERY_MANDATE = (
        "点评要求：请先用 query_dnd_knowledge 查询上述锚点同类型（kind）的"
        "官方条目做对照后再点评；禁止仅凭记忆点评。"
    )

    def _homebrew_review(self, json_text: str) -> str:
        """review：只解析草稿给锚点+同名命中，对照取料由 LLM 自主查库。"""
        text = (json_text or "").strip()
        if not text:
            return self._homebrew_usage()
        candidate = text
        m = re.match(r"^```(?:json)?\s*\n(?P<body>.*?)\n?```\s*$", text, re.DOTALL)
        if m:
            candidate = m.group("body")
        try:
            raw = json.loads(candidate)
        except json.JSONDecodeError:
            return "草稿不是可解析的 JSON，已切换纯文本点评模式（无锚点）。\n" + self._REVIEW_QUERY_MANDATE
        raws = flatten_raw_entries(raw)
        if not raws:
            return "草稿可解析但未识别到任何私设条目（缺 kind/name）。\n" + self._REVIEW_QUERY_MANDATE
        v = validate_homebrew_text(candidate, self.kb_manager.official_key_set())
        by_key = {(e.kind, e.name, e.source): e for e in v.entries}
        lines = [f"已解析草稿：{len(raws)} 条私设条目，锚点如下："]
        for r in raws:
            entry = by_key.get((r.kind, r.name, r.source))
            anchor = f"- {KIND_LABEL.get(r.kind, r.kind)}「{r.name}」({r.source})"
            if entry is not None and entry.side:
                keys = self._REVIEW_SIDE_KEYS.get(r.kind, ())
                frag = ", ".join(
                    f"{k}={entry.side[k]}" for k in keys if k in entry.side
                )
                if frag:
                    anchor += f"：{frag}"
            lines.append(anchor)
            try:
                hits = self.kb_manager.search(r.name, kind=r.kind, limit=3)
            except Exception:  # noqa: BLE001 - 查库失败不阻断点评
                hits = []
            if hits:
                lines.append("  同名/近似命中：")
                for h in hits[:3]:
                    lines.append(
                        f"  - {h.name}（{h.edition_label}）{h.homebrew_label}"
                    )
        issues = self._format_validation_issues(v)
        if issues:
            lines.append("草稿校验提示（不影响点评）：")
            lines += issues[:5]
        lines.append(self._REVIEW_QUERY_MANDATE)
        return "\n".join(lines)


    # ------------------------------------------------------------------
    # 插件卸载
    # ------------------------------------------------------------------

    async def terminate(self) -> None:
        logger.info("[trpg_assistant] 跑团助手插件已卸载。")

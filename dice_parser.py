"""
dice_parser.py — DnD 骰池表达式解析器。

支持语法（大小写不敏感）：
  基础:               d20, 1d20, 2d6+5, d8-1
  FATE/Fudge 骰:      4dF
  保留最高/最低:      4d6kh3, 8d100k4, 2d20kl1
  丢弃最低/最高:      8d6d3 (dl3), 8d6dl3, 8d6dh3
  优势/劣势:          d20adv, d20dis  （2d20kh1 / 2d20kl1 的语法糖）
  标准爆炸骰:         d6!, 2d10!>4 (>=4 爆炸), d6!3 (=3 爆炸)
  复合爆炸骰:         5d6!! (Shadowrun 风格)
  穿透爆炸骰:         5d6!p (HackMaster 风格)
  目标数成功计数:     3d6>3, 10d6<4
  失败计数附加:       3d6>3f1, 10d6<4f>5
  重骰:               2d8r<2, 8d6r, 2d6ro<2 (只重骰一次)
  排序:               8d6s (升序), 8d6sd (降序)
  多骰组:             2d6+1d4+3
  标签:               1d20+5 攻击检定  或  1d20+5#攻击检定
  复合修正:           2d6+1d4+3-1d2
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class DiceParseError(ValueError):
    """骰池表达式无法解析时抛出。"""


# ---------------------------------------------------------------------------
# 解析器产出的数据结构
# ---------------------------------------------------------------------------


@dataclass
class RerollCondition:
    """单条重骰条件，例如 r<2 或 ro=1。"""

    compare: str  # ">" / "<" / "="
    value: int
    once: bool = False  # True = ro（只重骰一次），False = r（循环）


@dataclass
class DiceGroup:
    """单组骰子，例如 4d6kh3 或 d20!。"""

    count: int  # 骰子数量
    sides: int  # 每个骰子的面数（0 代表 FATE 骰）
    fate: bool = False  # 是否为 FATE/Fudge 骰（dF）
    keep_mode: str | None = None  # "kh"（保留最高）或 "kl"（保留最低）
    keep_n: int | None = None  # 保留几个
    drop_mode: str | None = None  # "dl"（丢弃最低）或 "dh"（丢弃最高）
    drop_n: int | None = None  # 丢弃几个
    # --- 爆炸相关 ---
    exploding: bool = False  # 是否为爆炸骰
    explode_mode: str = "standard"  # "standard"(!), "compound"(!!), "penetrate"(!p)
    explode_compare: str | None = None  # ">" / "<" / "=" (None = 等于最大值)
    explode_value: int | None = None  # 自定义爆炸阈值
    # --- 成功/失败计数 ---
    success_compare: str | None = None  # ">" / "<" / "="
    success_value: int | None = None
    failure_compare: str | None = None  # ">" / "<" / "="
    failure_value: int | None = None
    # --- 重骰 ---
    reroll_conditions: list[RerollCondition] = field(default_factory=list)
    # --- 排序 ---
    sort_order: str | None = None  # "asc" / "desc"
    # --- 符号哨兵（内部使用）---
    modifier: int = 0  # -1 = 哨兵：该组取反
    # --- 骰数/骰面位置的括号算式（v0.47.0）---
    count_src: str | None = None  # 骰数位置原始算式文本，如 "(2+3)"；None = 字面整数
    sides_src: str | None = None  # 骰面位置原始算式文本，如 "(2*4)"
    count_expr: object | None = None  # 骰数位置括号子表达式 AST（求值期使用）
    sides_expr: object | None = None  # 骰面位置括号子表达式 AST


@dataclass
class ParsedExpression:
    """完整的骰池表达式解析结果。"""

    groups: list[DiceGroup] = field(default_factory=list)
    flat_modifier: int = 0  # 所有 token 累计的平坦整数修正值
    label: str = ""  # 可选标签/说明
    dc: int | None = None  # 难度等级（Difficulty Class），如 /r d20 感知 15 中的 15
    repeat: int = 1  # 多重投掷次数（N#expr，>=1）；1 = 单次投掷
    ast: object | None = None  # 复杂公式 AST（v0.47.0）；None = 扁平 +/- 和路径


# ---------------------------------------------------------------------------
# 表达式树节点（v0.47.0：四则运算 + 括号）
# ---------------------------------------------------------------------------


@dataclass
class ConstNode:
    """常数叶子，如 '3'。"""

    value: int


@dataclass
class DiceNode:
    """骰组叶子，包装一个 DiceGroup（骰数/骰面可能含括号算式）。"""

    group: DiceGroup


@dataclass
class BinOpNode:
    """二元运算节点：op ∈ {'+', '-', '*', '/'}。"""

    op: str
    left: object  # ExprNode
    right: object  # ExprNode


@dataclass
class NegNode:
    """一元负号节点。"""

    operand: object  # ExprNode


@dataclass
class GroupNode:
    """括号分组节点（保留显式括号结构，用于计数组限根检测与回显）。"""

    child: object  # ExprNode


ExprNode = ConstNode | DiceNode | BinOpNode | NegNode | GroupNode


# ---------------------------------------------------------------------------
# 词法分析助手
# ---------------------------------------------------------------------------

# 平坦整数 token（用于位置感知匹配）
_INT_TOKEN_RE = re.compile(r"\d+")

# 全角 → ASCII 转换表：防止全角符号（如 ＋）被误判为非 ASCII 标签分隔符。
# 含全角井号 ＃（U+FF03）→ '#'，使「3＃d20」等多重投掷写法也可识别。
_FULLWIDTH_TABLE = str.maketrans(
    "＋－＊／（）＃０１２３４５６７８９",
    "+-*/()#" + "0123456789",
)

# 单次解析允许的最大输入长度（字符数）。
# 提升为模块级常量，便于系统管理员在配置层或子类中覆盖。
_MAX_INPUT_LEN: int = 200


def _normalize_fullwidth(s: str) -> str:
    """
    将常见全角算术字符规范化为 ASCII 等价形式。

    例如：用户输入 d20＋5，＋（U+FF0B）属非 ASCII，不进行此归一化则
    _strip_label 会在 ＋ 处截断，把 "+5" 误判成标签而改变掷骰逻辑。
    """
    return s.translate(_FULLWIDTH_TABLE)


def _read_int(s: str, pos: int) -> tuple[int | None, int]:
    """尝试在 pos 处读取非负整数，返回 (value, new_pos) 或 (None, pos)。"""
    m = _INT_TOKEN_RE.match(s, pos)
    if m:
        return int(m.group(0)), m.end()
    return None, pos


def _read_compare_point(s: str, pos: int) -> tuple[str | None, int | None, int]:
    """
    读取可选的比较点（ComparePoint）：[>|<|=]N 或 仅 N（默认 =）。
    返回 (compare_op, value, new_pos)；若无有效数字则返回 (None, None, pos)。
    """
    if pos >= len(s):
        return None, None, pos
    start = pos
    compare = "="
    if s[pos] in (">", "<", "="):
        compare = s[pos]
        pos += 1
    val, new_pos = _read_int(s, pos)
    if val is None:
        # 消耗了比较符（含 '='）但没有数字 → 整体回退到消耗前位置，
        # 让残余字符留给上层报错，避免 'd6!=' 被静默当作 'd6!'。
        return None, None, start
    return compare, val, new_pos


# ---------------------------------------------------------------------------
# 骰子 token 子解析器（各负责一类修饰，就地修改 DiceGroup）
# ---------------------------------------------------------------------------


def _parse_keep_drop(expr: str, pos: int, group: DiceGroup) -> int:
    """
    解析可选的 keep / drop / adv / dis 修饰符，就地更新 group。

    支持：kh / kl / k（保留）、dh / dl / d（丢弃）、adv / dis 语法糖。
    """
    n = len(expr)
    if pos >= n:
        return pos
    ch = expr[pos].lower()
    if ch == "k":
        pos += 1
        if pos < n and expr[pos].lower() == "h":
            pos += 1
            kn, pos = _read_int(expr, pos)  # type: ignore[assignment]
            group.keep_mode = "kh"
            group.keep_n = kn if kn is not None else 1
        elif pos < n and expr[pos].lower() == "l":
            pos += 1
            kn, pos = _read_int(expr, pos)  # type: ignore[assignment]
            group.keep_mode = "kl"
            group.keep_n = kn if kn is not None else 1
        else:
            kn, pos = _read_int(expr, pos)  # type: ignore[assignment]
            group.keep_mode = "kh"
            group.keep_n = kn if kn is not None else 1
    elif ch == "d":
        # 先检查 'dis' 语法糖，避免被 drop 分支误匹配
        if pos + 3 <= n and expr[pos : pos + 3].lower() == "dis":
            # 劣势固定为 2d20kl1：显式骰数 >2 时静默覆盖会掩盖用户意图，报错提示。
            if group.count > 2:
                raise DiceParseError(
                    f"优势/劣势（adv/dis）固定为 2 颗骰子，骰数 {group.count} 无效；"
                    "直接写 d20dis 即可"
                )
            group.count = 2
            group.keep_mode = "kl"
            group.keep_n = 1
            pos += 3
        else:
            # 丢弃：需要 dl/dh/d(数字) 三种情况
            # 避免误把下一骰组的 'd' 消耗掉：必须有 h/l 或紧跟数字
            peek = pos + 1
            if peek < n and expr[peek].lower() == "h":
                pos += 2
                dn, pos = _read_int(expr, pos)  # type: ignore[assignment]
                group.drop_mode = "dh"
                group.drop_n = dn if dn is not None else 1
            elif peek < n and expr[peek].lower() == "l":
                pos += 2
                dn, pos = _read_int(expr, pos)  # type: ignore[assignment]
                group.drop_mode = "dl"
                group.drop_n = dn if dn is not None else 1
            elif peek < n and expr[peek].isdigit():
                pos += 1
                dn, pos = _read_int(expr, pos)  # type: ignore[assignment]
                group.drop_mode = "dl"  # 'd' 简写 = dl（丢弃最低，与 Roll20 一致）
                group.drop_n = dn if dn is not None else 1
            # else: 不是丢弃修饰 → 不消耗
    elif expr[pos : pos + 3].lower() == "adv":
        # 优势固定为 2d20kh1：显式骰数 >2 时静默覆盖会掩盖用户意图，报错提示。
        if group.count > 2:
            raise DiceParseError(
                f"优势/劣势（adv/dis）固定为 2 颗骰子，骰数 {group.count} 无效；"
                "直接写 d20adv 即可"
            )
        group.count = 2
        group.keep_mode = "kh"
        group.keep_n = 1
        pos += 3
    return pos


def _parse_exploding(expr: str, pos: int, group: DiceGroup) -> int:
    """解析可选的爆炸修饰（!、!!、!p）及自定义爆炸阈值，就地更新 group。"""
    n = len(expr)
    if pos >= n or expr[pos] != "!":
        return pos
    group.exploding = True
    pos += 1
    if pos < n and expr[pos] == "!":
        group.explode_mode = "compound"
        pos += 1
    elif pos < n and expr[pos].lower() == "p":
        group.explode_mode = "penetrate"
        pos += 1
    else:
        group.explode_mode = "standard"
    # 可选自定义爆炸 ComparePoint
    if pos < n and (expr[pos] in (">", "<", "=") or expr[pos].isdigit()):
        cmp, val, pos = _read_compare_point(expr, pos)
        group.explode_compare = cmp
        group.explode_value = val
    return pos


def _parse_success_failure(expr: str, pos: int, group: DiceGroup) -> int:
    """解析可选的成功计数（>N、<N）和失败计数（fN）修饰，就地更新 group。"""
    n = len(expr)
    if pos >= n or expr[pos] not in (">", "<"):
        return pos
    cmp, val, new_pos = _read_compare_point(expr, pos)
    if val is None:
        return pos
    group.success_compare = cmp
    group.success_value = val
    pos = new_pos
    # 可选失败计数（f[>|<|=]N）
    if pos < n and expr[pos].lower() == "f":
        pos += 1
        f_cmp, f_val, new_pos2 = _read_compare_point(expr, pos)
        if f_val is not None:
            group.failure_compare = f_cmp
            group.failure_value = f_val
            pos = new_pos2
        else:
            pos -= 1  # 'f' 后无数字 → 退回
    return pos


def _parse_reroll(expr: str, pos: int, group: DiceGroup) -> int:
    """解析可重复的重骰修饰（r 和 ro），就地向 group.reroll_conditions 追加条件。"""
    n = len(expr)
    while pos < n and expr[pos].lower() == "r":
        once = False
        pos += 1
        if pos < n and expr[pos].lower() == "o":
            once = True
            pos += 1
        cmp, val, new_pos = _read_compare_point(expr, pos)
        if val is None:
            cmp, val = "=", 1  # 默认：重骰 =1
        else:
            pos = new_pos
        group.reroll_conditions.append(
            RerollCondition(compare=cmp, value=val, once=once)
        )
    return pos


def _parse_sort(expr: str, pos: int, group: DiceGroup) -> int:
    """解析可选的排序修饰（s / sa / sd），就地更新 group。"""
    n = len(expr)
    if pos >= n or expr[pos].lower() != "s":
        return pos
    pos += 1
    if pos < n and expr[pos].lower() == "d":
        group.sort_order = "desc"
        pos += 1
    elif pos < n and expr[pos].lower() == "a":
        group.sort_order = "asc"
        pos += 1
    else:
        group.sort_order = "asc"  # 默认升序
    return pos


def _parse_dice_token(expr: str, pos: int) -> tuple[DiceGroup | None, int]:
    """
    从 expr[pos] 起尝试解析一个骰子 token。

    返回 (DiceGroup, new_pos) 或 (None, pos)（未能匹配时）。
    内部依次调用五个独立子解析器处理各类修饰符。

    子解析器扩展约定
    ----------------
    每个子解析器的函数签名如下::

        def _parse_XYZ(expr: str, pos: int, group: DiceGroup) -> int:

    须满足：
    * 只消耗其能识别的字符，相应推进 *pos*；
    * 修饰符不存在时返回原始 *pos* 不变；
    * 就地修改 *group* 以记录已解析的选项；
    * 输入模糊时应回退（返回预读前的 pos）而非修改 *group*；
      仅在输入明确非法时（如 5d20adv 的骰数冲突）抛出 DiceParseError。

    新增修饰符应作为额外的子解析器实现，并追加到本函数
    循环体末尾 _parse_sort 调用之后。
    """
    start = pos
    n = len(expr)

    # 1. 可选骰子数量：整数 或 '(' 子表达式 ')'
    count_val = None
    count_expr = None
    count_src = None
    m = _INT_TOKEN_RE.match(expr, pos)
    if m:
        count_val = int(m.group(0))
        pos = m.end()
    elif pos < n and expr[pos] == "(":
        sub, end = _parse_expr(expr, pos + 1)
        if end >= n or expr[end] != ")":
            raise DiceParseError(f"骰数位置的括号未闭合: {expr[pos:]!r}")
        count_expr = sub
        count_src = expr[pos : end + 1]
        pos = end + 1

    # 2. 'D' 或 'd'
    if pos >= n or expr[pos].lower() != "d":
        return None, start

    pos += 1  # 消耗 'd'

    # 3. 骰面：'F'/'f' = FATE，否则整数 或 '(' 子表达式 ')'
    fate = False
    sides = 0
    sides_expr = None
    sides_src = None
    if pos < n and expr[pos].lower() == "f":
        fate = True
        sides = 0
        pos += 1
    else:
        m2 = _INT_TOKEN_RE.match(expr, pos)
        if m2 is not None:
            sides = int(m2.group(0))
            pos = m2.end()
        elif pos < n and expr[pos] == "(":
            sub2, end2 = _parse_expr(expr, pos + 1)
            if end2 >= n or expr[end2] != ")":
                raise DiceParseError(f"骰面位置的括号未闭合: {expr[pos:]!r}")
            sides_expr = sub2
            sides_src = expr[pos : end2 + 1]
            pos = end2 + 1
        else:
            return None, start

    count = count_val if count_val is not None else 1
    group = DiceGroup(
        count=count,
        sides=sides,
        fate=fate,
        count_src=count_src,
        sides_src=sides_src,
        count_expr=count_expr,
        sides_expr=sides_expr,
    )

    # 4–8. 循环应用各类修饰：Roll20 语法不限定修饰符书写顺序
    # （如 4d6r<2kh3、8d6s!），单趟固定顺序会漏解析，
    # 因此重复整轮执行直到一整轮 pos 无前进为止。
    # 同类修饰符出现多次时后者覆盖前者（reroll 为追加列表）。
    while True:
        round_start = pos
        pos = _parse_keep_drop(expr, pos, group)
        pos = _parse_exploding(expr, pos, group)
        pos = _parse_success_failure(expr, pos, group)
        pos = _parse_reroll(expr, pos, group)
        pos = _parse_sort(expr, pos, group)
        if pos == round_start:
            break

    # 安全守卫：循环收敛后剩余字符若为字母，说明存在无法识别的修饰符，
    # 立即报错而非回退到主循环再失败（+/- 等组间分隔符不在此列）。
    if pos < len(expr) and expr[pos].isalpha():
        raise DiceParseError(
            f"无法识别的骰子修饰符 {expr[pos:]!r}"
            f"（应为 kh/kl/k、dl/dh/d<N>、!、>/<、r/ro、s/s[ad] 之一）"
        )

    return group, pos


# ---------------------------------------------------------------------------
# 表达式层：递归下降文法（v0.47.0，四则运算 + 括号）
# ---------------------------------------------------------------------------
#
# expr   := term (('+' | '-') term)*      左结合
# term   := factor (('*' | '/') factor)*  左结合，除法求值期向下取整
# factor := ('+' | '-') factor | atom     一元符号
# atom   := dice_token | '(' expr ')' | NUMBER
#
# 与 _parse_dice_token 相互递归（骰数/骰面位置的括号算式由 _parse_expr 解析）。


def _parse_expr(s: str, pos: int) -> tuple[ExprNode, int]:
    node, pos = _parse_term(s, pos)
    while pos < len(s) and s[pos] in ("+", "-"):
        op = s[pos]
        pos += 1
        right, pos = _parse_term(s, pos)
        node = BinOpNode(op, node, right)
    return node, pos


def _parse_term(s: str, pos: int) -> tuple[ExprNode, int]:
    node, pos = _parse_factor(s, pos)
    while pos < len(s) and s[pos] in ("*", "/"):
        op = s[pos]
        pos += 1
        right, pos = _parse_factor(s, pos)
        node = BinOpNode(op, node, right)
    return node, pos


def _parse_factor(s: str, pos: int) -> tuple[ExprNode, int]:
    if pos < len(s) and s[pos] in ("+", "-"):
        sign = s[pos]
        pos += 1
        operand, pos = _parse_factor(s, pos)
        return (NegNode(operand) if sign == "-" else operand), pos
    return _parse_atom(s, pos)


def _parse_atom(s: str, pos: int) -> tuple[ExprNode, int]:
    # 骰子 token 优先（它支持骰数/骰面位置的括号算式，如 (2+3)d6）。
    group, new_pos = _parse_dice_token(s, pos)
    if group is not None:
        return DiceNode(group), new_pos
    # 括号分组
    if pos < len(s) and s[pos] == "(":
        child, pos = _parse_expr(s, pos + 1)
        if pos >= len(s) or s[pos] != ")":
            raise DiceParseError("括号未闭合")
        return GroupNode(child), pos + 1
    # 常数
    m = _INT_TOKEN_RE.match(s, pos)
    if m:
        return ConstNode(int(m.group(0))), m.end()
    raise DiceParseError(f"无法解析表达式中的 '{s[pos:]}'")


def _validate_success_root_only(root: ExprNode) -> None:
    """
    计数组限根校验：带 >N/<N/fN 计数修饰的骰组只能作为整条表达式（可被一元负号
    包裹）出现，不得参与任何四则运算或出现在括号中（含骰数/骰面位置的括号算式）。
    """
    counted: list[DiceNode] = []

    def walk(n: ExprNode) -> None:
        if isinstance(n, DiceNode):
            if n.group.success_compare is not None:
                counted.append(n)
            if n.group.count_expr is not None:
                walk(n.group.count_expr)  # type: ignore[arg-type]
            if n.group.sides_expr is not None:
                walk(n.group.sides_expr)  # type: ignore[arg-type]
        elif isinstance(n, GroupNode):
            walk(n.child)  # type: ignore[arg-type]
        elif isinstance(n, NegNode):
            walk(n.operand)  # type: ignore[arg-type]
        elif isinstance(n, BinOpNode):
            walk(n.left)  # type: ignore[arg-type]
            walk(n.right)  # type: ignore[arg-type]

    walk(root)
    if not counted:
        return
    # 允许：根是 NegNode(DiceNode) 或 DiceNode，且为唯一计数组、未被括号包裹。
    effective = root.operand if isinstance(root, NegNode) else root  # type: ignore[attr-defined]
    if (
        isinstance(effective, DiceNode)
        and effective.group.success_compare is not None
        and len(counted) == 1
    ):
        return
    raise DiceParseError(
        "计数骰（>N/<N/fN）不能参与四则运算或出现在括号中；请单独投掷计数骰"
    )


def _flatten_sum(node: ExprNode, sign: int = 1) -> tuple[list[DiceGroup], int] | None:
    """
    尝试把 AST 扁平化为现行 (groups, flat_modifier) 结构。

    仅当 AST 为纯 +/- 和（无 * /、无括号、骰数/骰面为字面整数）时可行；
    否则返回 None。sign 表示当前子树的符号（-1 = 取反），复刻旧扁平循环的
    group.modifier 哨兵与 flat_modifier 累积语义。
    """
    if isinstance(node, ConstNode):
        return [], sign * node.value
    if isinstance(node, NegNode):
        return _flatten_sum(node.operand, -sign)  # type: ignore[arg-type]
    if isinstance(node, GroupNode):
        return _flatten_sum(node.child, sign)  # type: ignore[arg-type]
    if isinstance(node, DiceNode):
        g = node.group
        if g.count_expr is not None or g.sides_expr is not None:
            return None  # 骰数/骰面含算式 → 不可扁平
        g.modifier = -1 if sign == -1 else 0
        return [g], 0
    if isinstance(node, BinOpNode):
        if node.op == "+":
            left_res = _flatten_sum(node.left, sign)  # type: ignore[arg-type]
            right_res = _flatten_sum(node.right, sign)  # type: ignore[arg-type]
        elif node.op == "-":
            left_res = _flatten_sum(node.left, sign)  # type: ignore[arg-type]
            right_res = _flatten_sum(node.right, -sign)  # type: ignore[arg-type]
        else:
            return None  # * 或 / → 不可扁平
        if left_res is None or right_res is None:
            return None
        lg, lm = left_res
        rg, rm = right_res
        return lg + rg, lm + rm
    return None


def _extract_repeat(raw: str) -> tuple[int, str]:
    """
    从表达式开头提取多重投掷前缀 N#（Roll20 重复投掷语法）。

    消歧规则（位置敏感）：仅当 '#' 左侧整体为纯数字且位于表达式开头时，
    视为重复次数；其余位置的 '#' 一律留给 _strip_label 按标签分隔符处理。
      '3#d20+d6'      → (3, 'd20+d6')
      '3#d20+d6#攻击' → (3, 'd20+d6#攻击')，第二个 '#' 仍为标签
      'd20+5#攻击'    → (1, 'd20+5#攻击')（左侧非纯数字，不匹配）
      '2d6#3'         → (1, '2d6#3')（'2d6' 后是 'd'，不匹配）

    返回 (repeat, 剩余表达式)；无前缀时返回 (1, raw)。
    """
    m = re.match(r"^(\d+)#(.*)$", raw)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 1, raw


def _strip_label(raw: str) -> tuple[str, str]:
    """
    从原始输入中分离表达式部分与可选标签。

    分离策略（优先级从高到低）：
      1. '#' 强制分隔符，优先处理。
      2. 在首个非 ASCII *字母或 So 类符号* 字符处截断——兼容
         『d20 感知 15』、『1d8+2💥』等。
         - L*（CJK、假名等自然语言字符）：无条件截断。
         - So（Emoji 及杂项符号）：无条件截断。
         - Sm/Sc/Sk（数学/货币/修饰符号）：仅在前有空白时截断，
           避免将粘贴进来的 Unicode 运算符（如 ×、÷）静默丢弃。
      3. 以第一个空白字符切分：
         a. 若空白后紧跟 +/- 运算符，整体去空格视为纯算式，无标签。
         b. 否则按空白切分——兼容『d20 感知 15』（纯 ASCII 标签）。
      4. 纯 ASCII 且无分隔符：整体作为表达式，无标签。
         ASCII 标签必须通过 '#' 或空格显式分隔。

    返回 (expression_part, label_part)。
    """
    raw = raw.strip()
    raw = _normalize_fullwidth(raw)

    # 1. '#' 强制分隔符。
    if "#" in raw:
        parts = raw.split("#", 1)
        return parts[0].strip(), parts[1].strip()

    # 2. 在首个非 ASCII *字母或 So 类符号* 字符处截断（此步须在空白检测之前，
    #    否则 '2d6 + 1d4 伤害' 会在第一个空格处就被截断）。
    #    触发截断的 Unicode 类别：
    #      - L*（Lu/Ll/Lt/Lm/Lo）：自然语言文字，如 CJK 汉字、假名、西里尔字母
    #        ──无论位置，立即视为标签起点。
    #      - So（Other Symbol）：Emoji 及其他杂项符号（箭头、花饰等）
    #        ──同 L* 无条件截断；用户通常以 Emoji 作为装饰性标签。
    #    不触发无条件截断：
    #      - Sm（Math Symbol，如 ×、÷、√、≤）、Sc（Currency）、Sk（Modifier）
    #        ──仅在前有空白时才截断，避免将粘贴进表达式的 Unicode 数学运算符
    #        静默丢弃（旧行为），改为保留完整输入让解析器给出明确报错。
    #      - P*（标点）、Z*（分隔符）：不触发截断。
    for i, ch in enumerate(raw):
        if ord(ch) > 0x7F:
            cat = unicodedata.category(ch)
            if cat.startswith("L") or cat == "So":
                return raw[:i].strip(), raw[i:].strip()
            # Sm / Sc / Sk 仅在前有空白时才作为标签起点。
            if cat.startswith("S") and i > 0 and raw[i - 1].isspace():
                return raw[: i - 1].strip(), raw[i:].strip()

    # 3. 以第一个空白字符切分（纯 ASCII 输入）。
    ws_match = re.search(r"\s", raw)
    if ws_match:
        after = raw[ws_match.start() :].lstrip()
        # 若空白后紧跟运算符，说明这是算式内部空格（如 '2d6 + 1d4'）。
        # 仅归一化运算符周围的空格（' + ' → '+'），保留运算符后的非算式内容。
        # 例如 '2d6 + 1d4 damage' → '2d6+1d4 damage'，标签不被并入表达式。
        if after and after[0] in ("+", "-"):
            normalized = re.sub(r"\s*([+\-])\s*", r"\1", raw)
            ws_match2 = re.search(r"\s", normalized)
            if ws_match2:
                return (
                    normalized[: ws_match2.start()].strip(),
                    normalized[ws_match2.start() :].strip(),
                )
            return normalized, ""
        return raw[: ws_match.start()].strip(), raw[ws_match.start() :].strip()

    # 4. 纯 ASCII、无分隔符：整体作为表达式。
    return raw, ""


def parse(
    raw: str, default_sides: int = 20, max_input_len: int | None = None
) -> ParsedExpression:
    """
    将原始骰池表达式字符串解析为 ParsedExpression。

    表达式无效或为空时抛出 DiceParseError。

    Args:
        raw: 原始骰池表达式字符串。
        default_sides: 无参数时使用的默认骰面数，默认为 20（d20）。
        max_input_len: 允许的最大输入长度（字符数）。None 时使用模块级
            _MAX_INPUT_LEN 默认值（200），主要供插件配置动态覆盖。
    """
    limit = max_input_len if max_input_len is not None else _MAX_INPUT_LEN
    if raw and len(raw) > limit:
        raise DiceParseError(f"表达式过长（输入 {len(raw)} 字符，最大 {limit} 字符）")

    if not raw or not raw.strip():
        # 默认：单个 dN（N 由调用方指定，通常为 20）
        return ParsedExpression(
            groups=[DiceGroup(count=1, sides=default_sides)], flat_modifier=0, label=""
        )

    stripped = raw.strip()
    # 多重投掷前缀（N#）必须先于 _strip_label 提取：后者的 '#' 拆分是标签语义。
    repeat, stripped = _extract_repeat(_normalize_fullwidth(stripped))
    if repeat < 1:
        raise DiceParseError(f"重复投掷次数必须至少为 1，得到 {repeat}（如 3#d20）")

    expr_str, label = _strip_label(stripped)

    # 检测标签末尾是否为整数，若是则提取为难度等级（DC）。
    # 仅识别「空格 + 整数尾段」或「纯数字标签」两种写法：
    #   '技能名 15' → label='技能名', dc=15
    #   '技能名15'  → 不提取（'房间2' 这类名称不应被误判为 DC）
    dc: int | None = None
    if label:
        # 仅在「空格 + 纯整数尾段」时提取 DC，避免 '房间2' 被误判为 DC=2。
        # 兼容写法：
        #   '感知 15'  → label='感知', dc=15（空格分隔）
        #   '15'       → label='', dc=15（纯数字标签）
        dc_match = re.match(r"^(.+\S)\s+(\d+)\s*$", label)
        if dc_match:
            label = dc_match.group(1).strip()
            dc = int(dc_match.group(2))
        elif re.match(r"^\d+$", label.strip()):
            # 标签本身就是纯数字（无文字说明时直接写 DC）
            dc = int(label.strip())
            label = ""

    # 多重投掷禁用 DC：每次投掷独立输出，无法承载单一 DC 判定。
    if repeat > 1 and dc is not None:
        raise DiceParseError(
            f"多重投掷（{repeat}#）不支持 DC 判定；"
            "请去掉标签末尾的难度等级数字，或改为单次投掷"
        )

    # 去掉表达式内部的空格，便于 token 解析。
    expr_str = expr_str.replace(" ", "")
    if not expr_str:
        raise DiceParseError(f"无法解析骰池表达式: '{raw}'")

    # 递归下降构建表达式树。
    ast, pos = _parse_expr(expr_str, 0)
    if pos < len(expr_str):
        raise DiceParseError(
            f"无法解析骰池表达式中的 '{expr_str[pos:]}' (完整输入: '{raw}')\n"
            "示例语法: d20, 1d20+5, 4d6kh3, d20adv, d6!, 3d6>3, 2d8r<2, 4dF"
        )

    # 计数组限根校验：>N/<N/fN 不得参与四则或括号。
    _validate_success_root_only(ast)

    # 扁平兼容回退：纯 +/- 和 → 保留 groups + flat_modifier（与旧解析等价，
    # 零回归）；否则保留 ast 走新求值路径。
    groups: list[DiceGroup] = []
    flat_modifier = 0
    flattened = _flatten_sum(ast)
    if flattened is not None:
        groups, flat_modifier = flattened
        ast = None

    return ParsedExpression(
        groups=groups,
        flat_modifier=flat_modifier,
        label=label,
        dc=dc,
        repeat=repeat,
        ast=ast,
    )

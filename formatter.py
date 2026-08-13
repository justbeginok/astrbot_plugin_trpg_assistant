"""
formatter.py — DnD 骰点结果的纯文本格式化器。

输出规则：
  - 不使用任何 Markdown 语法（无 **、~~、>、-、# 等）
  - 不使用 emoji
  - 被丢弃的骰子用括号标注，如 (1)
  - 被重骰替换的原始值附加波浪线，如 ~2~
  - 爆炸追加骰附加 "!" 后缀，如 6!
  - FATE 骰面映射：-1 → "-", 0 → "0", 1 → "+"
  - 成功计数模式下，计入成功的骰值标注 *，计入失败的标注 x
  - 天然 20 / 天然 1 在结果行末注释
  - show_detail=False 时仅显示：<表达式> = <总计>
"""

from __future__ import annotations

from .dice_parser import BinOpNode, ConstNode, DiceNode, GroupNode, NegNode
from .dice_roller import DiceGroupResult, DieRoll, RollResult

# ---------------------------------------------------------------------------
# FATE 骰值映射
# ---------------------------------------------------------------------------

_FATE_DISPLAY = {-1: "-", 0: "0", 1: "+"}


def _fate_str(v: int) -> str:
    return _FATE_DISPLAY.get(v, str(v))


def _cmp(value: int, op: str, threshold: int) -> bool:
    """计算 value <op> threshold，其中 '>' / '<' 分别表示 >= / <=。

    与 dice_roller._compare 逻辑一致，但避免导入私有名称。
    """
    if op == ">":
        return value >= threshold
    if op == "<":
        return value <= threshold
    return value == threshold


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _group_label_core(g: DiceGroup) -> str:
    """为单组骰子构建可读标签（不含负号前缀），如 '4d6kh3'、'(2+3)d6'、'3d(2*4)'。

    骰数/骰面位置若含括号算式（count_src/sides_src），忠实回显原始算式文本。
    """
    if g.count_src is not None:
        count_str = g.count_src
    else:
        count_str = str(g.count) if g.count != 1 else ""

    if g.fate:
        base = f"{count_str}dF"
    else:
        sides_str = g.sides_src if g.sides_src is not None else str(g.sides)
        base = f"{count_str}d{sides_str}"

    # keep / drop 后缀
    if g.keep_mode == "kh" and g.keep_n == 1 and g.count == 2:
        kd_suffix = "adv"
    elif g.keep_mode == "kl" and g.keep_n == 1 and g.count == 2:
        kd_suffix = "dis"
    elif g.keep_mode == "kh" and g.keep_n is not None:
        kd_suffix = f"kh{g.keep_n}"
    elif g.keep_mode == "kl" and g.keep_n is not None:
        kd_suffix = f"kl{g.keep_n}"
    elif g.drop_mode == "dl" and g.drop_n is not None:
        kd_suffix = f"dl{g.drop_n}"
    elif g.drop_mode == "dh" and g.drop_n is not None:
        kd_suffix = f"dh{g.drop_n}"
    else:
        kd_suffix = ""

    # 爆炸后缀
    if g.exploding:
        if g.explode_mode == "compound":
            explode_mark = "!!"
        elif g.explode_mode == "penetrate":
            explode_mark = "!p"
        else:
            explode_mark = "!"
        # 自定义爆炸阈值
        if g.explode_compare is not None and g.explode_value is not None:
            cmp = "" if g.explode_compare == "=" else g.explode_compare
            explode_mark += f"{cmp}{g.explode_value}"
    else:
        explode_mark = ""

    # 成功/失败计数
    success_str = ""
    if g.success_compare is not None and g.success_value is not None:
        cmp = "" if g.success_compare == "=" else g.success_compare
        success_str = f"{cmp}{g.success_value}"
    failure_str = ""
    if g.failure_compare is not None and g.failure_value is not None:
        cmp = "" if g.failure_compare == "=" else g.failure_compare
        failure_str = f"f{cmp}{g.failure_value}"

    # 重骰后缀
    reroll_str_parts = []
    for cond in g.reroll_conditions:
        prefix = "ro" if cond.once else "r"
        cmp = "" if cond.compare == "=" else cond.compare
        reroll_str_parts.append(f"{prefix}{cmp}{cond.value}")
    reroll_str = "".join(reroll_str_parts)

    # 排序后缀
    if g.sort_order == "desc":
        sort_str = "sd"
    elif g.sort_order == "asc":
        sort_str = "s"
    else:
        sort_str = ""

    return f"{base}{kd_suffix}{explode_mark}{success_str}{failure_str}{reroll_str}{sort_str}"


def _group_label(gr: DiceGroupResult) -> str:
    """为单组骰子构建可读标签（含负号前缀）。"""
    sign = "-" if gr.negated else ""
    return sign + _group_label_core(gr.group)


def _annotate_die(die: DieRoll, gr: DiceGroupResult) -> str:
    """Format a single DieRoll into its display string."""
    g = gr.group
    raw = _fate_str(die.value) if g.fate else str(die.value)
    if die.state == "rerolled":
        return f"~{raw}~"
    if die.state == "exploded":
        # 爆炸追加骰在成功计数模式下同样参与计数，展示时也应标注成功/失败标记。
        if gr.is_success_mode and g.success_compare and g.success_value is not None:
            if _cmp(die.value, g.success_compare, g.success_value):
                raw = f"{raw}*"
            elif (
                g.failure_compare
                and g.failure_value is not None
                and _cmp(die.value, g.failure_compare, g.failure_value)
            ):
                raw = f"{raw}x"
        return f"{raw}!"
    # state 为 "kept"/"kept_capped"（保留）或 "dropped"（已丢弃）时到达此处
    if gr.is_success_mode and die.state in ("kept", "kept_capped"):
        if g.success_compare and g.success_value is not None:
            if _cmp(die.value, g.success_compare, g.success_value):
                raw = f"{raw}*"
            elif (
                g.failure_compare
                and g.failure_value is not None
                and _cmp(die.value, g.failure_compare, g.failure_value)
            ):
                raw = f"{raw}x"
    if die.state == "dropped":
        return f"({raw})"
    if die.state == "kept_capped":
        # 重骰深度耗尽，最终值仍落在应重骰区间——加 '?' 后缀提示。
        return f"{raw}?"
    return raw


def _format_dice_list(gr: DiceGroupResult) -> str:
    """
    将单组骰子的各个骰值格式化为括号列表。

    展示规则：
      - 被丢弃骰子用圆括号包裹：(1)
      - 被重骰替换的原始值加波浪线：~2~
      - 爆炸追加骰加 '!' 后缀（复合爆炸无追加骰概念，跳过）
      - FATE 骰显示 -/0/+
      - 成功计数模式：计入成功的骰值加 *，计入失败的加 x

    依赖引擎填充的 die_rolls（按位置精确标注），若列表为空则返回空括号。
    """
    return "[" + ", ".join(_annotate_die(d, gr) for d in gr.die_rolls) + "]"


def _rebuild_expr(result: RollResult) -> str:
    """重建用于显示的紧凑表达式字符串。"""
    parts: list[str] = []
    for i, gr in enumerate(result.group_results):
        lbl = _group_label(gr)
        if i == 0 and not gr.negated:
            parts.append(lbl.lstrip("+"))
        elif gr.negated:
            parts.append(lbl)  # _group_label 已带 '-' 前缀
        else:
            parts.append("+" + lbl)

    mod = result.expression.flat_modifier
    if mod > 0:
        parts.append(f"+{mod}")
    elif mod < 0:
        parts.append(str(mod))

    return "".join(parts)


# ---------------------------------------------------------------------------
# AST 回显与明细（v0.47.0）
# ---------------------------------------------------------------------------

_PREC = {"+": 1, "-": 1, "*": 2, "/": 2}


def _expr_to_str(node: object, parent_prec: int = 0) -> str:
    """把表达式树回显为中缀字符串（显式括号保留、按优先级最小加括号）。"""
    if isinstance(node, ConstNode):
        return str(node.value)
    if isinstance(node, DiceNode):
        return _group_label_core(node.group)
    if isinstance(node, GroupNode):
        return "(" + _expr_to_str(node.child) + ")"
    if isinstance(node, NegNode):
        inner = _expr_to_str(node.operand, 3)
        return "-" + inner
    if isinstance(node, BinOpNode):
        prec = _PREC[node.op]
        left = _expr_to_str(node.left, prec)
        right = _expr_to_str(node.right, prec)
        s = f"{left}{node.op}{right}"
        if prec < parent_prec:
            return "(" + s + ")"
        return s
    return "?"


def _ast_detail(node: object, group_iter: iter) -> str:
    """
    中序遍历 AST 生成明细主体：骰组展开为骰值列表、常数与运算符原位插入。

    group_iter 按左到右（中序）供给 DiceGroupResult，与 _eval_node 的
    group_results 顺序一致。
    """
    if isinstance(node, DiceNode):
        gr = next(group_iter)
        return _format_dice_list(gr)
    if isinstance(node, ConstNode):
        return str(node.value)
    if isinstance(node, NegNode):
        return "-" + _ast_detail(node.operand, group_iter)
    if isinstance(node, GroupNode):
        return "(" + _ast_detail(node.child, group_iter) + ")"
    if isinstance(node, BinOpNode):
        left = _ast_detail(node.left, group_iter)
        right = _ast_detail(node.right, group_iter)
        return f"{left} {node.op} {right}"
    return "?"


# ---------------------------------------------------------------------------
# 公开格式化器
# ---------------------------------------------------------------------------


def _net_success_str(total_successes: int, total_failures: int) -> str:
    """
    将成功数/失败数格式化为可读字符串。

    当负号组导致计数器出现负値时，改为展示净值，避免 '−N成功' 等语义难懂的输出。
    """
    if total_successes < 0 or total_failures < 0:
        net = total_successes - total_failures
        return f"{net}净成功" if net >= 0 else f"{abs(net)}净失败"
    if total_failures:
        return f"{total_successes}成功 {total_failures}失败"
    return f"{total_successes}成功"


def _success_totals(result: RollResult) -> tuple[int, int]:
    """汇总单次投掷的成功/失败计数（负号组分别扣减），返回 (成功数, 失败数)。"""
    total_successes = 0
    total_failures = 0
    for gr in result.group_results:
        s = gr.successes or 0
        f = gr.failures or 0
        if gr.negated:
            total_successes -= s
            total_failures -= f
        else:
            total_successes += s
            total_failures += f
    return total_successes, total_failures


def _format_body(
    result: RollResult, show_detail: bool, annotate_natural: bool = True
) -> str:
    """
    格式化单次投掷的明细主体（不含标签/表达式前缀）。

    show_detail=True   → '[骰值] +5 = 18' 或 '[骰值] = 2成功 1失败'
    show_detail=False  → '18' 或 '2成功 1失败'（仅数值）
    annotate_natural=False 时禁用大成功/大失败注释（多重投掷逐行使用）。
    单次投掷与多重投掷的逐行输出共用。
    """
    # --- 成功计数模式 ---
    if result.is_success_mode:
        total_successes, total_failures = _success_totals(result)
        count_str = _net_success_str(total_successes, total_failures)
        if not show_detail:
            return count_str

        dice_parts = [_format_dice_list(gr) for gr in result.group_results]
        dice_str = " ".join(dice_parts)

        dc = result.expression.dc
        if dc is not None:
            net = total_successes - total_failures
            judge = "成功" if net >= dc else "失败"
            return f"{dice_str} = {count_str} / {dc} {judge}"

        return f"{dice_str} = {count_str}"

    # --- 普通求和模式 ---
    if not show_detail:
        return str(result.total)

    dice_parts = [_format_dice_list(gr) for gr in result.group_results]
    dice_str = " ".join(dice_parts)

    mod = result.expression.flat_modifier
    mod_str = f" +{mod}" if mod > 0 else (f" {mod}" if mod < 0 else "")

    dc = result.expression.dc
    if dc is not None:
        if annotate_natural and result.is_natural_20:
            judge = "大成功"
        elif annotate_natural and result.is_natural_1:
            judge = "大失败"
        else:
            judge = "成功" if result.total >= dc else "失败"
        total_str = f"{result.total} / {dc} {judge}"
        return f"{dice_str}{mod_str} = {total_str}"

    result_annotation = ""
    if annotate_natural and result.is_natural_20:
        result_annotation = " 大成功"
    elif annotate_natural and result.is_natural_1:
        result_annotation = " 大失败"

    return f"{dice_str}{mod_str} = {result.total}{result_annotation}"


def _fmt_avg(value: int, repeat: int) -> str:
    """格式化平均值：最多 2 位小数并去尾零（3 次合计 50 → '16.67'）。"""
    return f"{value / repeat:.2f}".rstrip("0").rstrip(".")


def _format_repeated(result: RollResult, show_detail: bool) -> str:
    """
    格式化多重投掷（repeat>1）结果。

    首行为标题行（历史摘要取此行，须纯文本安全）：
      3#d20+d6: 重复 3 次
    逐行明细（show_detail=True 时）：
      #1 [12] [4] = 16
    汇总行：
      合计: 50  平均: 16.67
    """
    expr = result.expression
    first = result.sub_results[0]
    if expr.ast is not None:
        expr_str = f"{expr.repeat}#{_expr_to_str(expr.ast)}"
    else:
        expr_str = f"{expr.repeat}#{_rebuild_expr(first)}"
    prefix = f"{expr.label} {expr_str}" if expr.label else expr_str
    n = len(result.sub_results)
    lines = [f"{prefix}: 重复 {n} 次"]

    if show_detail:
        for i, sub in enumerate(result.sub_results, start=1):
            if expr.ast is not None:
                detail = _ast_detail(expr.ast, iter(sub.group_results))
                lines.append(f"#{i} {detail} = {sub.total}")
            else:
                lines.append(f"#{i} {_format_body(sub, True, annotate_natural=False)}")

    if result.is_success_mode:
        total_s, total_f = 0, 0
        for sub in result.sub_results:
            s, f = _success_totals(sub)
            total_s += s
            total_f += f
        count_str = _net_success_str(total_s, total_f)
        avg_s = _fmt_avg(total_s, n)
        if total_f:
            avg_f = _fmt_avg(total_f, n)
            lines.append(f"合计: {count_str}  平均: {avg_s}成功/次 {avg_f}失败/次")
        else:
            lines.append(f"合计: {count_str}  平均: {avg_s}成功/次")
    else:
        lines.append(f"合计: {result.total}  平均: {_fmt_avg(result.total, n)}")
    return "\n".join(lines)


def format_result(result: RollResult, show_detail: bool = True) -> str:
    """
    将 RollResult 格式化为纯文本字符串。

    单次投掷（show_detail=True） → {标签} {表达式}: {骰值列表} = {总计}
    多重投掷                      → 标题行 + 每行 #N 明细 + 汇总行（合计/平均）

    成功计数模式示例：
      3d6>3: [1, 4*, 5*, 2, 6*] = 3成功
      3d6>3f1: [1x, 4*, 5*] = 2成功 1失败
    """
    if result.sub_results:
        return _format_repeated(result, show_detail)

    if result.expression.ast is not None:
        expr_str = _expr_to_str(result.expression.ast)
        prefix = f"{result.label} {expr_str}" if result.label else expr_str
        if not show_detail:
            return f"{prefix} = {result.total}"
        detail = _ast_detail(result.expression.ast, iter(result.group_results))
        return f"{prefix}: {detail} = {result.total}"

    expr_str = _rebuild_expr(result)
    prefix = f"{result.label} {expr_str}" if result.label else expr_str

    if show_detail:
        return f"{prefix}: {_format_body(result, True)}"
    return f"{prefix} = {_format_body(result, False)}"

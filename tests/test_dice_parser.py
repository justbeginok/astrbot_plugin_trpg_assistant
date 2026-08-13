"""dice_parser 单元测试：修复项 F5/F6/F7/F9 及解析回归用例。"""

from __future__ import annotations

import pytest
from astrbot_plugin_trpg_assistant.dice_parser import (
    BinOpNode,
    ConstNode,
    DiceNode,
    DiceParseError,
    GroupNode,
    NegNode,
    parse,
)

# ---------------------------------------------------------------------------
# F5：修饰符顺序不再硬编码
# ---------------------------------------------------------------------------


class TestModifierOrderF5:
    def test_reroll_before_keep(self) -> None:
        g = parse("4d6r<2kh3").groups[0]
        assert (g.count, g.sides) == (4, 6)
        assert (g.keep_mode, g.keep_n) == ("kh", 3)
        assert len(g.reroll_conditions) == 1
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("<", 2, False)

    def test_sort_before_exploding(self) -> None:
        g = parse("8d6s!").groups[0]
        assert g.sort_order == "asc"
        assert g.exploding
        assert g.explode_mode == "standard"

    def test_canonical_order_still_works(self) -> None:
        g = parse("4d6kh3r<2").groups[0]
        assert (g.keep_mode, g.keep_n) == ("kh", 3)
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("<", 2, False)

    def test_unknown_modifier_still_rejected(self) -> None:
        with pytest.raises(DiceParseError):
            parse("4d6xyz3")


# ---------------------------------------------------------------------------
# F6：比较符后无数字须整体回退
# ---------------------------------------------------------------------------


class TestComparePointBacktrackF6:
    def test_bare_equals_after_explode_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("d6!=")

    def test_explode_with_value_still_works(self) -> None:
        g = parse("d6!=3").groups[0]
        assert g.exploding
        assert (g.explode_compare, g.explode_value) == ("=", 3)


# ---------------------------------------------------------------------------
# F7：adv/dis 显式骰数 >2 时报错
# ---------------------------------------------------------------------------


class TestAdvDisF7:
    def test_adv_with_more_than_two_dice_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("5d20adv")

    def test_dis_with_more_than_two_dice_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("3d20dis")

    @pytest.mark.parametrize("raw", ["d20adv", "1d20adv", "2d20adv"])
    def test_adv_valid_counts(self, raw: str) -> None:
        g = parse(raw).groups[0]
        assert (g.count, g.keep_mode, g.keep_n) == (2, "kh", 1)

    def test_dis_valid(self) -> None:
        g = parse("d20dis").groups[0]
        assert (g.count, g.keep_mode, g.keep_n) == (2, "kl", 1)


# ---------------------------------------------------------------------------
# F9：DC 仅在空格分隔（或纯数字标签）时提取——与实现对齐的行为固定用例
# ---------------------------------------------------------------------------


class TestDcExtractionF9:
    def test_dc_with_space(self) -> None:
        expr = parse("d20 感知 15")
        assert expr.label == "感知"
        assert expr.dc == 15

    def test_no_space_suffix_is_not_dc(self) -> None:
        expr = parse("d20 房间2")
        assert expr.label == "房间2"
        assert expr.dc is None

    def test_pure_number_label_is_dc(self) -> None:
        expr = parse("d20 15")
        assert expr.label == ""
        assert expr.dc == 15


# ---------------------------------------------------------------------------
# 解析回归用例
# ---------------------------------------------------------------------------


class TestParserRegression:
    def test_keep_low(self) -> None:
        g = parse("2d20kl1").groups[0]
        assert (g.count, g.sides, g.keep_mode, g.keep_n) == (2, 20, "kl", 1)

    def test_drop_shorthand(self) -> None:
        g = parse("8d6d3").groups[0]
        assert (g.drop_mode, g.drop_n) == ("dl", 3)

    def test_drop_low_explicit(self) -> None:
        g = parse("8d6dl3").groups[0]
        assert (g.drop_mode, g.drop_n) == ("dl", 3)

    def test_drop_high(self) -> None:
        g = parse("8d6dh2").groups[0]
        assert (g.drop_mode, g.drop_n) == ("dh", 2)

    def test_success_failure(self) -> None:
        g = parse("3d6>3f1").groups[0]
        assert (g.success_compare, g.success_value) == (">", 3)
        assert (g.failure_compare, g.failure_value) == ("=", 1)

    def test_fate(self) -> None:
        g = parse("4dF").groups[0]
        assert g.fate
        assert g.count == 4

    def test_reroll_once(self) -> None:
        g = parse("2d6ro<2").groups[0]
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("<", 2, True)

    def test_reroll_default(self) -> None:
        g = parse("8d6r").groups[0]
        cond = g.reroll_conditions[0]
        assert (cond.compare, cond.value, cond.once) == ("=", 1, False)

    def test_sort_asc_and_desc(self) -> None:
        assert parse("8d6s").groups[0].sort_order == "asc"
        assert parse("8d6sd").groups[0].sort_order == "desc"

    def test_multi_group_with_flat_modifier(self) -> None:
        expr = parse("2d6+1d4+3")
        assert len(expr.groups) == 2
        assert (expr.groups[0].count, expr.groups[0].sides) == (2, 6)
        assert (expr.groups[1].count, expr.groups[1].sides) == (1, 4)
        assert expr.flat_modifier == 3

    def test_negative_group(self) -> None:
        expr = parse("1d8-1d6")
        assert expr.groups[0].modifier == 0
        assert expr.groups[1].modifier == -1

    def test_fullwidth_plus_normalized(self) -> None:
        expr = parse("d20＋5")
        assert len(expr.groups) == 1
        assert expr.groups[0].sides == 20
        assert expr.flat_modifier == 5


# ---------------------------------------------------------------------------
# v0.47.0：多重投掷前缀 N#（Repeat Roll）
# ---------------------------------------------------------------------------


class TestRepeatExtraction:
    def test_repeat_prefix(self) -> None:
        expr = parse("3#d20+d6")
        assert expr.repeat == 3
        assert [(g.count, g.sides) for g in expr.groups] == [(1, 20), (1, 6)]

    def test_repeat_with_label(self) -> None:
        expr = parse("3#d20+d6#攻击")
        assert expr.repeat == 3
        assert expr.label == "攻击"
        assert [(g.count, g.sides) for g in expr.groups] == [(1, 20), (1, 6)]

    def test_repeat_defaults_to_one(self) -> None:
        assert parse("d20+5").repeat == 1

    def test_hash_label_unchanged(self) -> None:
        expr = parse("d20+5#攻击")
        assert expr.repeat == 1
        assert expr.label == "攻击"

    def test_hash_dc_unchanged(self) -> None:
        expr = parse("2d6#3")
        assert expr.repeat == 1
        assert expr.label == ""
        assert expr.dc == 3

    def test_fullwidth_hash_repeat(self) -> None:
        expr = parse("3＃d20")
        assert expr.repeat == 3
        assert expr.groups[0].sides == 20

    def test_zero_repeat_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("0#d20")

    def test_repeat_with_dc_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("3#d20 感知 15")

    def test_empty_expression_after_hash_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("3#")


# ---------------------------------------------------------------------------
# v0.47.0：复杂公式 AST
# ---------------------------------------------------------------------------


class TestArithmeticAst:
    def test_multiply_precedence_shape(self) -> None:
        ast = parse("2d6*3+1").ast
        assert isinstance(ast, BinOpNode) and ast.op == "+"
        assert isinstance(ast.left, BinOpNode) and ast.left.op == "*"
        assert isinstance(ast.left.left, DiceNode)
        assert isinstance(ast.left.right, ConstNode) and ast.left.right.value == 3
        assert isinstance(ast.right, ConstNode) and ast.right.value == 1

    def test_count_expression_src(self) -> None:
        expr = parse("(2+3)d6")
        assert isinstance(expr.ast, DiceNode)
        g = expr.ast.group
        assert g.count_src == "(2+3)"
        assert g.count_expr is not None

    def test_sides_expression_src(self) -> None:
        expr = parse("3d(2*4)")
        assert isinstance(expr.ast, DiceNode)
        g = expr.ast.group
        assert g.sides_src == "(2*4)"
        assert g.sides_expr is not None

    def test_count_expression_with_dice(self) -> None:
        expr = parse("(1d6+1)d8")
        assert isinstance(expr.ast, DiceNode)
        g = expr.ast.group
        assert g.count_expr is not None
        assert g.count_src == "(1d6+1)"

    def test_flat_subtraction_still_flattens(self) -> None:
        expr = parse("1d4-1d6")
        assert expr.ast is None
        assert len(expr.groups) == 2
        assert expr.groups[1].modifier == -1

    def test_group_node_preserves_parentheses(self) -> None:
        ast = parse("(2d6+3)*4").ast
        assert isinstance(ast, BinOpNode) and ast.op == "*"
        assert isinstance(ast.left, GroupNode)

    def test_negation_flattens_to_negative_group(self) -> None:
        # -d20+5 等价于「负 d20 + 常数 5」，可扁平化。
        expr = parse("-d20+5")
        assert expr.ast is None
        assert expr.groups[0].modifier == -1
        assert expr.flat_modifier == 5

    def test_negation_node_when_not_flattenable(self) -> None:
        ast = parse("-(2d6*3)").ast
        assert isinstance(ast, NegNode)


class TestArithmeticErrors:
    def test_trailing_operator_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("1d4/")

    def test_unclosed_parenthesis_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("(2d6")

    def test_success_count_mixed_with_arithmetic_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("3d6>3+1d4")

    def test_success_count_in_parentheses_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("(3d6>3)")

    def test_success_count_as_count_raises(self) -> None:
        with pytest.raises(DiceParseError):
            parse("(3d6>3)d4")


class TestArithmeticLexicalBoundary:
    def test_sort_then_multiply(self) -> None:
        g = parse("2d6s*3").ast
        assert isinstance(g, BinOpNode) and g.op == "*"
        assert isinstance(g.left, DiceNode)
        assert g.left.group.sort_order == "asc"

    def test_keep_then_multiply(self) -> None:
        g = parse("2d6k*3").ast
        assert isinstance(g, BinOpNode) and g.op == "*"
        assert g.left.group.keep_mode == "kh"

    def test_explode_threshold_then_multiply(self) -> None:
        g = parse("d6!>4*2").ast
        assert isinstance(g, BinOpNode) and g.op == "*"
        assert g.left.group.exploding
        assert g.left.group.explode_value == 4

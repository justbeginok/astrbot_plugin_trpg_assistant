"""dice_roller 单元测试：修复项 F1/F2/F3/F4/F8 及掷骰回归用例。"""

from __future__ import annotations

import pytest
from astrbot_plugin_trpg_assistant.dice_parser import parse
from astrbot_plugin_trpg_assistant.dice_roller import DiceRollError, roll

# ---------------------------------------------------------------------------
# F1：穿透爆炸连锁
# ---------------------------------------------------------------------------


class TestPenetrateF1:
    def test_penetrate_chains_on_raw_value(self, make_rng) -> None:
        # 基础骰 6 → 追加骰原始值 6,6,3；连锁判定用原始值，记录值减 1。
        rng = make_rng([6, 6, 6, 3])
        result = roll(parse("1d6!p"))
        gr = result.group_results[0]
        assert gr.exploded_extra == [5, 5, 2]
        assert result.total == 6 + 5 + 5 + 2
        assert rng.randint_calls == 4


# ---------------------------------------------------------------------------
# F2：穿透追加骰掷出 1 计为 0
# ---------------------------------------------------------------------------


class TestPenetrateZeroF2:
    def test_penetrate_one_becomes_zero(self, make_rng) -> None:
        make_rng([6, 1])
        result = roll(parse("1d6!p"))
        gr = result.group_results[0]
        assert gr.exploded_extra == [0]
        assert result.total == 6


# ---------------------------------------------------------------------------
# F3：复合爆炸在重骰之后执行
# ---------------------------------------------------------------------------


class TestCompoundAfterRerollF3:
    def test_reroll_compares_single_die_not_merged_total(self, make_rng) -> None:
        # 基础骰 1 → 重骰成 6 → 爆炸链 6,3 → 单骰合并值 15。
        make_rng([1, 6, 6, 3])
        result = roll(parse("1d6!!r1"))
        gr = result.group_results[0]
        # 重骰判定发生在单骰原始值 1 上（而非合并总值）。
        assert gr.rerolled_originals == [1]
        assert gr.kept_rolls == [15]
        assert result.total == 15
        # compound 每颗骰展示单个合并值，重骰原始值保留在历史中。
        states = [(d.value, d.state) for d in gr.die_rolls]
        assert states == [(1, "rerolled"), (15, "kept")]


# ---------------------------------------------------------------------------
# F4：爆炸骰预算增量强制 + 恒成立条件前置守卫
# ---------------------------------------------------------------------------


class TestExplosionBudgetF4:
    def test_standard_explosion_budget_enforced_incrementally(self, max_rng) -> None:
        with pytest.raises(DiceRollError):
            roll(parse("100d2!"))
        # 增量中止：不允许掷完全部 100 + 100*20 = 2100 次才报错。
        assert max_rng.randint_calls <= 600

    def test_compound_explosion_budget_enforced(self, max_rng) -> None:
        with pytest.raises(DiceRollError):
            roll(parse("100d2!!"))
        assert max_rng.randint_calls <= 600

    def test_always_true_explode_condition_rejected_upfront(self, max_rng) -> None:
        with pytest.raises(DiceRollError):
            roll(parse("d6!>1"))
        # 前置守卫：一颗骰子都不应掷出。
        assert max_rng.randint_calls == 0

    def test_default_threshold_not_affected_by_guard(self, make_rng) -> None:
        make_rng([3])
        result = roll(parse("1d6!"))
        assert result.total == 3


# ---------------------------------------------------------------------------
# F8：爆炸追加骰的重骰历史保留
# ---------------------------------------------------------------------------


class TestExplodedRerollHistoryF8:
    def test_exploded_die_reroll_history_kept(self, make_rng) -> None:
        # 基础骰 6 → 爆炸掷出 1 → 触发 r1 重骰成 6。
        make_rng([6, 1, 6])
        result = roll(parse("1d6!r1"))
        gr = result.group_results[0]
        states = [(d.value, d.state) for d in gr.die_rolls]
        assert states == [(6, "kept"), (1, "rerolled"), (6, "exploded")]
        assert gr.rerolled_originals == [1]


# ---------------------------------------------------------------------------
# 回归：keep / drop
# ---------------------------------------------------------------------------


class TestKeepDropRegression:
    def test_keep_high(self, make_rng) -> None:
        make_rng([1, 2, 3, 4])
        gr = roll(parse("4d6kh3")).group_results[0]
        assert gr.kept_rolls == [2, 3, 4]
        assert gr.dropped_rolls == [1]

    def test_keep_low(self, make_rng) -> None:
        make_rng([15, 3])
        gr = roll(parse("2d20kl1")).group_results[0]
        assert gr.kept_rolls == [3]
        assert gr.dropped_rolls == [15]

    def test_drop_shorthand(self, make_rng) -> None:
        make_rng([1, 2, 3, 4])
        gr = roll(parse("4d6d1")).group_results[0]
        assert gr.kept_rolls == [2, 3, 4]
        assert gr.dropped_rolls == [1]

    def test_drop_high(self, make_rng) -> None:
        make_rng([1, 2, 3, 4])
        gr = roll(parse("4d6dh1")).group_results[0]
        assert gr.kept_rolls == [1, 2, 3]
        assert gr.dropped_rolls == [4]


# ---------------------------------------------------------------------------
# 回归：成功/失败计数
# ---------------------------------------------------------------------------


class TestSuccessCountRegression:
    def test_success_and_failure_counting(self, make_rng) -> None:
        make_rng([1, 4, 5])
        result = roll(parse("3d6>3f1"))
        gr = result.group_results[0]
        assert gr.successes == 2
        assert gr.failures == 1
        assert gr.subtotal == 1


# ---------------------------------------------------------------------------
# 回归：FATE 骰
# ---------------------------------------------------------------------------


class TestFateRegression:
    def test_fate_total(self, make_rng) -> None:
        rng = make_rng([-1, 0, 1, 1])
        result = roll(parse("4dF"))
        assert result.total == 1
        assert rng.choice_calls == 4


# ---------------------------------------------------------------------------
# 回归：重骰
# ---------------------------------------------------------------------------


class TestRerollRegression:
    def test_reroll_once_does_not_chain(self, make_rng) -> None:
        # 基础骰 [1, 5]，1 触发 ro 重骰成 1，不再重骰。
        rng = make_rng([1, 5, 1])
        gr = roll(parse("2d6ro<2")).group_results[0]
        assert gr.kept_rolls == [1, 5]
        assert gr.rerolled_originals == [1]
        assert rng.randint_calls == 3

    def test_reroll_depth_exhausted_marks_kept_capped(self, make_rng) -> None:
        # RNG 永远返回 1：深度耗尽后最终值仍落在应重骰区间。
        rng = make_rng([], fallback=1)
        gr = roll(parse("1d6r<2")).group_results[0]
        assert gr.die_rolls[-1].state == "kept_capped"
        assert rng.randint_calls == 1 + 20  # 基础骰 + reroll_max_depth 次重骰


# ---------------------------------------------------------------------------
# 回归：排序
# ---------------------------------------------------------------------------


class TestSortRegression:
    def test_sort_asc(self, make_rng) -> None:
        make_rng([3, 1, 2])
        gr = roll(parse("3d6s")).group_results[0]
        assert gr.kept_rolls == [1, 2, 3]
        assert [d.value for d in gr.die_rolls] == [1, 2, 3]

    def test_sort_desc(self, make_rng) -> None:
        make_rng([3, 1, 2])
        gr = roll(parse("3d6sd")).group_results[0]
        assert gr.kept_rolls == [3, 2, 1]


# ---------------------------------------------------------------------------
# 回归：天然 20 / 天然 1
# ---------------------------------------------------------------------------


class TestNaturalRollRegression:
    def test_natural_20(self, make_rng) -> None:
        make_rng([20])
        assert roll(parse("d20")).is_natural_20

    def test_natural_1(self, make_rng) -> None:
        make_rng([1])
        assert roll(parse("d20")).is_natural_1

    def test_natural_20_with_advantage(self, make_rng) -> None:
        make_rng([20, 5])
        result = roll(parse("d20adv"))
        assert result.is_natural_20
        assert not result.is_natural_1

    def test_natural_1_with_advantage(self, make_rng) -> None:
        make_rng([1, 1])
        assert roll(parse("d20adv")).is_natural_1


# ---------------------------------------------------------------------------
# 回归：多组合与负号组
# ---------------------------------------------------------------------------


class TestMultiGroupRegression:
    def test_multi_group_total(self, make_rng) -> None:
        make_rng([2, 3, 4])
        result = roll(parse("2d6+1d4+3"))
        assert result.total == 2 + 3 + 4 + 3

    def test_negative_group(self, make_rng) -> None:
        make_rng([8, 3])
        result = roll(parse("1d8-1d6"))
        assert result.total == 5
        assert result.group_results[1].subtotal == -3


# ---------------------------------------------------------------------------
# v0.47.0：多重投掷（N#）
# ---------------------------------------------------------------------------


class TestRepeatRoll:
    def test_repeat_produces_independent_sub_results(self, make_rng) -> None:
        make_rng([20, 5, 12])
        result = roll(parse("3#d20"))
        assert len(result.sub_results) == 3
        assert result.group_results == []
        assert [r.total for r in result.sub_results] == [20, 5, 12]
        assert result.total == 37

    def test_repeat_exceeds_max_raises(self) -> None:
        with pytest.raises(DiceRollError):
            roll(parse("21#d20"), max_repeat=20)

    def test_repeat_budget_exceeds_max_dice_raises(self) -> None:
        with pytest.raises(DiceRollError):
            roll(parse("3#100d6"))  # 300 颗 > max_dice 100

    def test_repeat_success_count_independent(self, make_rng) -> None:
        make_rng([6, 1, 2, 3, 5, 4, 2, 6, 3])
        result = roll(parse("3#3d6>3"))
        assert len(result.sub_results) == 3
        assert result.is_success_mode
        assert [r.total for r in result.sub_results] == [1, 3, 2]

    def test_repeat_natural_roll_disabled(self, make_rng) -> None:
        make_rng([20, 5])
        result = roll(parse("2#d20"))
        assert result.is_natural_20 is False
        assert result.is_natural_1 is False
        assert result.sub_results[0].is_natural_20  # 子结果仍可单独判定


# ---------------------------------------------------------------------------
# v0.47.0：复杂公式求值
# ---------------------------------------------------------------------------


class TestArithmeticRoll:
    def test_multiply_two_groups(self, make_rng) -> None:
        # 3d6 = 3+5+4 = 12；6d12 = 4+6+1+5+6+2 = 24 → 288
        make_rng([3, 5, 4, 4, 6, 1, 5, 6, 2])
        result = roll(parse("3d6*(2+4)d12"))
        assert result.total == 288
        assert len(result.group_results) == 2
        assert result.ast_value == 288

    def test_division_floors_left_assoc(self, make_rng) -> None:
        make_rng([5])
        assert roll(parse("(5/2)*3")).total == 6

    def test_division_by_zero_raises(self, make_rng) -> None:
        make_rng([4])
        with pytest.raises(DiceRollError):
            roll(parse("1d4/0"))

    def test_sides_expression(self, make_rng) -> None:
        make_rng([8])
        result = roll(parse("3d(2*4)"))
        assert result.group_results[0].group.sides == 8

    def test_count_expression_with_dice(self, make_rng) -> None:
        # (1d6+1) = 3 → 3 颗 d8
        make_rng([2, 1, 2, 3])
        result = roll(parse("(1d6+1)d8"))
        # group_results: [count_expr 的 1d6, 主骰组 3d8]
        assert len(result.group_results) == 2
        assert result.group_results[1].group.count == 3

    def test_negative_middle_result(self, make_rng) -> None:
        make_rng([1, 6])
        assert roll(parse("1d4-1d6")).total == -5

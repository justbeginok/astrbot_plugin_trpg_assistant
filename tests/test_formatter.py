"""formatter 集成测试：F8 展示要求及关键展示路径回归。"""

from __future__ import annotations

from astrbot_plugin_trpg_assistant.dice_parser import parse
from astrbot_plugin_trpg_assistant.dice_roller import roll
from astrbot_plugin_trpg_assistant.formatter import format_result

# ---------------------------------------------------------------------------
# F8：爆炸追加骰被重骰时原始值须以 ~N~ 展示
# ---------------------------------------------------------------------------


class TestExplodedRerollDisplayF8:
    def test_rerolled_original_of_exploded_die_shown(self, make_rng) -> None:
        # 基础骰 6 → 爆炸掷出 1 → r1 重骰成 6：原始 1 的 ~1~ 标注不可丢失。
        make_rng([6, 1, 6])
        out = format_result(roll(parse("1d6!r1")))
        assert "~1~" in out
        assert "[6, ~1~, 6!]" in out


# ---------------------------------------------------------------------------
# 展示回归
# ---------------------------------------------------------------------------


class TestDisplayRegression:
    def test_dropped_dice_in_parentheses(self, make_rng) -> None:
        make_rng([1, 2, 3, 4])
        out = format_result(roll(parse("4d6kh3")))
        assert "(1)" in out
        assert "= 9" in out

    def test_success_failure_marks(self, make_rng) -> None:
        make_rng([1, 4, 5])
        out = format_result(roll(parse("3d6>3f1")))
        assert "1x" in out
        assert "4*" in out
        assert "5*" in out
        assert "2成功 1失败" in out

    def test_fate_faces(self, make_rng) -> None:
        make_rng([-1, 0, 1, 1])
        out = format_result(roll(parse("4dF")))
        assert "[-, 0, +, +]" in out

    def test_natural_20_annotation_with_advantage(self, make_rng) -> None:
        make_rng([20, 5])
        out = format_result(roll(parse("d20adv")))
        assert "大成功" in out

    def test_dc_judgement(self, make_rng) -> None:
        make_rng([10])
        out = format_result(roll(parse("d20 感知 15")))
        assert "10 / 15 失败" in out

    def test_reroll_depth_exhausted_question_mark(self, make_rng) -> None:
        make_rng([], fallback=1)
        out = format_result(roll(parse("1d6r<2")))
        assert "1?" in out

    def test_show_detail_false(self, make_rng) -> None:
        make_rng([4])
        out = format_result(roll(parse("1d6+2")), show_detail=False)
        assert out == "d6+2 = 6"


# ---------------------------------------------------------------------------
# v0.47.0：多重投掷输出
# ---------------------------------------------------------------------------


class TestRepeatFormat:
    def test_repeat_multiline_output(self, make_rng) -> None:
        make_rng([12, 4, 7, 3, 18, 6])
        out = format_result(roll(parse("3#d20+d6")))
        lines = out.splitlines()
        assert lines[0] == "3#d20+d6: 重复 3 次"
        assert lines[1] == "#1 [12] [4] = 16"
        assert lines[2] == "#2 [7] [3] = 10"
        assert lines[3] == "#3 [18] [6] = 24"
        assert lines[4] == "合计: 50  平均: 16.67"

    def test_repeat_with_label_title(self, make_rng) -> None:
        make_rng([20, 6])
        out = format_result(roll(parse("2#d20+6#攻击")))
        assert out.splitlines()[0] == "攻击 2#d20+6: 重复 2 次"

    def test_repeat_no_detail_only_title_and_summary(self, make_rng) -> None:
        make_rng([4, 6])
        out = format_result(roll(parse("2#d6")), show_detail=False)
        lines = out.splitlines()
        assert len(lines) == 2
        assert lines[0] == "2#d6: 重复 2 次"
        assert lines[1] == "合计: 10  平均: 5"

    def test_repeat_no_natural_annotation(self, make_rng) -> None:
        make_rng([20, 5])
        out = format_result(roll(parse("2#d20")))
        assert "大成功" not in out
        assert "大失败" not in out

    def test_repeat_success_count_lines(self, make_rng) -> None:
        make_rng([6, 4, 1, 2, 6, 3])
        out = format_result(roll(parse("2#3d6>3f1")))
        lines = out.splitlines()
        assert lines[0] == "2#3d6>3f1: 重复 2 次"
        assert lines[1] == "#1 [6*, 4*, 1x] = 2成功 1失败"
        assert lines[2] == "#2 [2, 6*, 3*] = 2成功"
        assert lines[3] == "合计: 4成功 1失败  平均: 2成功/次 0.5失败/次"

    def test_repeat_avg_trims_trailing_zeros(self, make_rng) -> None:
        make_rng([1, 2])
        out = format_result(roll(parse("2#d6")))
        assert "平均: 1.5" in out
        assert "1.50" not in out


# ---------------------------------------------------------------------------
# v0.47.0：复杂公式回显
# ---------------------------------------------------------------------------


class TestArithmeticFormat:
    def test_multiply_echo_and_detail(self, make_rng) -> None:
        make_rng([3, 5, 4, 4, 6, 1, 5, 6, 2])
        out = format_result(roll(parse("3d6*(2+4)d12")))
        assert out.startswith("3d6*(2+4)d12: ")
        assert " * " in out
        assert "= 288" in out

    def test_count_expression_echo(self, make_rng) -> None:
        make_rng([4, 3])
        out = format_result(roll(parse("(2+3)d6")))
        assert out.startswith("(2+3)d6: ")
        assert "[4, 3" in out

    def test_sides_expression_echo(self, make_rng) -> None:
        make_rng([8])
        out = format_result(roll(parse("3d(2*4)")))
        assert out.startswith("3d(2*4): ")

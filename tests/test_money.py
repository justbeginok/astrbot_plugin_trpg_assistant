"""money.py 货币结算纯函数测试：折铜 / 找零 / 解析 / 币制显示 / 扣款。"""

from __future__ import annotations

from astrbot_plugin_trpg_assistant.money import (
    format_cp,
    inventory_copper,
    make_change,
    parse_money,
    settle_payment,
    to_copper,
    to_money,
)


class TestConversion:
    def test_to_copper(self) -> None:
        assert to_copper(1, 0, 0) == 100
        assert to_copper(1, 1, 1) == 111
        assert to_copper(0, 0, 0) == 0
        assert to_copper(-1, 2, 3) == 23  # 负分量按 0 容错

    def test_to_money(self) -> None:
        assert to_money(0) == (0, 0, 0)
        assert to_money(213) == (2, 1, 3)
        assert to_money(100) == (1, 0, 0)
        assert to_money(-5) == (0, 0, 0)

    def test_make_change_greedy(self) -> None:
        assert make_change(213) == [("金币", 2), ("银币", 1), ("铜币", 3)]
        assert make_change(100) == [("金币", 1)]
        assert make_change(10) == [("银币", 1)]
        assert make_change(9) == [("铜币", 9)]
        assert make_change(0) == []

    def test_format_cp(self) -> None:
        assert format_cp(213) == "2金1银3铜"
        assert format_cp(100) == "1金"
        assert format_cp(50) == "5银"
        assert format_cp(9) == "9铜"
        assert format_cp(0) == "0铜"

    def test_inventory_copper(self) -> None:
        assert inventory_copper({"金币": 1, "银币": 1, "铜币": 1}) == 111
        assert inventory_copper({"金币": 0, "银币": 0}) == 0
        assert inventory_copper({"苹果": 5}) == 0  # 非币种忽略


class TestParseMoney:
    def test_concatenated_cn(self) -> None:
        assert parse_money("3金5银10铜") == 360
        assert parse_money("3金币5银币") == 350

    def test_whitespace_separated(self) -> None:
        assert parse_money("2 金币") == 200
        assert parse_money("1 金 5 银") == 150

    def test_plain_number_is_copper(self) -> None:
        assert parse_money("50") == 50
        assert parse_money("0") == 0

    def test_mixed_short_and_long(self) -> None:
        assert parse_money("1金2") == 102
        assert parse_money("2金5银") == 250

    def test_invalid_text(self) -> None:
        assert parse_money("送人的礼物") is None
        assert parse_money("abc50") is None
        assert parse_money("-5") is None
        assert parse_money("") is None
        assert parse_money("   ") is None
        assert parse_money("1金 垃圾") is None


class TestSettlePayment:
    def test_pay_whole_gold(self) -> None:
        ok, have = settle_payment({"金币": 20}, 1500)
        assert ok
        assert have == {"金币": 5, "银币": 0, "铜币": 0}

    def test_break_gold_for_small_price(self) -> None:
        # 1 金币买 50 铜 → 破开一枚金币，找回 5 银
        ok, have = settle_payment({"金币": 1}, 50)
        assert ok
        assert have == {"金币": 0, "银币": 5, "铜币": 0}

    def test_break_one_coin_minimal(self) -> None:
        # 1 银 + 1 铜 买 5 铜 → 银币被破开，找回 6 铜
        ok, have = settle_payment({"银币": 1, "铜币": 1}, 5)
        assert ok
        assert have == {"金币": 0, "银币": 0, "铜币": 6}

    def test_keep_large_coins_when_possible(self) -> None:
        # 1 金 + 50 铜 买 30 铜 → 只花铜币，金币保留
        ok, have = settle_payment({"金币": 1, "铜币": 50}, 30)
        assert ok
        assert have == {"金币": 1, "银币": 0, "铜币": 20}

    def test_insufficient(self) -> None:
        ok, have = settle_payment({"铜币": 3}, 5)
        assert not ok
        assert have == {}

    def test_zero_price_no_change(self) -> None:
        ok, have = settle_payment({"金币": 1}, 0)
        assert ok
        assert have == {"金币": 1, "银币": 0, "铜币": 0}

    def test_missing_coins(self) -> None:
        ok, have = settle_payment({}, 0)
        assert ok
        assert have == {"金币": 0, "银币": 0, "铜币": 0}
        ok2, _ = settle_payment({}, 1)
        assert not ok2

    def test_exact_silver(self) -> None:
        # 3 银 买 25 铜 → 2 银 + 5 铜（25 = 2×10 + 5）
        ok, have = settle_payment({"银币": 3}, 25)
        assert ok
        assert have == {"金币": 0, "银币": 0, "铜币": 5}

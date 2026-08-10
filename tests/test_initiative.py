"""initiative.py 与 /ri 参数解析单元测试。

覆盖点：
  - _parse_ri_arg 三分支：空参数/带符号调整值（含负数）/纯整数固定值，
    以及名称跟随解析与非法参数拒绝。
  - add() 入列后按先攻值降序排列，同值按 seq 升序（先报先动）。
  - seq 单调递增：移除后重新入列不产生重复 seq。
  - 名称清洗：控制字符（换行等）被剔除，防止伪造多行输出。
  - advance()：未开始→从最高者开始（轮数 1）；顺序推进；绕回顶部轮数 +1；
    空列表返回 current=None 且状态不变。
  - remove()：移除非当前者指针不变；移除当前者指针移到后继（含平手规则）；
    移除末位者绕回并轮数 +1；移除最后一名清空状态；同名移除最早入列者。
  - clear()：返回被清除条数并清空存储。
  - format_list / format_advance / format_entry_confirmation 输出内容。
"""

from __future__ import annotations

import asyncio

from astrbot_plugin_trpg_assistant.initiative import (
    InitiativeManager,
    InitiativeState,
)
from astrbot_plugin_trpg_assistant.main import _parse_ri_arg


class FakeEvent:
    """最小化的假 AstrMessageEvent，仅提供 InitiativeManager 依赖的成员。"""

    def __init__(self, origin: str, sender_id: str = "u1", sender_name: str = "Alice") -> None:
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name


class FakeStar:
    """内存字典实现的假 Star，模拟 KV 读写（均为 async）。"""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._data[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._data.pop(key, None)


# ---------------------------------------------------------------------------
# /ri 参数解析（main._parse_ri_arg）
# ---------------------------------------------------------------------------


class TestParseRiArg:
    def test_no_arg_rolls_for_self(self) -> None:
        kind, number, name = _parse_ri_arg("", "Alice")
        assert (kind, number, name) == ("roll", 0, "Alice")

    def test_plus_modifier_rolls(self) -> None:
        kind, number, name = _parse_ri_arg("+3", "Alice")
        assert (kind, number, name) == ("roll", 3, "Alice")

    def test_negative_modifier_rolls(self) -> None:
        kind, number, name = _parse_ri_arg("-2", "Alice")
        assert (kind, number, name) == ("roll", -2, "Alice")

    def test_plus_modifier_with_name(self) -> None:
        kind, number, name = _parse_ri_arg("+2 食人魔", "Alice")
        assert (kind, number, name) == ("roll", 2, "食人魔")

    def test_fixed_value_without_name(self) -> None:
        kind, number, name = _parse_ri_arg("15", "Alice")
        assert (kind, number, name) == ("fixed", 15, "Alice")

    def test_fixed_value_with_name(self) -> None:
        kind, number, name = _parse_ri_arg("15 哥布林甲", "Alice")
        assert (kind, number, name) == ("fixed", 15, "哥布林甲")

    def test_multiple_spaces_between_tokens(self) -> None:
        kind, number, name = _parse_ri_arg("  +3   食人魔  ", "Alice")
        assert (kind, number, name) == ("roll", 3, "食人魔")

    def test_invalid_text_rejected(self) -> None:
        kind, _, _ = _parse_ri_arg("abc", "Alice")
        assert kind == "invalid"

    def test_oversized_fixed_value_rejected(self) -> None:
        kind, _, _ = _parse_ri_arg("10000", "Alice")
        assert kind == "invalid"

    def test_trailing_space_keeps_modifier_semantics(self) -> None:
        # "15 " 无名称：按固定值给自己处理（计划中的歧义约定）。
        kind, number, name = _parse_ri_arg("15 ", "Alice")
        assert (kind, number, name) == ("fixed", 15, "Alice")


# ---------------------------------------------------------------------------
# 入列与排序
# ---------------------------------------------------------------------------


class TestAddAndSort:
    def _manager(self) -> tuple[InitiativeManager, FakeStar, FakeEvent]:
        star = FakeStar()
        mgr = InitiativeManager(star=star)
        event = FakeEvent("group:1")
        return mgr, star, event

    def test_entries_sorted_by_value_desc(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            for name, value in [("哥布林", 7), ("食人魔", 18), ("游荡者", 15)]:
                await mgr.add(event, name, value)

            state = await mgr.get_state(event)
            names = [e.name for e in state.sorted_entries()]
            assert names == ["食人魔", "游荡者", "哥布林"]

        asyncio.run(_run())

    def test_tie_broken_by_insertion_order(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "哥布林甲", 7)
            await mgr.add(event, "哥布林乙", 7)
            await mgr.add(event, "哥布林丙", 7)

            state = await mgr.get_state(event)
            names = [e.name for e in state.sorted_entries()]
            assert names == ["哥布林甲", "哥布林乙", "哥布林丙"]

        asyncio.run(_run())

    def test_seq_monotonic_after_removal(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 10)
            await mgr.add(event, "B", 20)
            await mgr.remove(event, "A")
            _, entry = await mgr.add(event, "C", 5)

            state = await mgr.get_state(event)
            seqs = [e.seq for e in state.entries]
            assert len(set(seqs)) == len(seqs), "seq 不允许重复"
            assert entry.seq == 3

        asyncio.run(_run())

    def test_name_sanitized(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "哥布林\n甲\t", 7)

            state = await mgr.get_state(event)
            assert state.entries[0].name == "哥布林 甲"

        asyncio.run(_run())

    def test_name_truncated(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "长" * 100, 7)

            state = await mgr.get_state(event)
            assert len(state.entries[0].name) <= 31  # 30 字符 + 省略号

        asyncio.run(_run())

    def test_roll_entry_records_modifier(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            _, entry = await mgr.add(event, "游荡者", 18, modifier=3)

            assert entry.value == 18
            assert entry.modifier == 3
            assert entry.is_fixed is False

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 回合推进
# ---------------------------------------------------------------------------


class TestAdvance:
    def _manager(self) -> tuple[InitiativeManager, FakeStar, FakeEvent]:
        star = FakeStar()
        mgr = InitiativeManager(star=star)
        event = FakeEvent("group:1")
        return mgr, star, event

    def test_unstarted_advance_starts_with_highest(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "哥布林", 7)
            await mgr.add(event, "食人魔", 18)
            await mgr.add(event, "游荡者", 15)

            result = await mgr.advance(event)
            assert result.started is True
            assert result.wrapped is False
            assert result.previous is None
            assert result.current is not None and result.current.name == "食人魔"
            assert result.state.round == 1

        asyncio.run(_run())

    def test_sequential_advance(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "哥布林", 7)
            await mgr.add(event, "食人魔", 18)
            await mgr.add(event, "游荡者", 15)

            await mgr.advance(event)
            result = await mgr.advance(event)

            assert result.started is False
            assert result.wrapped is False
            assert result.previous is not None and result.previous.name == "食人魔"
            assert result.current is not None and result.current.name == "游荡者"
            assert result.state.round == 1

        asyncio.run(_run())

    def test_wrap_increments_round(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "哥布林", 7)
            await mgr.add(event, "食人魔", 18)

            await mgr.advance(event)  # → 食人魔（round 1）
            result = await mgr.advance(event)  # → 哥布林（round 1）
            assert result.wrapped is False
            result = await mgr.advance(event)  # → 食人魔（round 2）

            assert result.wrapped is True
            assert result.current is not None and result.current.name == "食人魔"
            assert result.state.round == 2
            assert result.previous is not None and result.previous.name == "哥布林"

        asyncio.run(_run())

    def test_advance_empty_list(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()

            result = await mgr.advance(event)
            assert result.current is None
            state = await mgr.get_state(event)
            assert state.current_seq is None and state.round == 0

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 移除单位
# ---------------------------------------------------------------------------


class TestRemove:
    def _manager(self) -> tuple[InitiativeManager, FakeStar, FakeEvent]:
        star = FakeStar()
        mgr = InitiativeManager(star=star)
        event = FakeEvent("group:1")
        return mgr, star, event

    def test_remove_non_current_keeps_pointer(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)
            await mgr.add(event, "B", 15)
            await mgr.add(event, "C", 10)
            await mgr.advance(event)  # current = A

            result = await mgr.remove(event, "C")
            assert result.removed is not None and result.removed.name == "C"
            assert result.next_current is None  # 指针未移动
            state = await mgr.get_state(event)
            assert state.get_current() is not None and state.get_current().name == "A"

        asyncio.run(_run())

    def test_remove_current_moves_to_successor(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)
            await mgr.add(event, "B", 15)
            await mgr.add(event, "C", 10)
            await mgr.advance(event)  # A
            await mgr.advance(event)  # B（current）

            result = await mgr.remove(event, "B")
            assert result.removed is not None and result.removed.name == "B"
            assert result.next_current is not None and result.next_current.name == "C"
            state = await mgr.get_state(event)
            assert state.round == 1  # 未绕回，轮数不变

        asyncio.run(_run())

    def test_remove_current_last_wraps_and_increments_round(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)
            await mgr.add(event, "B", 10)
            await mgr.advance(event)  # A
            await mgr.advance(event)  # B（current，末位）

            result = await mgr.remove(event, "B")
            assert result.removed is not None and result.removed.name == "B"
            assert result.next_current is not None and result.next_current.name == "A"
            state = await mgr.get_state(event)
            assert state.round == 2

        asyncio.run(_run())

    def test_remove_current_with_tie_successor(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)
            await mgr.add(event, "B", 15)  # 先入列
            await mgr.add(event, "C", 15)  # 后入列（同值）
            await mgr.advance(event)  # A
            await mgr.advance(event)  # B（current）

            result = await mgr.remove(event, "B")
            # 平手规则：同值 15 中 B 在前，其后继是同值的 C（后入列者）
            assert result.next_current is not None and result.next_current.name == "C"

        asyncio.run(_run())

    def test_remove_last_entry_clears_state(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)
            await mgr.advance(event)

            result = await mgr.remove(event, "A")
            assert result.removed is not None
            assert result.next_current is None
            state = await mgr.get_state(event)
            assert state.entries == []
            assert state.current_seq is None and state.round == 0

        asyncio.run(_run())

    def test_remove_not_found(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)

            result = await mgr.remove(event, "不存在")
            assert result.removed is None

        asyncio.run(_run())

    def test_remove_duplicate_names_removes_first(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "哥布林", 7)
            await mgr.add(event, "哥布林", 9)

            result = await mgr.remove(event, "哥布林")
            assert result.removed is not None
            state = await mgr.get_state(event)
            assert len(state.entries) == 1
            assert state.entries[0].value == 9  # 先攻 7 的那个被移除

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 清空
# ---------------------------------------------------------------------------


class TestClear:
    def _manager(self) -> tuple[InitiativeManager, FakeStar, FakeEvent]:
        star = FakeStar()
        mgr = InitiativeManager(star=star)
        event = FakeEvent("group:1")
        return mgr, star, event

    def test_clear_returns_count_and_empties(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = InitiativeManager(star=star)
            event = FakeEvent("group:1")
            for i in range(4):
                await mgr.add(event, f"单位{i}", 10 + i)

            count = await mgr.clear(event)
            assert count == 4

            state = await mgr.get_state(event)
            assert state.entries == []
            assert star._data == {}

        asyncio.run(_run())

    def test_clear_empty_returns_zero(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            count = await mgr.clear(event)
            assert count == 0

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 会话隔离
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    def test_entries_isolated_per_session(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            mgr = InitiativeManager(star=star)
            event_a = FakeEvent("group:1")
            event_b = FakeEvent("group:2")

            await mgr.add(event_a, "A", 20)
            await mgr.add(event_b, "B", 10)

            state_a = await mgr.get_state(event_a)
            state_b = await mgr.get_state(event_b)
            assert [e.name for e in state_a.entries] == ["A"]
            assert [e.name for e in state_b.entries] == ["B"]

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 状态容错
# ---------------------------------------------------------------------------


class TestCorruptState:
    def test_from_dict_tolerates_missing_and_dirty_fields(self) -> None:
        state = InitiativeState.from_dict(
            {
                "entries": [
                    {"name": "A", "value": "20", "seq": "1"},
                    {"name": "B", "value": None},
                    "not-a-dict",
                ],
                "current_seq": "1",
                "round": "3",
            }
        )
        assert len(state.entries) == 2
        assert state.entries[0].value == 20
        assert state.entries[1].value == 0
        assert state.current_seq == 1
        assert state.round == 3

    def test_get_kv_returns_non_dict_treated_as_empty(self) -> None:
        async def _run() -> None:
            star = FakeStar()
            await star.put_kv_data("initiative:group:1", ["oops"])
            mgr = InitiativeManager(star=star)
            state = await mgr.get_state(FakeEvent("group:1"))
            assert state.entries == []

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# 格式化输出
# ---------------------------------------------------------------------------


class TestFormatting:
    def _manager(self) -> tuple[InitiativeManager, FakeStar, FakeEvent]:
        star = FakeStar()
        mgr = InitiativeManager(star=star)
        event = FakeEvent("group:1")
        return mgr, star, event

    def test_format_list_empty(self) -> None:
        text = InitiativeManager.format_list(InitiativeState())
        assert "空" in text

    def test_format_list_with_marker_and_round(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "食人魔", 18)
            await mgr.add(event, "哥布林", 7)
            await mgr.advance(event)  # current = 食人魔，round = 1

            state = await mgr.get_state(event)
            text = InitiativeManager.format_list(state)
            assert "第 1 轮" in text
            assert "▶" in text
            assert "食人魔" in text
            assert "哥布林" in text

        asyncio.run(_run())

    def test_format_list_unstarted_hint(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "食人魔", 18)

            state = await mgr.get_state(event)
            text = InitiativeManager.format_list(state)
            assert "尚未开始" in text

        asyncio.run(_run())

    def test_format_advance_started(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "食人魔", 18)
            result = await mgr.advance(event)
            text = InitiativeManager.format_advance(result)
            assert "战斗开始" in text
            assert "食人魔" in text

        asyncio.run(_run())

    def test_format_advance_wrapped(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            await mgr.add(event, "A", 20)
            await mgr.add(event, "B", 10)
            await mgr.advance(event)
            await mgr.advance(event)
            result = await mgr.advance(event)  # 绕回 → round 2
            text = InitiativeManager.format_advance(result)
            assert "第 2 轮开始" in text
            assert "B 的回合结束" in text
            assert "A" in text

        asyncio.run(_run())

    def test_format_entry_confirmation_roll_with_modifier(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            _, entry = await mgr.add(event, "游荡者", 18, modifier=3)
            text = InitiativeManager.format_entry_confirmation(entry)
            assert "d20+3" in text
            assert "18" in text

        asyncio.run(_run())

    def test_format_entry_confirmation_fixed(self) -> None:
        async def _run() -> None:
            mgr, _, event = self._manager()
            _, entry = await mgr.add(event, "哥布林甲", 7, is_fixed=True)
            text = InitiativeManager.format_entry_confirmation(entry)
            assert "已录入" in text
            assert "7" in text

        asyncio.run(_run())

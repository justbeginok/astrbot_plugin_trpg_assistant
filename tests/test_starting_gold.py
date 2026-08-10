"""车卡起始金币自动发放（v0.20.0）测试。

覆盖点：
  - kb.starting_gold：解析 2014 goldAlternative 骰式（5d4 × 10）；2024 无 → None。
  - _grant_starting_gold：代骰 → ×乘数 → 金币入包（value 按面值 100）；2024/掷骰失败静默跳过。
  - _finalize 集成：完整 2014 车卡流程落库后自动发放，文案含发放结果。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from astrbot_plugin_trpg_assistant.chargen import ChargenManager
from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
# 测试构建时隔离真实补丁目录（kb_patches/），避免真实补丁污染 fixture 计数。
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


class FakeEvent:
    def __init__(self, message_str: str = "", origin: str = "group:1") -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self.stopped = False

    def get_sender_id(self) -> str:
        return "u1"

    def get_sender_name(self) -> str:
        return "Alice"

    def is_private_chat(self) -> bool:
        return False

    def is_admin(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self, db_path: Path, config: dict | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}
        self._kb_manager = KnowledgeBaseManager(db_path)

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def make_plugin(tmp_path: Path) -> _MemoryPlugin:
    db = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, db, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return _MemoryPlugin(db)


def make_chargen(p: _MemoryPlugin, roll: object) -> ChargenManager:
    return ChargenManager(
        star=p,
        character_manager=p.character_manager,
        kb_manager=p.kb_manager,
        roll_fn=roll,
        inventory_manager=p._inventory,
    )


# ---------------------------------------------------------------------------
# kb.starting_gold
# ---------------------------------------------------------------------------


def test_kb_starting_gold_2014(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    assert p.kb_manager.starting_gold("战士", "2014") == ("5d4", 10)
    # 不带版本也能命中（2014 唯一）
    assert p.kb_manager.starting_gold("战士") == ("5d4", 10)


def test_kb_starting_gold_2024_none(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    # fixture 无 2024 职业；2024 源数据也不含 goldAlternative
    assert p.kb_manager.starting_gold("战士", "2024") is None


def test_kb_starting_gold_unknown(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    assert p.kb_manager.starting_gold("不存在的职业") is None
    assert p.kb_manager.starting_gold("") is None


# ---------------------------------------------------------------------------
# _grant_starting_gold
# ---------------------------------------------------------------------------


def _fake_roll_fixed(total: int = 3, detail: str = "5d4=[3,3,3,3]→12"):
    return lambda expr: (total, detail)


def test_grant_writes_coins_to_bag(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, _fake_roll_fixed())
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014"))
    assert qty == 30  # 3 × 10
    assert "30 金币" in text
    inv = run(p._inventory.get_personal(FakeEvent()))
    entry = inv.find("金币")
    assert entry is not None
    assert entry.qty == 30
    assert entry.value == 100.0  # 货币条目按面值


def test_grant_2024_skips(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, _fake_roll_fixed())
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2024"))
    assert qty is None and text == ""
    assert run(p._inventory.get_personal(FakeEvent())).items == []


def test_grant_roll_failure_skips(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, lambda expr: (None, "掷骰错误: x"))
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014"))
    assert qty is None and text == ""
    assert run(p._inventory.get_personal(FakeEvent())).items == []


def test_grant_without_kb_skips(tmp_path: Path) -> None:
    # 旧库/替身无 starting_gold 方法 → 静默跳过（getattr 保护）
    p = make_plugin(tmp_path)
    cg = ChargenManager(
        star=p,
        character_manager=p.character_manager,
        kb_manager=object(),  # 无 starting_gold
        roll_fn=_fake_roll_fixed(),
        inventory_manager=p._inventory,
    )
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014"))
    assert qty is None and text == ""


# ---------------------------------------------------------------------------
# 群规则三态：固定金额 / DM 骰式 / auto
# ---------------------------------------------------------------------------


def test_grant_fixed_no_roll_required(tmp_path: Path) -> None:
    """固定金额不依赖 roll_fn（DM 统一起始财富）。"""
    p = make_plugin(tmp_path)
    cg = ChargenManager(
        star=p,
        character_manager=p.character_manager,
        kb_manager=p.kb_manager,
        roll_fn=None,  # 固定金额不需要代骰
        inventory_manager=p._inventory,
    )
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014", "150"))
    assert qty == 150
    assert "DM 规则固定" in text and "150 金币" in text
    entry = run(p._inventory.get_personal(FakeEvent())).find("金币")
    assert entry is not None and entry.qty == 150


def test_grant_dm_dice(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, _fake_roll_fixed(total=5))
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014", "3d6×10"))
    assert qty == 50  # 5 × 10
    assert "DM 规则代骰" in text
    entry = run(p._inventory.get_personal(FakeEvent())).find("金币")
    assert entry is not None and entry.qty == 50


def test_grant_dm_dice_no_multiplier(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, _fake_roll_fixed(total=7))
    qty, _ = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014", "5d4"))
    assert qty == 7  # 无乘数 → 乘数 1


def test_grant_dm_dice_roll_failure_skips(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, lambda expr: (None, "掷骰错误: x"))
    qty, text = run(cg._grant_starting_gold(FakeEvent(), "战士", "2014", "3d6×10"))
    assert qty is None and text == ""
    assert run(p._inventory.get_personal(FakeEvent())).items == []


# ---------------------------------------------------------------------------
# _finalize 集成：完整 2014 车卡流程
# ---------------------------------------------------------------------------


def test_finalize_grants_starting_gold(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    cg = make_chargen(p, _fake_roll_fixed(total=5))
    ev = FakeEvent()
    run(cg.start(ev))
    # 2014 路径：确认 → 矮人 → 战士 → 侍僧 → 属性 → (加值步跳过) → 阵营 →
    # 生平×3 → 命名
    for answer in (
        "确认", "矮人", "战士", "侍僧", "15 14 13 12 10 8",
        "中立善良", "出身", "决定", "经历", "阿尔文",
    ):
        last = run(cg.advance(ev, answer))
    assert last.done is True
    # 起始金币：5 × 10 = 50 金币入包
    inv = run(p._inventory.get_personal(ev))
    entry = inv.find("金币")
    assert entry is not None and entry.qty == 50
    assert "起始金币已代骰发放" in last.next_question
    assert "50 金币" in last.next_question


def test_finalize_fixed_gold_from_rule(tmp_path: Path) -> None:
    """群规则固定起始金币：落库后按固定金额发放（不代骰）。"""
    p = make_plugin(tmp_path)
    cg = make_chargen(p, _fake_roll_fixed(total=99))  # 即使代骰也只会用固定值
    ev = FakeEvent()
    run(cg.start(ev))
    # 先设群规则固定 200 金币
    from astrbot_plugin_trpg_assistant.chargen import parse_rule_edit

    rule = run(cg.get_rule(ev))
    new_rule, _ = parse_rule_edit(rule, ["起始金币", "200"])
    assert new_rule is not None
    run(cg.set_rule(ev, new_rule))
    for answer in (
        "确认", "矮人", "战士", "侍僧", "15 14 13 12 10 8",
        "中立善良", "出身", "决定", "经历", "阿尔文",
    ):
        last = run(cg.advance(ev, answer))
    assert last.done is True
    entry = run(p._inventory.get_personal(ev)).find("金币")
    assert entry is not None and entry.qty == 200
    assert "DM 规则固定" in last.next_question and "200 金币" in last.next_question

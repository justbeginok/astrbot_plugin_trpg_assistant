"""开卡规则与引导状态机（/车卡）单元测试。

覆盖点：
  - AbilityGenMethod / ChargenRule 序列化与容错、format 展示。
  - ABILITY_GEN_ALIASES 别名注册表（27buy/32buy/dnd5/标准数组）。
  - parse_rule_edit：版本/属性别名/自定义购点/自定义掷骰/子职时机/起始等级/重置。
  - 三校验器边界：购点 27 恰好通过 / 28 超池拒绝 / 池外值拒绝；
    标准数组与掷骰分配的多重集比对。
  - parse_ability_input：裸数字顺序 / 显式映射 / 错误输入。
  - 引导状态机：2014 与 2024 双路径全流转、乱序 answer 拒绝、
    cancel、DONE 落库 + 草稿清除、购点硬拒绝不推进、代骰写入草稿。
"""

from __future__ import annotations

import asyncio

from astrbot_plugin_trpg_assistant.chargen import (
    ABILITY_GEN_ALIASES,
    POINT_BUY_COST,
    ChargenDraft,
    ChargenManager,
    ChargenRule,
    AbilityGenMethod,
    parse_ability_input,
    parse_bonus_choice,
    parse_rule_edit,
    validate_point_buy,
    validate_rolled_assign,
    validate_standard_array,
)
from astrbot_plugin_trpg_assistant.character import (
    AbilityScores,
    CharacterManager,
    CharacterSheet,
    ClassLevel,
)
from astrbot_plugin_trpg_assistant.kb import AbilityOffer, ChooseSpec


class _KVStar:
    def __init__(self) -> None:
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


class _Event:
    def __init__(self, origin: str = "group:1", sender_id: str = "u1") -> None:
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self.message_str = ""

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return "Alice"

    def is_private_chat(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text


class _FakeHit:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeKB:
    """知识库替身：names_by_kind[kind] = 合法名称集合。

    v0.18 可选：abilities（race/background → AbilityOffer）、class_combat
    （职业 → dict(hd_faces, caster, spell_ability)），供加值选择与规则引擎测试。
    """

    def __init__(
        self,
        names_by_kind: dict[str, set[str]],
        abilities: dict | None = None,
        class_combat: dict | None = None,
    ) -> None:
        self._names = names_by_kind
        self.available = True
        ab = abilities or {}
        self._race_ab: dict[str, AbilityOffer] = ab.get("race", {})
        self._bg_ab: dict[str, AbilityOffer] = ab.get("background", {})
        self._class_combat = class_combat or {}

    def search(self, query: str, kind: str | None = None, limit: int = 8) -> list:
        names = self._names.get(kind or "", set())
        if query in names:
            return [_FakeHit(query)]
        return []

    def race_ability(self, name: str, edition: str = "") -> AbilityOffer | None:
        return self._race_ab.get(name)

    def background_ability(self, name: str) -> AbilityOffer | None:
        return self._bg_ab.get(name)

    def class_combat(self, name: str, edition: str = ""):
        row = self._class_combat.get(name)
        if row is None:
            return None

        class _Row:
            pass

        r = _Row()
        r.hd_faces = row["hd_faces"]
        r.caster = row["caster"]
        r.spell_ability = row["spell_ability"]
        r.saves = ["str"]
        return r

    def subclass_caster(self, class_name: str, subclass: str, edition: str = ""):
        return None

    def item_combat(self, name: str):
        return None

    def item_base_item(self, name: str) -> str:
        return ""


def _kb_for():
    return _FakeKB(
        {
            "race": {"人类", "精灵", "矮人"},
            "class": {"法师", "战士", "游侠"},
            "background": {"士兵", "流浪儿", "侍僧"},
        }
    )


def _kb_with_abilities() -> _FakeKB:
    """带加值 offer 与职业战斗数据的替身（半精灵 choose / 矮人 flat / 2024 侍僧 weighted）。"""
    half_elf = AbilityOffer(
        flat={"cha": 2},
        chooses=[ChooseSpec(kind="count", from_set=["str", "dex", "con", "int", "wis"], count=2)],
    )
    dwarf = AbilityOffer(flat={"con": 2})
    acolyte = AbilityOffer(
        chooses=[
            ChooseSpec(kind="weighted", from_set=["int", "wis", "cha"], weights=[2, 1]),
            ChooseSpec(kind="weighted", from_set=["int", "wis", "cha"], weights=[1, 1, 1]),
        ]
    )
    return _FakeKB(
        {
            "race": {"人类", "精灵", "矮人", "半精灵"},
            "class": {"法师", "战士", "游侠"},
            "background": {"士兵", "流浪儿", "侍僧"},
        },
        abilities={
            "race": {"半精灵": half_elf, "矮人": dwarf},
            "background": {"侍僧": acolyte},
        },
        class_combat={
            "法师": dict(hd_faces=6, caster="full", spell_ability="int"),
            "战士": dict(hd_faces=10, caster="", spell_ability=""),
        },
    )


def _roll_fake():
    """确定性代骰回调：返回固定池子。"""
    pool = [15, 14, 13, 12, 10, 8]

    def _roll(expr: str) -> tuple[int | None, str]:
        value = pool.pop(0) if pool else 8
        return value, f"{expr}=[6,5,4,{value-15+6 if value > 14 else 1}]→{value}"

    return _roll


def run(coro):
    return asyncio.run(coro)


def make_manager(rule_edition: str = "2014", kind: str = "point_buy", **kw) -> ChargenManager:
    star = _KVStar()
    cm = CharacterManager(star=star)
    cg = ChargenManager(
        star=star,
        character_manager=cm,
        kb_manager=_kb_for(),
        roll_fn=_roll_fake(),
    )
    rule = ChargenRule(
        edition=rule_edition,
        ability=AbilityGenMethod(kind=kind, **kw),
    )
    run(cg.set_rule(_Event(), rule))
    return cg


class TestRuleModel:
    def test_roundtrip(self) -> None:
        rule = ChargenRule(
            edition="2024",
            ability=AbilityGenMethod(kind="roll", expr="5d6kh3", count=4),
            subclass_at_creation="on",
            starting_level=3,
        )
        assert ChargenRule.from_dict(rule.to_dict()).to_dict() == rule.to_dict()

    def test_from_dict_tolerant(self) -> None:
        rule = ChargenRule.from_dict({"edition": "xxx", "ability": "junk", "starting_level": "99"})
        assert rule.edition == "2014"
        assert rule.ability.kind == "point_buy"
        assert rule.starting_level == 20  # 夹取

    def test_default_rule(self) -> None:
        rule = ChargenRule()
        assert rule.edition == "2014"
        assert rule.ability.kind == "point_buy"
        assert rule.ability.pool == 27
        assert rule.subclass_at_creation == "auto"  # 默认按规则等级
        assert "27 点" in rule.format()
        assert "2014" in rule.format()

    def test_point_buy_cost_table(self) -> None:
        assert POINT_BUY_COST == {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}


class TestRuleEdition:
    """v0.48.0：get_rule_edition —— 区分「显式设版本」与「未设规则」。"""

    def test_value_when_rule_set(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        cg = ChargenManager(star=star, character_manager=cm)
        run(cg.set_rule(_Event(origin="group:1"), ChargenRule(edition="2024")))
        assert run(cg.get_rule_edition(_Event(origin="group:1"))) == "2024"
        assert run(cg.get_rule_edition(_Event(origin="group:1"))) != "2014"

    def test_none_when_missing(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        cg = ChargenManager(star=star, character_manager=cm)
        # 私聊/未设规则：无 KV 数据 → None（调用方据此取最新版）
        assert run(cg.get_rule_edition(_Event(origin="private:u1"))) is None

    def test_invalid_value_normalized_to_2014(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        cg = ChargenManager(star=star, character_manager=cm)
        # 非法版本在模型层被归一化为 2014（__post_init__），不抛异常
        run(cg.set_rule(_Event(origin="group:9"), ChargenRule(edition="1999")))
        assert run(cg.get_rule_edition(_Event(origin="group:9"))) == "2014"


class TestAliases:
    def test_registry_contents(self) -> None:
        assert ABILITY_GEN_ALIASES["27buy"].pool == 27
        assert ABILITY_GEN_ALIASES["32buy"].pool == 32
        assert ABILITY_GEN_ALIASES["dnd5"].kind == "roll"
        assert ABILITY_GEN_ALIASES["dnd5"].expr == "4d6kh3"
        assert ABILITY_GEN_ALIASES["dnd5"].count == 6
        assert ABILITY_GEN_ALIASES["标准数组"].kind == "standard_array"


class TestParseRuleEdit:
    def test_set_edition(self) -> None:
        rule = ChargenRule()
        new, msg = parse_rule_edit(rule, ["版本", "2024"])
        assert new is not None and new.edition == "2024"
        assert "2024" in msg

    def test_invalid_edition(self) -> None:
        _, msg = parse_rule_edit(ChargenRule(), ["版本", "1999"])
        assert "无效的版本" in msg

    def test_set_alias(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["属性", "32buy"])
        assert new is not None and new.ability.pool == 32
        new2, _ = parse_rule_edit(rule, ["属性", "dnd5"])
        assert new2 is not None and new2.ability.kind == "roll"

    def test_custom_point_buy(self) -> None:
        rule = ChargenRule()
        new, msg = parse_rule_edit(rule, ["属性", "购点", "池=32", "上限=17"])
        assert new is not None
        assert new.ability.kind == "point_buy"
        assert new.ability.pool == 32
        assert new.ability.max_score == 17
        # 缺点数池报错
        _, msg = parse_rule_edit(rule, ["属性", "购点", "下限=8"])
        assert "点数池" in msg

    def test_custom_roll_with_validation(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["属性", "掷骰", "5d6kh3", "5"], validate_expr=lambda e: True)
        assert new is not None and new.ability.expr == "5d6kh3" and new.ability.count == 5
        _, msg = parse_rule_edit(rule, ["属性", "掷骰", "bad"], validate_expr=lambda e: False)
        assert "无法解析" in msg

    def test_subclass_timing(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["子职时机", "开"])
        assert new is not None and new.subclass_at_creation == "on"
        new2, _ = parse_rule_edit(rule, ["子职时机", "按规则"])
        assert new2 is not None and new2.subclass_at_creation == "auto"

    def test_starting_level(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["起始等级", "5"])
        assert new is not None and new.starting_level == 5
        _, msg = parse_rule_edit(rule, ["起始等级", "99"])
        assert "无效的起始等级" in msg

    def test_starting_gold_three_modes(self) -> None:
        rule = ChargenRule()
        # 自动（默认）
        new, msg = parse_rule_edit(rule, ["起始金币", "自动"])
        assert new is not None and new.starting_gold == "auto"
        assert "起始金币" in msg
        # 固定金额
        new, _ = parse_rule_edit(rule, ["起始金币", "150"])
        assert new is not None and new.starting_gold == "150"
        # 自定义骰式（主表达式过校验）
        new, _ = parse_rule_edit(rule, ["起始金币", "5d4×10"], validate_expr=lambda e: True)
        assert new is not None and new.starting_gold == "5d4×10"
        # 非法骰式被拒
        _, msg = parse_rule_edit(rule, ["起始金币", "bad"], validate_expr=lambda e: False)
        assert "无效的起始金币" in msg
        # 完全非法值
        _, msg = parse_rule_edit(rule, ["起始金币", "abc"])
        assert "无效的起始金币" in msg

    def test_starting_gold_compact(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["起始金币150"])
        assert new is not None and new.starting_gold == "150"

    def test_starting_gold_model_validation(self) -> None:
        # __post_init__ 容错：非法回退 auto
        assert ChargenRule(starting_gold="junk").starting_gold == "auto"
        assert ChargenRule(starting_gold="150").starting_gold == "150"
        assert ChargenRule(starting_gold="5d4x10").starting_gold == "5d4x10"
        # from_dict 容错
        assert ChargenRule.from_dict({"starting_gold": "200"}).starting_gold == "200"
        assert ChargenRule.from_dict({}).starting_gold == "auto"
        # roundtrip
        rule = ChargenRule(starting_gold="5d4×10")
        assert ChargenRule.from_dict(rule.to_dict()).starting_gold == "5d4×10"
        # format 展示
        assert "起始金币：固定 150 金币" in ChargenRule(starting_gold="150").format()
        assert "起始金币：按职业（自动）" in ChargenRule().format()
        assert "起始金币：骰式 5d4×10（金币）" in ChargenRule(starting_gold="5d4×10").format()

    def test_reset(self) -> None:
        rule = ChargenRule(edition="2024", starting_level=5)
        new, _ = parse_rule_edit(rule, ["重置"])
        assert new is not None and new == ChargenRule()

    def test_view(self) -> None:
        rule = ChargenRule()
        new, msg = parse_rule_edit(rule, [])
        assert new is None
        assert "开卡规则" in msg

    def test_unknown(self) -> None:
        _, msg = parse_rule_edit(ChargenRule(), ["xxx"])
        assert "未知的设置项" in msg


class TestParseRuleEditCompact:
    """紧凑写法容错：设置项与值之间无空格（版本2024 / 子职时机开 / 起始等级3）。"""

    def test_compact_edition(self) -> None:
        rule = ChargenRule()
        new, msg = parse_rule_edit(rule, ["版本2024"])
        assert new is not None and new.edition == "2024"
        assert "2024" in msg

    def test_compact_subclass(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["子职时机开"])
        assert new is not None and new.subclass_at_creation == "on"

    def test_compact_starting_level(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["起始等级3"])
        assert new is not None and new.starting_level == 3

    def test_compact_ability_alias(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["属性32buy"])
        assert new is not None and new.ability.pool == 32

    def test_compact_ability_custom_point_buy(self) -> None:
        rule = ChargenRule()
        new, _ = parse_rule_edit(rule, ["属性购点", "池=32", "上限=17"])
        assert new is not None
        assert new.ability.kind == "point_buy"
        assert new.ability.pool == 32
        assert new.ability.max_score == 17

    def test_compact_unknown_still_rejected(self) -> None:
        _, msg = parse_rule_edit(ChargenRule(), ["版本2024x"])
        assert "无效的版本" in msg
        _, msg2 = parse_rule_edit(ChargenRule(), ["版2024"])
        assert "未知的设置项" in msg2


class TestValidators:
    def test_point_buy_exact_pool(self) -> None:
        m = AbilityGenMethod(kind="point_buy", pool=27)
        # 15 14 13 12 10 8 → 9+7+5+4+2+0 = 27 恰好通过
        assert validate_point_buy([15, 14, 13, 12, 10, 8], m) is None

    def test_point_buy_over_pool_rejected(self) -> None:
        m = AbilityGenMethod(kind="point_buy", pool=27)
        # 15 15 14 14 8 8 → 9+9+7+7+0+0 = 32 > 27
        err = validate_point_buy([15, 15, 14, 14, 8, 8], m)
        assert err is not None
        assert "32" in err and "27" in err
        assert "DM" in err or "车卡规则" in err  # 指向 DM 改规则

    def test_point_buy_out_of_range_rejected(self) -> None:
        m = AbilityGenMethod(kind="point_buy", pool=27)
        assert validate_point_buy([16, 14, 13, 12, 10, 8], m) is not None
        assert validate_point_buy([7, 14, 13, 12, 10, 8], m) is not None

    def test_point_buy_wrong_length(self) -> None:
        m = AbilityGenMethod(kind="point_buy", pool=27)
        assert validate_point_buy([15, 14, 13, 12, 10], m) is not None

    def test_standard_array_multiset(self) -> None:
        m = AbilityGenMethod(kind="standard_array")
        assert validate_standard_array([15, 14, 13, 12, 10, 8], m) is None
        assert validate_standard_array([8, 10, 12, 13, 14, 15], m) is None  # 顺序无关
        assert validate_standard_array([15, 15, 13, 12, 10, 8], m) is not None  # 缺 14

    def test_rolled_assign_multiset(self) -> None:
        pool = [15, 14, 13, 12, 10, 8]
        assert validate_rolled_assign([8, 10, 12, 13, 14, 15], pool) is None
        assert validate_rolled_assign([15, 14, 13, 12, 10, 9], pool) is not None  # 改了数字


class TestParseAbilityInput:
    def test_bare_numbers_in_order(self) -> None:
        scores, err = parse_ability_input("15 14 13 12 10 8")
        assert err == ""
        assert scores == [15, 14, 13, 12, 10, 8]  # 力/敏/体/智/感/魅

    def test_explicit_mapping(self) -> None:
        scores, err = parse_ability_input("", assign="力15 敏14 体13 智12 感10 魅8")
        assert err == ""
        assert scores == [15, 14, 13, 12, 10, 8]

    def test_full_names_mapping(self) -> None:
        scores, err = parse_ability_input("力量15 敏捷14 体质13 智力12 感知10 魅力8")
        assert err == ""
        assert scores == [15, 14, 13, 12, 10, 8]

    def test_missing_values(self) -> None:
        _, err = parse_ability_input("15 14 13")
        assert "6 个" in err or "不完整" in err

    def test_empty(self) -> None:
        _, err = parse_ability_input("")
        assert err

    def test_garbage(self) -> None:
        _, err = parse_ability_input("abc def")
        assert "无法识别" in err


class TestChargenFlow2014:
    def _flow(self) -> tuple[ChargenManager, _Event]:
        cg, ev = make_manager("2014", "point_buy"), _Event()
        run(cg.start(ev))  # 建立草稿（CONFIRM 步）
        return cg, ev

    def test_full_flow_saves_card_and_clears_draft(self) -> None:
        cg, ev = self._flow()
        steps = [
            ("确认", None),
            ("人类", None),
            ("法师", None),
            ("士兵", None),
            ("15 14 13 12 10 8", None),
            ("守序善良", None),
            ("双亲是面包师", None),
            ("为了报仇选择学法术", None),
            ("流浪三年后加入冒险团", None),
            ("阿尔文", None),
        ]
        last = None
        for answer, _ in steps:
            last = run(cg.advance(ev, answer))
            assert last.done is False or answer == "阿尔文"
        assert last is not None and last.done is True
        # 草稿已删除
        assert run(cg.get_draft(ev)) is None
        # 卡片已落库且活跃
        card = run(cg._characters.get_card(ev))
        assert card is not None
        assert card.name == "阿尔文"
        assert card.race == "人类"
        assert card.classes[0].class_name == "法师"
        assert card.background == "士兵"
        assert card.alignment == "守序善良"
        assert card.ability_scores.get("str") == 15
        assert card.ability_scores.get("cha") == 8
        assert "出身" in card.backstory and "人生经历" in card.backstory

    def test_point_buy_over_pool_hard_reject_does_not_advance(self) -> None:
        cg, ev = self._flow()
        for answer in ("确认", "人类", "法师", "士兵"):
            run(cg.advance(ev, answer))
        reply = run(cg.advance(ev, "15 15 14 14 8 8"))  # 32 > 27
        assert "未接受" in reply.check
        assert "超过" in reply.check
        # 状态未推进：仍在分配步
        draft = run(cg.get_draft(ev))
        assert draft.state == "ABILITY_ASSIGN"
        # 改用合规分配通过
        reply2 = run(cg.advance(ev, "15 14 13 12 10 8"))
        assert "已接受属性分配" in reply2.check
        assert run(cg.get_draft(ev)).state == "ALIGNMENT"

    def test_unknown_race_rejected(self) -> None:
        cg, ev = self._flow()
        run(cg.advance(ev, "确认"))
        reply = run(cg.advance(ev, "不存在的种族"))
        assert "未接受" in reply.check
        assert run(cg.get_draft(ev)).state == "RACE"

    def test_order_mismatch_rejected(self) -> None:
        cg, ev = self._flow()
        for answer in ("确认", "人类", "法师", "士兵"):
            run(cg.advance(ev, answer))
        # 直接提交阵营（跳过分配步）→ 解析失败不推进
        reply = run(cg.advance(ev, "守序善良"))
        assert "未接受" in reply.check or "6 个" in reply.check
        assert run(cg.get_draft(ev)).state == "ABILITY_ASSIGN"

    def test_cancel(self) -> None:
        cg, ev = self._flow()
        run(cg.advance(ev, "确认"))
        reply = run(cg.cancel(ev))
        assert "已取消" in reply.check
        assert run(cg.get_draft(ev)) is None
        # 取消后再 advance → 未开始提示
        reply2 = run(cg.advance(ev, "人类"))
        assert "未开始" in reply2.progress

    def test_start_overwrites_old_draft(self) -> None:
        cg, ev = self._flow()
        run(cg.advance(ev, "确认"))
        reply = run(cg.start(ev))
        assert "已开始" in reply.check
        assert run(cg.get_draft(ev)).state == "CONFIRM"

    def test_start_prefill_full_2014_skips_to_ability(self) -> None:
        """v0.35.0：2014 全预填（种族/职业/背景）→ 跳到属性分配步。"""
        cg, ev = self._flow()
        reply = run(cg.start(
            ev,
            prefill={"race": "人类", "class_name": "法师", "background": "士兵"},
        ))
        draft = run(cg.get_draft(ev))
        assert draft.state == "ABILITY_ASSIGN"
        assert draft.data["race"] == "人类"
        assert draft.data["class_name"] == "法师"
        assert draft.data["background"] == "士兵"
        assert "已预填" in reply.check and "已完成前置步骤" in reply.check

    def test_start_prefill_invalid_class_breaks_chain(self) -> None:
        """v0.35.0：非法预填项被忽略并停在对应步（链式预填不跳空）。"""
        cg, ev = self._flow()
        reply = run(cg.start(
            ev,
            prefill={"race": "人类", "class_name": "不存在的职业", "background": "士兵"},
        ))
        draft = run(cg.get_draft(ev))
        assert draft.state == "CLASS"
        assert draft.data.get("race") == "人类"  # 前置合法项已预填
        assert draft.data.get("class_name") != "不存在的职业"
        assert "不是有效的职业" in reply.check
        assert "需重答" in reply.check

    def test_start_prefill_unknown_key_ignored(self) -> None:
        """v0.35.0：未知键不影响（停在第一个正式步骤之前：CONFIRM 后的第一问）。"""
        cg, ev = self._flow()
        reply = run(cg.start(ev, prefill={"xxx": "yyy"}))
        draft = run(cg.get_draft(ev))
        assert draft.state == "RACE"
        assert "已开始" in reply.check or "预填" in reply.check

    def test_duplicate_card_name_rejected_at_name_step(self) -> None:
        cg, ev = self._flow()
        # 先造一张同名卡
        run(cg._characters.save_card(ev, CharacterSheet(name="阿尔文")))
        for answer in ("确认", "人类", "法师", "士兵", "15 14 13 12 10 8", "守序善良",
                       "出身", "决定", "经历"):
            run(cg.advance(ev, answer))
        reply = run(cg.advance(ev, "阿尔文"))
        assert "未接受" in reply.check
        assert "已有一张名为" in reply.check


class TestChargenFlow2024:
    def test_full_flow_with_origin_steps(self) -> None:
        cg, ev = make_manager("2024", "point_buy"), _Event()
        run(cg.start(ev))
        for answer in ("确认", "法师", "侍僧", "人类", "15 14 13 12 10 8",
                       "中立善良", "出身", "决定", "经历", "阿尔文"):
            reply = run(cg.advance(ev, answer))
        assert reply.done is True
        card = run(cg._characters.get_card(ev))
        assert card is not None
        assert card.edition == "2024"
        assert card.background == "侍僧"
        assert card.race == "人类"  # 2024 物种写入 race 字段
        assert card.alignment == "中立善良"

    def test_start_prefill_full_2024_writes_species(self) -> None:
        """v0.35.0：2024 全预填（职业/背景/种族）→ 物种写入 species、跳到属性步。"""
        cg, ev = make_manager("2024", "point_buy"), _Event()
        reply = run(cg.start(
            ev,
            prefill={"race": "人类", "class_name": "法师", "background": "侍僧"},
        ))
        draft = run(cg.get_draft(ev))
        assert draft.state == "ABILITY_ASSIGN"
        assert draft.data["class_name"] == "法师"
        assert draft.data["background"] == "侍僧"
        assert draft.data["species"] == "人类"
        assert "已预填" in reply.check and "已完成前置步骤" in reply.check


class TestChargenFlowRoll:
    def test_roll_flow_assigns_dice_pool(self) -> None:
        cg, ev = make_manager("2014", "roll", expr="4d6kh3", count=6), _Event()
        run(cg.start(ev))
        for answer in ("确认", "人类", "法师", "士兵"):
            run(cg.advance(ev, answer))
        # 代骰步
        reply = run(cg.advance(ev, "骰"))
        assert "已代骰" in reply.check
        draft = run(cg.get_draft(ev))
        assert sorted(draft.ability_pool, reverse=True) == [15, 14, 13, 12, 10, 8]
        # 分配必须原样使用池子
        reply = run(cg.advance(ev, "15 14 13 12 10 8"))
        assert "已接受属性分配" in reply.check
        # 自报数字被拒
        cg2, ev2 = make_manager("2014", "roll", expr="4d6kh3", count=6), _Event()
        run(cg2.start(ev2))
        for answer in ("确认", "人类", "法师", "士兵"):
            run(cg2.advance(ev2, answer))
        run(cg2.advance(ev2, "骰"))
        reply2 = run(cg2.advance(ev2, "18 18 18 8 8 8"))
        assert "未接受" in reply2.check
        assert "代骰" in reply2.check

    def test_roll_failure_reports(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        cg = ChargenManager(star=star, character_manager=cm, kb_manager=_kb_for(),
                            roll_fn=lambda e: (None, "掷骰错误: 爆炸"))
        run(cg.set_rule(_Event(), ChargenRule(ability=AbilityGenMethod(kind="roll"))))
        ev = _Event()
        run(cg.start(ev))
        for answer in ("确认", "人类", "法师", "士兵"):
            run(cg.advance(ev, answer))
        reply = run(cg.advance(ev, "骰"))
        assert "未接受" in reply.check
        assert "掷骰错误" in reply.check


class TestChargenStatus:
    def test_status_no_draft(self) -> None:
        cg, ev = make_manager(), _Event()
        reply = run(cg.status(ev))
        assert "未开始" in reply.progress

    def test_status_shows_progress(self) -> None:
        cg, ev = make_manager(), _Event()
        run(cg.start(ev))
        run(cg.advance(ev, "确认"))
        reply = run(cg.status(ev))
        assert "2/11" in reply.progress  # 2014: CONFIRM=1, RACE=2（v0.18 加 ABILITY_BONUS）
        assert "种族" in reply.progress

    def test_2024_step_count(self) -> None:
        cg, ev = make_manager("2024"), _Event()
        run(cg.start(ev))
        run(cg.advance(ev, "确认"))
        reply = run(cg.status(ev))
        # 2024 路径同样 11 步（CLASS/ORIGIN_BG/ORIGIN_SPECIES 替代 RACE/CLASS/BACKGROUND）
        assert "2/11" in reply.progress
        assert "职业" in reply.progress

    def test_dirty_state_advance_falls_back(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        cg = ChargenManager(star=star, character_manager=cm, kb_manager=_kb_for())
        ev = _Event()
        run(star.put_kv_data("character:draft:group:1:u1", {"state": "NOT_A_STATE"}))
        reply = run(cg.advance(ev, "随便"))
        assert "未知状态" in reply.check


# ---------------------------------------------------------------------------
# v0.18：种族/背景加值选择（ABILITY_BONUS 步）
# ---------------------------------------------------------------------------


class TestBonusChoice:
    SPECS_2014 = [
        ChooseSpec(kind="count", from_set=["str", "dex", "con", "int", "wis"], count=2),
    ]
    SPECS_2024 = [
        ChooseSpec(kind="weighted", from_set=["int", "wis", "cha"], weights=[2, 1]),
        ChooseSpec(kind="weighted", from_set=["int", "wis", "cha"], weights=[1, 1, 1]),
    ]

    def test_count_valid(self) -> None:
        picks, err = parse_bonus_choice("力+1 敏+1", self.SPECS_2014)
        assert err == "" and picks == {"str": 1, "dex": 1}

    def test_count_wrong_amount_rejected(self) -> None:
        _, err = parse_bonus_choice("力+2 敏+1", self.SPECS_2014)
        assert "不匹配任何可选方案" in err

    def test_count_wrong_count_rejected(self) -> None:
        _, err = parse_bonus_choice("力+1 敏+1 体+1", self.SPECS_2014)
        assert "不匹配任何可选方案" in err

    def test_count_out_of_from_set_rejected(self) -> None:
        _, err = parse_bonus_choice("魅+1 敏+1", self.SPECS_2014)  # 魅力不在半精灵集合
        assert "不匹配任何可选方案" in err

    def test_weighted_order_free(self) -> None:
        picks, err = parse_bonus_choice("感+1 智+2", self.SPECS_2024)
        assert err == "" and picks == {"wis": 1, "int": 2}

    def test_weighted_duplicate_rejected(self) -> None:
        _, err = parse_bonus_choice("智+2 智+1", self.SPECS_2024)
        assert "不能重复选择" in err

    def test_weighted_three_way(self) -> None:
        picks, err = parse_bonus_choice("智+1 感+1 魅+1", self.SPECS_2024)
        assert err == "" and picks == {"int": 1, "wis": 1, "cha": 1}

    def test_weighted_wrong_amounts_rejected(self) -> None:
        _, err = parse_bonus_choice("智+1 感+1", self.SPECS_2024)  # 只给了 +1/+1
        assert "不匹配任何可选方案" in err

    def test_garbage_input(self) -> None:
        _, err = parse_bonus_choice("hello world", self.SPECS_2014)
        assert "无法识别" in err

    def test_empty_input(self) -> None:
        _, err = parse_bonus_choice("", self.SPECS_2014)
        assert "请选择加值方案" in err


class TestChargenFlowBonus:
    def _manager(self, edition: str = "2014"):
        star = _KVStar()
        cm = CharacterManager(star=star)
        cg = ChargenManager(
            star=star,
            character_manager=cm,
            kb_manager=_kb_with_abilities(),
            roll_fn=_roll_fake(),
        )
        run(cg.set_rule(_Event(), ChargenRule(edition=edition)))
        return cg

    def test_2014_half_elf_bonus_step(self) -> None:
        cg, ev = self._manager("2014"), _Event()
        run(cg.start(ev))
        for a in ("确认", "半精灵", "法师", "士兵"):
            run(cg.advance(ev, a))
        reply = run(cg.advance(ev, "15 14 13 12 10 8"))
        assert "自选加值" in reply.check
        assert run(cg.get_draft(ev)).state == "ABILITY_BONUS"
        # 非法选择硬拒绝，不推进
        bad = run(cg.advance(ev, "力+2 敏+1"))
        assert "未接受" in bad.check
        assert run(cg.get_draft(ev)).state == "ABILITY_BONUS"
        # 合法选择推进
        ok = run(cg.advance(ev, "力+1 敏+1"))
        assert "已接受加值选择" in ok.check
        assert run(cg.get_draft(ev)).state == "ALIGNMENT"
        # 走完落库：加值自动叠加（力15+1=16、敏14+1=15、魅8+2=10）
        last = None
        for a in ("守序善良", "出身", "决定", "经历", "阿尔文"):
            last = run(cg.advance(ev, a))
        assert last.done is True
        card = run(cg._characters.get_card(ev))
        assert card.ability_scores.get("str") == 16
        assert card.ability_scores.get("dex") == 15
        assert card.ability_scores.get("cha") == 10

    def test_2014_flat_only_skips_bonus_step(self) -> None:
        cg, ev = self._manager("2014"), _Event()
        run(cg.start(ev))
        for a in ("确认", "矮人", "战士", "士兵"):
            run(cg.advance(ev, a))
        reply = run(cg.advance(ev, "15 14 13 12 10 8"))
        assert "自动应用固定加值" in reply.check  # 无 choose，跳过加值选择步
        assert run(cg.get_draft(ev)).state == "ALIGNMENT"
        last = None
        for a in ("守序善良", "出身", "决定", "经历", "阿尔文"):
            last = run(cg.advance(ev, a))
        assert last.done is True
        card = run(cg._characters.get_card(ev))
        assert card.ability_scores.get("con") == 15  # 13 + 矮人 +2

    def test_2024_background_weighted(self) -> None:
        cg, ev = self._manager("2024"), _Event()
        run(cg.start(ev))
        for a in ("确认", "法师", "侍僧", "人类", "15 14 13 12 10 8"):
            run(cg.advance(ev, a))
        assert run(cg.get_draft(ev)).state == "ABILITY_BONUS"
        # 力不在背景可选集合（int/wis/cha）
        bad = run(cg.advance(ev, "智+2 力+1"))
        assert "未接受" in bad.check
        ok = run(cg.advance(ev, "智+2 感+1"))
        assert "已接受加值选择" in ok.check
        assert run(cg.get_draft(ev)).state == "ALIGNMENT"
        last = None
        for a in ("中立善良", "出身", "决定", "经历", "阿尔文"):
            last = run(cg.advance(ev, a))
        assert last.done is True
        card = run(cg._characters.get_card(ev))
        assert card.ability_scores.get("int") == 14  # 12 + 2
        assert card.ability_scores.get("wis") == 11  # 10 + 1
        assert card.ability_scores.get("cha") == 8  # 未选

    def test_finalize_engine_computes_base(self) -> None:
        """落库时规则引擎自动算战斗字段 base（法师 1 级）。"""
        cg, ev = self._manager("2024"), _Event()
        run(cg.start(ev))
        for a in ("确认", "法师", "侍僧", "人类", "15 14 13 12 10 8", "智+2 感+1",
                  "中立善良", "出身", "决定", "经历", "阿尔文"):
            last = run(cg.advance(ev, a))
        assert last.done is True
        card = run(cg._characters.get_card(ev))
        assert card.hp_max.base > 0  # 法师 hd6 + 体修
        assert card.ac.base == 10 + AbilityScores.modifier(14)  # 无甲 10+敏
        assert card.spell_slots["1"].base == 2  # full 1 级
        assert card.attack_bonuses["法师法术攻击"].base > 0

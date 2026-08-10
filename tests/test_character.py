"""角色卡（/卡）数据模型与 KV 管理器单元测试。

覆盖点：
  - AbilityScores / CharacterSheet 序列化往返与脏数据容错。
  - 派生属性（等级/熟练加值/技能与豁免修正/被动感知）。
  - CharacterManager 多卡 CRUD：索引一致性、活跃切换、删除回退、改名。
  - update_fields 字段白名单（hp/ac/slotN/attack/装备槽/生平/阵营/熟练/命名掷骰）。
  - resolve_roll_alias 别名识别（属性/技能/豁免，紧凑写法不误伤）。
"""

from __future__ import annotations

import asyncio

from astrbot_plugin_trpg_assistant.character import (
    ABILITY_ALIAS,
    ABILITY_CN,
    ABILITY_NAMES,
    AbilityScores,
    CharacterManager,
    CharacterSheet,
    ClassLevel,
    EquipmentSlots,
    LayeredStat,
    SKILL_ALIAS,
    SKILL_CN,
    SKILLS,
    resolve_roll_alias,
)


class _KVStar:
    """内存 KV 替身（模拟 AstrBot get/put/delete_kv_data）。"""

    def __init__(self) -> None:
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


class _Event:
    def __init__(
        self, origin: str = "group:1", sender_id: str = "u1", sender_name: str = "Alice"
    ) -> None:
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name
        self.message_str = ""

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_private_chat(self) -> bool:
        return False

    def plain_result(self, text: str) -> str:
        return text


def run(coro):
    return asyncio.run(coro)


def make_sheet(**overrides) -> CharacterSheet:
    base = dict(
        name="阿尔文",
        edition="2014",
        classes=[ClassLevel(class_name="法师", subclass="塑能", level=3)],
        race="人类",
        background="士兵",
        alignment="守序善良",
        ability_scores=AbilityScores(strength=8, dexterity=14, constitution=12,
                                     intelligence=16, wisdom=10, charisma=13),
        skill_proficiencies={"arcana", "history"},
        save_proficiencies={"int", "wis"},
        hp_max=LayeredStat(bonus=22),
        ac=LayeredStat(bonus=14),
        spell_slots={"1": LayeredStat(bonus=4), "2": LayeredStat(bonus=3)},
        attack_bonuses={"火球术": LayeredStat(bonus=7)},
        equipment=EquipmentSlots(main_hand="匕首", armor="皮甲"),
        backstory="【出身】孤儿\n【人生经历】学徒",
        named_rolls={"侦查": "1d20+2"},
    )
    base.update(overrides)
    return CharacterSheet(**base)


class TestLevelUp:
    """v0.18：/卡 升级 → CharacterManager.level_up（等级 +1 + 整卡重算回调）。"""

    def _manager_with_card(self):
        star = _KVStar()
        cm = CharacterManager(star=star)
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        return cm, ev

    def test_level_up_default_main_class(self) -> None:
        cm, ev = self._manager_with_card()
        card, report, err = run(cm.level_up(ev, None, ""))
        assert err is None
        assert card.classes[0].class_name == "法师"
        assert card.classes[0].level == 4

    def test_level_up_specified_existing_class(self) -> None:
        cm, ev = self._manager_with_card()
        run(cm.level_up(ev, None, "战士"))  # 新增兼职
        card, _, err = run(cm.level_up(ev, None, "战士"))
        assert err is None
        assert [(c.class_name, c.level) for c in card.classes] == [("法师", 3), ("战士", 2)]

    def test_level_up_new_multiclass_appends(self) -> None:
        cm, ev = self._manager_with_card()
        card, _, err = run(cm.level_up(ev, None, "战士"))
        assert err is None
        assert [(c.class_name, c.level) for c in card.classes] == [("法师", 3), ("战士", 1)]

    def test_level_up_class_cap_20(self) -> None:
        star = _KVStar()
        mgr = CharacterManager(star=star)
        ev = _Event()
        run(mgr.save_card(ev, make_sheet(classes=[ClassLevel(class_name="法师", level=20)])))
        _, _, err = run(mgr.level_up(ev, None, ""))
        assert err is not None and "上限" in err

    def test_level_up_recalc_fn_called_with_updated_level(self) -> None:
        cm, ev = self._manager_with_card()
        calls: list[int] = []

        def recalc(sheet):
            calls.append(sheet.classes[0].level)
            sheet.hp_max.base = sheet.classes[0].level * 10
            return "ok"

        card, report, err = run(cm.level_up(ev, None, "", recalc_fn=recalc))
        assert err is None
        assert calls == [4]  # 重算发生在等级 +1 之后
        assert card.hp_max.base == 40

    def test_level_up_preserves_bonus_layer(self) -> None:
        cm, ev = self._manager_with_card()
        card, _, err = run(cm.level_up(ev, None, ""))
        assert err is None
        assert card.hp_max.bonus == 22  # bonus 是房规层，升级重算不动
        assert card.ac.bonus == 14

    def test_level_up_no_card(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        _, _, err = run(cm.level_up(_Event(), None, ""))
        assert err is not None and "角色卡" in err

    def test_level_up_no_classes(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        ev = _Event()
        run(cm.save_card(ev, make_sheet(classes=[])))
        _, _, err = run(cm.level_up(ev, None, ""))
        assert err is not None and "职业" in err

    def test_level_up_active_card_targets_by_name(self) -> None:
        cm, ev = self._manager_with_card()
        # name 参数指定非活跃卡（活跃卡之外另存一张）
        run(cm.save_card(ev, make_sheet(name="备用卡", classes=[ClassLevel(class_name="战士", level=2)])))
        card, _, err = run(cm.level_up(ev, "备用卡", ""))
        assert err is None
        assert card.name == "备用卡"
        assert card.classes[0].level == 3


class TestLevelDown:
    """v0.24.0：/卡 降级 → CharacterManager.level_down（等级 -1 + 重算回调）。"""

    def _manager_with_card(self, level: int = 3) -> tuple:
        star = _KVStar()
        cm = CharacterManager(star=star)
        ev = _Event()
        run(cm.save_card(ev, make_sheet(classes=[ClassLevel(class_name="法师", level=level)])))
        return cm, ev

    def test_level_down_default_main_class(self) -> None:
        cm, ev = self._manager_with_card(level=3)
        card, _, err = run(cm.level_down(ev, None, ""))
        assert err is None
        assert card.classes[0].level == 2

    def test_level_down_specified_class(self) -> None:
        cm, ev = self._manager_with_card(level=5)
        card, _, err = run(cm.level_down(ev, None, "法师"))
        assert err is None
        assert card.classes[0].level == 4

    def test_level_down_min_level_1(self) -> None:
        cm, ev = self._manager_with_card(level=1)
        card, _, err = run(cm.level_down(ev, None, ""))
        assert err is not None and "1 级" in err
        assert card is None

    def test_level_down_missing_class(self) -> None:
        cm, ev = self._manager_with_card(level=3)
        _, _, err = run(cm.level_down(ev, None, "战士"))
        assert err is not None and "战士" in err

    def test_level_down_no_classes(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        ev = _Event()
        run(cm.save_card(ev, make_sheet(classes=[])))
        _, _, err = run(cm.level_down(ev, None, ""))
        assert err is not None and "职业" in err


class TestAbilityScores:
    def test_modifier_formula(self) -> None:
        assert AbilityScores.modifier(10) == 0
        assert AbilityScores.modifier(8) == -1
        assert AbilityScores.modifier(15) == 2
        assert AbilityScores.modifier(20) == 5
        assert AbilityScores.modifier(1) == -5

    def test_get_and_set(self) -> None:
        s = AbilityScores()
        s.set("str", 18)
        assert s.get("str") == 18
        try:
            s.get("xyz")
            assert False
        except ValueError:
            pass

    def test_clamp(self) -> None:
        s = AbilityScores(strength=99, dexterity=-5, constitution="abc")
        assert s.strength == 30
        assert s.dexterity == 1  # 负值夹取到下限 1
        assert s.constitution == 10  # 非法 → 默认

    def test_from_dict_tolerant(self) -> None:
        s = AbilityScores.from_dict({"strength": "18", "wisdom": None})
        assert s.strength == 18
        assert s.wisdom == 10


class TestCharacterSheetSerialization:
    def test_roundtrip(self) -> None:
        sheet = make_sheet()
        restored = CharacterSheet.from_dict(sheet.to_dict())
        assert restored.to_dict() == sheet.to_dict()

    def test_from_dict_non_dict_returns_default(self) -> None:
        sheet = CharacterSheet.from_dict("not a dict")
        assert sheet.name == "未知冒险者"
        assert sheet.edition == "2014"

    def test_dirty_data_tolerated(self) -> None:
        sheet = CharacterSheet.from_dict(
            {
                "name": "  脏卡\n",  # 控制字符清洗
                "edition": "9999",  # 非法版本 → 2014
                "classes": [{"class_name": "战士", "level": "2"}, "junk", None],
                "skill_proficiencies": ["arcana", "not_a_skill", "PERCEPTION"],
                "save_proficiencies": ["str", "xyz"],
                "hp_max": "bad",
                "ac": {"base": "x", "bonus": 3},
                "spell_slots": {"1": {"bonus": 2}, "2": "junk"},
                "attack_bonuses": {"长剑": {"bonus": 5}},
                "equipment": "bad",
                "named_rolls": {"a": "1d20"},
            }
        )
        assert sheet.name == "脏卡"
        assert sheet.edition == "2014"
        assert len(sheet.classes) == 1
        assert sheet.classes[0].class_name == "战士"
        assert sheet.classes[0].level == 2
        assert sheet.skill_proficiencies == {"arcana", "perception"}
        assert sheet.save_proficiencies == {"str"}
        assert sheet.hp_max.total == 0
        assert sheet.ac.total == 3
        assert sheet.spell_slots["1"].total == 2
        assert "2" not in sheet.spell_slots  # 脏子条目跳过
        assert sheet.attack_bonuses["长剑"].total == 5
        assert sheet.equipment.main_hand == ""

    def test_proficiency_bonus_by_level(self) -> None:
        assert CharacterSheet().proficiency_bonus == 2
        sheet = CharacterSheet(classes=[ClassLevel(class_name="战士", level=5)])
        assert sheet.proficiency_bonus == 3
        sheet2 = CharacterSheet(classes=[ClassLevel(class_name="战士", level=9)])
        assert sheet2.proficiency_bonus == 4
        sheet3 = CharacterSheet(classes=[ClassLevel(class_name="战士", level=17)])
        assert sheet3.proficiency_bonus == 6

    def test_derived_modifiers(self) -> None:
        sheet = make_sheet()  # 3 级 → 熟练加值 2
        # 智力 16 → +3，奥秘熟练 → 3+2=5
        assert sheet.get_skill_modifier("arcana") == 5
        # 力量 8 → -1，运动不熟练 → -1
        assert sheet.get_skill_modifier("athletics") == -1
        # 智力豁免熟练 → 3+2=5
        assert sheet.get_save_modifier("int") == 5
        # 感知豁免熟练 → 0+2=2
        assert sheet.get_save_modifier("wis") == 2
        # 被动感知 = 10 + (感知 10 → 0) = 10（察觉不熟练）
        assert sheet.passive_perception == 10

    def test_level_sum_with_multiclass(self) -> None:
        sheet = CharacterSheet(
            classes=[
                ClassLevel(class_name="战士", level=3),
                ClassLevel(class_name="法师", level=2),
            ]
        )
        assert sheet.level == 5
        assert sheet.proficiency_bonus == 3

    def test_empty_classes_level_falls_back_to_1(self) -> None:
        assert CharacterSheet().level == 1


class TestCharacterManagerCrud:
    def _manager(self):
        return CharacterManager(star=_KVStar())

    def test_save_card_auto_active(self) -> None:
        cm = self._manager()
        ev = _Event()
        err = run(cm.save_card(ev, make_sheet(name="阿尔文")))
        assert err is None
        card = run(cm.get_card(ev))
        assert card is not None and card.name == "阿尔文"
        assert run(cm.get_active_name(ev)) == "阿尔文"

    def test_multi_card_index_and_switch(self) -> None:
        cm = self._manager()
        ev = _Event()
        run(cm.save_card(ev, make_sheet(name="阿尔文")))
        run(cm.save_card(ev, make_sheet(name="二号卡")))
        names = run(cm.list_cards(ev))
        assert names == ["阿尔文", "二号卡"]
        assert run(cm.get_active_name(ev)) == "阿尔文"  # 第一张自动活跃
        assert run(cm.set_active(ev, "二号卡")) is True
        card = run(cm.get_card(ev))
        assert card.name == "二号卡"
        # 切换不存在的卡
        assert run(cm.set_active(ev, "不存在")) is False

    def test_delete_card_fallback_active(self) -> None:
        cm = self._manager()
        ev = _Event()
        run(cm.save_card(ev, make_sheet(name="阿尔文")))
        run(cm.save_card(ev, make_sheet(name="二号卡")))
        run(cm.set_active(ev, "阿尔文"))
        assert run(cm.delete_card(ev, "阿尔文")) is True
        # 活跃卡回退到列表第一张
        assert run(cm.get_active_name(ev)) == "二号卡"
        assert run(cm.delete_card(ev, "阿尔文")) is False  # 已删
        # 删除最后一张 → 活跃清空
        run(cm.delete_card(ev, "二号卡"))
        assert run(cm.get_active_name(ev)) is None
        assert run(cm.get_card(ev)) is None

    def test_rename_card(self) -> None:
        cm = self._manager()
        ev = _Event()
        run(cm.save_card(ev, make_sheet(name="阿尔文")))
        ok, msg = run(cm.rename_card(ev, "阿尔文", "新名"))
        assert ok is True
        assert "新名" in msg
        assert run(cm.get_active_name(ev)) == "新名"  # 活跃指针跟随
        assert run(cm.list_cards(ev)) == ["新名"]
        # 重名拒绝
        run(cm.save_card(ev, make_sheet(name="另一张")))
        ok2, _ = run(cm.rename_card(ev, "新名", "另一张"))
        assert ok2 is False

    def test_update_fields(self) -> None:
        cm = self._manager()
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        card, applied = run(cm.update_fields(ev, None, {"hp": 25}))
        assert card is not None and card.hp_max.bonus == 25
        card, applied = run(cm.update_fields(ev, None, {"slot1": 3}))
        assert card.spell_slots["1"].bonus == 3
        card, applied = run(cm.update_fields(ev, None, {"attack": "长剑=6"}))
        assert card.attack_bonuses["长剑"].bonus == 6
        card, applied = run(cm.update_fields(ev, None, {"main_hand": "长剑"}))
        assert card.equipment.main_hand == "长剑"
        card, applied = run(cm.update_fields(ev, None, {"main_hand": "-"}))
        assert card.equipment.main_hand == ""
        card, applied = run(cm.update_fields(ev, None, {"backstory": "新生平"}))
        assert card.backstory == "新生平"
        # 熟练覆盖
        card, applied = run(cm.update_fields(ev, None, {"skills": "arcana, stealth"}))
        assert card.skill_proficiencies == {"arcana", "stealth"}
        card, applied = run(cm.update_fields(ev, None, {"saves": "str, 敏"}))
        assert card.save_proficiencies == {"str", "dex"}
        # 命名掷骰
        card, applied = run(cm.update_fields(ev, None, {"named_roll": "侦查=1d20+2"}))
        assert card.named_rolls == {"侦查": "1d20+2"}
        # 无卡时返回 None
        cm2 = self._manager()
        card, applied = run(cm2.update_fields(_Event(sender_id="u9"), None, {"hp": 1}))
        assert card is None and applied == []

    def test_index_isolation_between_senders(self) -> None:
        cm = self._manager()
        run(cm.save_card(_Event(sender_id="u1"), make_sheet(name="阿尔文")))
        assert run(cm.list_cards(_Event(sender_id="u2"))) == []

    def test_dirty_kv_does_not_crash(self) -> None:
        star = _KVStar()
        cm = CharacterManager(star=star)
        ev = _Event()
        run(star.put_kv_data("character:group:1:u1:坏卡", "not a dict"))
        run(star.put_kv_data("character:index:group:1:u1", {"names": ["坏卡"]}))
        card = run(cm.get_card(ev, "坏卡"))
        assert card is None  # 脏数据 → 视为无卡，不抛异常


class TestResolveRollAlias:
    def test_ability_check(self) -> None:
        assert resolve_roll_alias("力量") == ("ability", "str", "")
        assert resolve_roll_alias("str 15") == ("ability", "str", "15")
        assert resolve_roll_alias("敏捷") == ("ability", "dex", "")

    def test_skill_check(self) -> None:
        assert resolve_roll_alias("察觉") == ("skill", "perception", "")
        assert resolve_roll_alias("隐匿 13") == ("skill", "stealth", "13")
        assert resolve_roll_alias("perception") == ("skill", "perception", "")

    def test_save_check(self) -> None:
        assert resolve_roll_alias("敏捷豁免") == ("save", "dex", "")
        assert resolve_roll_alias("力量豁免 15") == ("save", "str", "15")
        assert resolve_roll_alias("str save") == ("save", "str", "")

    def test_attack_check(self) -> None:
        # v0.22：攻击检定别名 → (attack, "", rest)，key 恒空由调用方解析
        assert resolve_roll_alias("攻击") == ("attack", "", "")
        assert resolve_roll_alias("攻击 长剑") == ("attack", "", "长剑")
        assert resolve_roll_alias("攻击 列表") == ("attack", "", "列表")
        assert resolve_roll_alias("attack") == ("attack", "", "")
        assert resolve_roll_alias("atk") == ("attack", "", "")
        assert resolve_roll_alias("Attack 长剑") == ("attack", "", "长剑")

    def test_named_roll_v32(self) -> None:
        # v0.32.0：传入 named_rolls 时整词命中优先于内建别名
        named = {"侦察": "1d20+2", "熟练度": "3d6+1"}
        assert resolve_roll_alias("侦察", named) == ("named", "侦察", "")
        assert resolve_roll_alias("侦察 15", named) == ("named", "侦察", "15")
        # 大小写归一（英文名）
        assert resolve_roll_alias("SCOUT", {"scout": "2d10"}) == ("named", "scout", "")
        # 覆盖内建：登记「察觉」后 /r 察觉 走命名掷骰
        assert resolve_roll_alias("察觉", {"察觉": "1d20+8"}) == ("named", "察觉", "")
        # 未命中命名 → 回退内建别名
        assert resolve_roll_alias("力量", {"侦察": "1d20+2"}) == ("ability", "str", "")
        # 不传 named_rolls 行为与旧版一致
        assert resolve_roll_alias("侦察") is None

    def test_compact_syntax_not_matched(self) -> None:
        # 紧凑写法（d20感知15）首 token 不是纯别名 → 不触发
        assert resolve_roll_alias("d20感知15") is None
        assert resolve_roll_alias("1d20+2 力量") is None  # 表达式开头
        assert resolve_roll_alias("") is None
        # 攻击不误伤：紧凑/复合词不触发
        assert resolve_roll_alias("d20攻击") is None
        assert resolve_roll_alias("攻击豁免") is None
        assert resolve_roll_alias("攻击长剑") is None
        # 命名掷骰同样只整词命中
        assert resolve_roll_alias("d20侦察", {"侦察": "1d20+2"}) is None

    def test_alias_tables_cover_everything(self) -> None:
        # 每个技能都有中文名与英文名可达
        for skill in SKILLS:
            assert skill in SKILL_ALIAS
            cn = next((k for k, v in SKILL_CN.items() if v == skill), None)
            assert cn is not None, f"技能 {skill} 缺中文名"
        for ab in ABILITY_NAMES:
            assert ab in ABILITY_ALIAS
            assert ABILITY_CN[ab] in ABILITY_ALIAS


class TestAttackRollMethods:
    """v0.22：角色卡攻击掷骰辅助方法（list_attacks/main_hand_attack/resolve_attack）。"""

    def _sheet(self) -> CharacterSheet:
        return CharacterSheet(
            name="阿尔文",
            attack_bonuses={
                "长剑": LayeredStat(base=4, bonus=1),  # total 5
                "匕首": LayeredStat(base=5),
                "长弓": LayeredStat(base=6),
                "法师法术攻击": LayeredStat(base=7),
                "+1 巨锤": LayeredStat(base=4),
            },
            equipment=EquipmentSlots(main_hand="长剑"),
        )

    def test_list_attacks_sorted_by_name(self) -> None:
        assert self._sheet().list_attacks() == [
            ("+1 巨锤", 4),
            ("匕首", 5),
            ("法师法术攻击", 7),
            ("长剑", 5),
            ("长弓", 6),
        ]

    def test_list_attacks_empty(self) -> None:
        assert CharacterSheet().list_attacks() == []

    def test_main_hand_attack_hit(self) -> None:
        assert self._sheet().main_hand_attack() == ("长剑", 5)

    def test_main_hand_empty(self) -> None:
        sheet = CharacterSheet(
            attack_bonuses={"长剑": LayeredStat(base=4)},
            equipment=EquipmentSlots(),
        )
        assert sheet.main_hand_attack() is None

    def test_main_hand_fallback_contains(self) -> None:
        # 主手「巨锤」与攻击表键「+1 巨锤」不一致 → 包含匹配兜底
        sheet = CharacterSheet(
            attack_bonuses={"+1 巨锤": LayeredStat(base=4)},
            equipment=EquipmentSlots(main_hand="巨锤"),
        )
        assert sheet.main_hand_attack() == ("+1 巨锤", 4)

    def test_main_hand_not_found(self) -> None:
        sheet = CharacterSheet(
            attack_bonuses={"长剑": LayeredStat(base=4)},
            equipment=EquipmentSlots(main_hand="法杖"),
        )
        assert sheet.main_hand_attack() is None

    def test_resolve_attack_exact(self) -> None:
        assert self._sheet().resolve_attack("长剑") == ("长剑", 5)

    def test_resolve_attack_prefix(self) -> None:
        assert self._sheet().resolve_attack("长弓") == ("长弓", 6)
        assert self._sheet().resolve_attack("+1") == ("+1 巨锤", 4)

    def test_resolve_attack_contains(self) -> None:
        assert self._sheet().resolve_attack("巨锤") == ("+1 巨锤", 4)

    def test_resolve_attack_miss(self) -> None:
        assert self._sheet().resolve_attack("权杖") is None
        assert self._sheet().resolve_attack("") is None


class TestNewProficiencyFields:
    """v0.21：专精/专长/工具·武器·防具熟练——序列化与兼容。"""

    def test_from_dict_missing_keys_defaults_empty(self) -> None:
        # 旧卡无五个新 key → 全空，行为与现状一致
        sheet = CharacterSheet.from_dict({"name": "旧卡", "skill_proficiencies": ["arcana"]})
        assert sheet.skill_expertise == set()
        assert sheet.feats == []
        assert sheet.tool_proficiencies == set()
        assert sheet.weapon_proficiencies == set()
        assert sheet.armor_proficiencies == set()

    def test_dirty_new_fields_cleaned(self) -> None:
        sheet = CharacterSheet.from_dict(
            {
                "skill_expertise": ["arcana", "not_a_skill", "PERCEPTION"],
                "feats": ["巨武器大师", "幸运", "巨武器大师", 123, ""],
                "tool_proficiencies": ["盗贼工具", "", 42],
                "weapon_proficiencies": "长剑",  # 字符串 → 空集（防逐字拆）
                "armor_proficiencies": ["轻甲"],
            }
        )
        assert sheet.skill_expertise == {"arcana", "perception"}
        assert sheet.feats == ["巨武器大师", "幸运"]
        assert sheet.tool_proficiencies == {"盗贼工具"}
        assert sheet.weapon_proficiencies == set()
        assert sheet.armor_proficiencies == {"轻甲"}

    def test_expertise_filters_invalid_keys(self) -> None:
        sheet = CharacterSheet(skill_expertise={"perception", "junk", "STEALTH"})
        assert sheet.skill_expertise == {"perception", "stealth"}

    def test_roundtrip_new_fields(self) -> None:
        sheet = make_sheet(
            skill_expertise={"arcana"},
            feats=["幸运"],
            tool_proficiencies={"盗贼工具"},
            weapon_proficiencies={"简易武器", "长剑"},
            armor_proficiencies={"轻甲"},
        )
        restored = CharacterSheet.from_dict(sheet.to_dict())
        assert restored.to_dict() == sheet.to_dict()
        assert restored.feats == ["幸运"]
        assert restored.weapon_proficiencies == {"简易武器", "长剑"}

    def test_format_sheet_renders_new_sections(self) -> None:
        sheet = make_sheet(
            skill_proficiencies={"arcana", "stealth"},
            skill_expertise={"stealth"},
            feats=["幸运"],
            tool_proficiencies={"盗贼工具"},
            weapon_proficiencies={"简易武器"},
            armor_proficiencies={"轻甲"},
        )
        out = CharacterManager.format_sheet(sheet)
        assert "隐匿★" in out  # 专精技能带星号
        assert "专精：隐匿" in out
        assert "专长：幸运" in out
        assert "工具 盗贼工具" in out
        assert "武器 简易武器" in out
        assert "防具 轻甲" in out
        assert "被动察觉" in out  # 战斗段新增

    def test_format_sheet_omits_empty_new_sections(self) -> None:
        out = CharacterManager.format_sheet(make_sheet())
        assert "专精：" not in out
        assert "专长：" not in out
        assert "工具 " not in out and "武器 " not in out and "防具 " not in out


class TestLanguages:
    """v0.28.0：语言（多门，自由文本无词表校验）——序列化与兼容。"""

    def test_from_dict_missing_key_defaults_empty(self) -> None:
        # 旧卡无 languages key → 空集，零迁移
        sheet = CharacterSheet.from_dict({"name": "旧卡", "skill_proficiencies": ["arcana"]})
        assert sheet.languages == set()

    def test_roundtrip(self) -> None:
        sheet = make_sheet(languages={"通用语", "精灵语"})
        restored = CharacterSheet.from_dict(sheet.to_dict())
        assert restored.to_dict() == sheet.to_dict()
        assert restored.languages == {"通用语", "精灵语"}

    def test_dirty_cleaned(self) -> None:
        sheet = CharacterSheet.from_dict(
            {
                "languages": ["通用语", "通用语", "", 42, "精灵语"],
            }
        )
        assert sheet.languages == {"通用语", "精灵语"}

    def test_string_input_becomes_empty(self) -> None:
        # 字符串输入 → 空集（防逐字拆，与武器熟练先例一致）
        sheet = CharacterSheet.from_dict({"languages": "通用语"})
        assert sheet.languages == set()

    def test_update_fields_overrides(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet(languages={"通用语"})))
        card, applied = run(cm.update_fields(ev, None, {"languages": "通用语, 精灵语 龙语"}))
        assert applied == ["languages"]
        assert card.languages == {"通用语", "精灵语", "龙语"}
        # 再次整体覆盖（不残留旧语言）
        card, _ = run(cm.update_fields(ev, None, {"languages": "地底通用语"}))
        assert card.languages == {"地底通用语"}

    def test_format_sheet_renders(self) -> None:
        out = CharacterManager.format_sheet(make_sheet(languages={"通用语", "精灵语"}))
        assert "语言：精灵语、通用语" in out  # 排序后渲染

    def test_format_sheet_omits_empty(self) -> None:
        out = CharacterManager.format_sheet(make_sheet())
        assert "语言：" not in out


class _FeatureRow:
    def __init__(self, level: int, name: str) -> None:
        self.level = level
        self.name = name


class _FakeClassFeatures:
    def __init__(self, names: list[tuple[int, str]]) -> None:
        self.base_rows = [_FeatureRow(lv, n) for lv, n in names]
        self.subclass_rows: list[_FeatureRow] = []


class _FakeKb:
    """class_features/race_features 替身：返回固定特性列表。"""

    def __init__(self, names: list[tuple[int, str]] | None = None) -> None:
        self._names = names or []

    def class_features(self, class_name: str, subclass: str | None = None):
        return _FakeClassFeatures(self._names)

    def race_features(self, name: str, edition: str = "") -> list[str]:
        return {"矮人": ["矮人体魄", "矮人战斗训练", "石中精妙"]}.get(name, [])


class TestFormatSheetCollapse:
    """v0.22.2：format_sheet 长字段折叠，防整卡超长被平台截断。"""

    def test_collapses_class_features(self) -> None:
        names = [(1, f"特性{i}") for i in range(1, 10)]
        sheet = make_sheet()
        out = CharacterManager.format_sheet(sheet, _FakeKb(names))
        assert "职业特性：" in out
        assert "特性1" in out and "特性8" in out
        assert "特性9" not in out
        assert "…等 9 项" in out

    def test_collapses_feats(self) -> None:
        sheet = make_sheet(feats=[f"专长{i}" for i in range(1, 8)])
        out = CharacterManager.format_sheet(sheet)
        assert "专长：专长1、专长2、专长3、专长4、专长5、专长6" in out
        assert "…等 7 项" in out

    def test_collapses_attacks(self) -> None:
        atk = {f"武器{i}": LayeredStat(bonus=i) for i in range(1, 10)}
        sheet = make_sheet(attack_bonuses=atk)
        out = CharacterManager.format_sheet(sheet)
        assert "武器1:1" in out and "武器8:8" in out
        assert "武器9:9" not in out
        assert "…等 9 项" in out

    def test_under_limit_unchanged(self) -> None:
        sheet = make_sheet(feats=["幸运", "巨武器大师"])
        out = CharacterManager.format_sheet(sheet)
        assert "专长：幸运、巨武器大师" in out
        assert "…等" not in out


class TestSpeedAndRaceLayout:
    """v0.23.0：速度字段与卡面布局（战斗核心前置 + 种族特性行）。"""

    def test_speed_serialization_roundtrip(self) -> None:
        sheet = make_sheet(speed=LayeredStat(base=30, bonus=5))
        restored = CharacterSheet.from_dict(sheet.to_dict())
        assert restored.speed.base == 30
        assert restored.speed.bonus == 5
        assert restored.speed.total == 35

    def test_speed_missing_defaults_zero(self) -> None:
        sheet = CharacterSheet.from_dict({"name": "旧卡"})
        assert sheet.speed.base == 0
        assert sheet.speed.bonus == 0

    def test_speed_line_and_layout_order(self) -> None:
        sheet = make_sheet(speed=LayeredStat(base=30, bonus=5))
        out = CharacterManager.format_sheet(sheet)
        lines = out.split("\n")
        hp_idx = next(i for i, l in enumerate(lines) if l.startswith("HP "))
        ab_idx = next(i for i, l in enumerate(lines) if l.startswith("属性值："))
        assert hp_idx < ab_idx  # 战斗核心在属性之前
        assert "HP " in lines[1] and "AC " in lines[1] and "速度 30+5尺" in lines[1]

    def test_race_features_line(self) -> None:
        sheet = make_sheet(race="矮人")
        out = CharacterManager.format_sheet(sheet, _FakeKb())
        assert "种族特性：矮人体魄、矮人战斗训练、石中精妙" in out

    def test_race_features_omitted_without_kb_or_race(self) -> None:
        out = CharacterManager.format_sheet(make_sheet(race="矮人"))  # 无 kb
        assert "种族特性：" not in out
        out = CharacterManager.format_sheet(make_sheet(race=""))  # 无种族
        assert "种族特性：" not in out


class TestExpertiseAndJoAT:
    """v0.21：技能专精（双倍熟练）与吟游诗人万事通（半熟练）。"""

    def _bard_sheet(self, level: int = 2) -> CharacterSheet:
        return CharacterSheet(
            classes=[ClassLevel(class_name="吟游诗人", level=level)],
            ability_scores=AbilityScores(strength=14, dexterity=14),  # 两处修正 +2
            skill_proficiencies={"stealth", "perception"},
        )

    def test_expertise_doubles_proficiency(self) -> None:
        sheet = self._bard_sheet(level=3)  # 熟练 +2
        sheet.skill_expertise = {"stealth"}
        mod, tags = sheet.skill_check("stealth")
        assert mod == 2 + 4  # 敏捷+2，专精 2×2=4
        assert tags == ["专精+4"]

    def test_proficiency_single(self) -> None:
        sheet = self._bard_sheet(level=3)
        mod, tags = sheet.skill_check("stealth")  # 隐匿→敏捷 +2，熟练 +2
        assert mod == 4
        assert tags == ["熟练+2"]

    def test_joat_half_proficiency_unskilled_skill(self) -> None:
        sheet = self._bard_sheet(level=2)  # 熟练 +2 → 万事通 +1
        mod, tags = sheet.skill_check("athletics")  # 力量+2，不熟练
        assert mod == 2 + 1
        assert tags == ["万事通+1"]

    def test_joat_excluded_when_proficient_or_expert(self) -> None:
        sheet = self._bard_sheet(level=9)  # 熟练 +4 → 万事通 +2
        mod, _ = sheet.skill_check("stealth")  # 熟练
        assert mod == 2 + 4
        sheet.skill_expertise = {"stealth"}
        mod, tags = sheet.skill_check("stealth")  # 专精 > 万事通
        assert mod == 2 + 8
        assert tags == ["专精+8"]

    def test_joat_bonus_by_bard_level(self) -> None:
        assert self._bard_sheet(level=1).jack_of_all_trades_bonus() == 0
        assert self._bard_sheet(level=2).jack_of_all_trades_bonus() == 1
        assert self._bard_sheet(level=9).jack_of_all_trades_bonus() == 2
        assert self._bard_sheet(level=17).jack_of_all_trades_bonus() == 3

    def test_joat_english_class_name(self) -> None:
        sheet = CharacterSheet(classes=[ClassLevel(class_name="Bard", level=3)])
        assert sheet.jack_of_all_trades_bonus() == 1

    def test_joat_non_bard_zero(self) -> None:
        sheet = CharacterSheet(classes=[ClassLevel(class_name="战士", level=10)])
        assert sheet.jack_of_all_trades_bonus() == 0

    def test_joat_multiclass_hit(self) -> None:
        sheet = CharacterSheet(
            classes=[
                ClassLevel(class_name="战士", level=5),
                ClassLevel(class_name="吟游诗人", level=2),
            ]
        )  # 总等级 7 → 熟练 +3 → 万事通 +1
        assert sheet.jack_of_all_trades_bonus() == 1

    def test_ability_check_joat(self) -> None:
        sheet = self._bard_sheet(level=2)
        mod, tags = sheet.ability_check("str")
        assert mod == 2 + 1
        assert tags == ["万事通+1"]

    def test_save_check_no_joat(self) -> None:
        # 豁免不走 ability_check，天然不吃万事通
        sheet = self._bard_sheet(level=9)
        mod = sheet.get_save_modifier("str")
        assert mod == 2  # 力量+2，无豁免熟练，无万事通

    def test_passive_perception_tracks_expertise_and_joat(self) -> None:
        # 察觉→感知 10（+0）；吟游诗人 2 级万事通 +1
        sheet = self._bard_sheet(level=2)
        sheet.skill_proficiencies = {"perception"}
        assert sheet.passive_perception == 10 + 2  # 熟练
        sheet2 = self._bard_sheet(level=2)
        sheet2.skill_proficiencies = set()
        assert sheet2.passive_perception == 10 + 1  # 万事通
        sheet3 = self._bard_sheet(level=2)
        sheet3.skill_proficiencies = {"perception"}
        sheet3.skill_expertise = {"perception"}
        assert sheet3.passive_perception == 10 + 4  # 专精

    def test_get_skill_modifier_delegates(self) -> None:
        sheet = self._bard_sheet(level=3)
        sheet.skill_expertise = {"stealth"}
        assert sheet.get_skill_modifier("stealth") == 6  # 2 + 4

    def test_update_fields_expertise_and_feats(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        card, applied = run(
            cm.update_fields(ev, None, {"expertise": "察觉,隐匿", "feats": "幸运,巨武器大师"})
        )
        assert card is not None
        assert "expertise" in applied and "feats" in applied
        assert card.skill_expertise == {"perception", "stealth"}
        assert card.feats == ["幸运", "巨武器大师"]

    def test_update_fields_tool_weapon_armor(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        card, applied = run(
            cm.update_fields(
                ev, None, {"tools": "盗贼工具", "weapons": "简易武器,长剑", "armors": "轻甲"}
            )
        )
        assert card is not None
        assert card.tool_proficiencies == {"盗贼工具"}
        assert card.weapon_proficiencies == {"简易武器", "长剑"}
        assert card.armor_proficiencies == {"轻甲"}


class TestV30Fields:
    """v0.30.0：人物基础信息 / 资源 / 先攻 / 已知法术字段。"""

    def test_roundtrip_with_new_fields(self) -> None:
        sheet = CharacterSheet(
            name="多萝西",
            classes=[ClassLevel(class_name="术士", subclass="狂野术法", level=4)],
            deity="无",
            age="14",
            gender="女",
            height="148cm",
            weight="40kg",
            hit_dice_used=1,
            inspiration=1,
            initiative=LayeredStat(base=3, bonus=1),
            spells={"戏法": ["火焰箭", "光亮术"], "1": ["护盾术", "法师护甲"], "2": ["迷踪步"]},
        )
        restored = CharacterSheet.from_dict(sheet.to_dict())
        assert restored.to_dict() == sheet.to_dict()
        assert restored.deity == "无"
        assert restored.age == "14"
        assert restored.gender == "女"
        assert restored.height == "148cm"
        assert restored.weight == "40kg"
        assert restored.hit_dice_used == 1
        assert restored.inspiration == 1
        assert restored.initiative.total == 4
        assert restored.spells["戏法"] == ["火焰箭", "光亮术"]
        assert restored.spells["1"] == ["护盾术", "法师护甲"]

    def test_dirty_new_fields_tolerated(self) -> None:
        sheet = CharacterSheet.from_dict(
            {
                "name": "脏卡",
                "hit_dice_used": 99,       # 越界 → clamp 20
                "inspiration": "x",        # 非数字 → 0
                "initiative": "bad",       # 非 dict → 默认 0
                "spells": {                # 脏 key 丢弃、脏子条目跳过
                    "戏法": "not_a_list",
                    "一环": ["护盾术", "护盾术", ""],
                    "10": ["九环外"],
                    "cantrip": ["火焰箭"],
                },
                "age": "  14\n",           # 控制字符清洗
                "weight": None,
            }
        )
        assert sheet.hit_dice_used == 20
        assert sheet.inspiration == 0
        assert sheet.initiative.total == 0
        assert sheet.spells == {"戏法": ["火焰箭"], "1": ["护盾术"]}
        assert sheet.age == "14"
        assert sheet.weight == ""

    def test_missing_new_fields_default_empty(self) -> None:
        """旧卡（无新字段）零迁移：缺失即空，不抛错。"""
        sheet = CharacterSheet.from_dict({"name": "旧卡", "classes": [{"class_name": "战士", "level": 1}]})
        assert sheet.deity == "" and sheet.age == "" and sheet.gender == ""
        assert sheet.height == "" and sheet.weight == ""
        assert sheet.hit_dice_used == 0 and sheet.inspiration == 0
        assert sheet.initiative.total == 0 and sheet.spells == {}

    def test_spell_ring_sorting(self) -> None:
        """环阶排序：戏法置顶，其余按环数升序。"""
        sheet = CharacterSheet(spells={"9": ["祈愿术"], "戏法": ["火焰箭"], "2": ["迷踪步"], "1": ["护盾术"]})
        assert list(sheet.spells.keys()) == ["戏法", "1", "2", "9"]

    def test_parse_spells_text_multiline(self) -> None:
        from astrbot_plugin_trpg_assistant.character import parse_spells_text

        text = "戏法：魔法技俩，法师之手，光亮术\n一环：法师护甲，护盾术，塔莎狂笑术（妖精触碰）\n二环：涡旋翘曲，迷踪步"
        spells = parse_spells_text(text)
        assert spells == {
            "戏法": ["魔法技俩", "法师之手", "光亮术"],
            "1": ["法师护甲", "护盾术", "塔莎狂笑术（妖精触碰）"],
            "2": ["涡旋翘曲", "迷踪步"],
        }

    def test_parse_spells_text_single_line(self) -> None:
        from astrbot_plugin_trpg_assistant.character import parse_spells_text

        spells = parse_spells_text("戏法:火焰箭,光亮术　1环:护盾术;cantrip:法师之手")
        assert spells == {
            "戏法": ["火焰箭", "光亮术", "法师之手"],
            "1": ["护盾术"],
        }

    def test_update_fields_v30(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        card, applied = run(
            cm.update_fields(
                ev,
                None,
                {
                    "deity": "无",
                    "age": "14",
                    "gender": "女",
                    "height": "148cm",
                    "weight": "40kg",
                    "hit_dice_used": "2",
                    "inspiration": "1",
                    "initiative": "1",
                    "spells": "戏法:火焰箭,光亮术　1环:护盾术",
                },
            )
        )
        assert card is not None
        assert set(applied) == {
            "deity", "age", "gender", "height", "weight",
            "hit_dice_used", "inspiration", "initiative", "spells",
        }
        assert card.deity == "无" and card.gender == "女"
        assert card.hit_dice_used == 2 and card.inspiration == 1
        assert card.initiative.bonus == 1
        assert card.spells == {"戏法": ["火焰箭", "光亮术"], "1": ["护盾术"]}


class TestV31EntryDelete:
    """v0.31.0：细项条目级删除（攻击 / 已知法术单条）。"""

    def test_update_fields_attack_delete(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        sheet = make_sheet(attack_bonuses={"长剑": LayeredStat(bonus=5), "长弓": LayeredStat(bonus=3)})
        run(cm.save_card(ev, sheet))
        card, applied = run(cm.update_fields(ev, None, {"attack": "长剑=-"}))
        assert card is not None
        assert "attack" in applied
        assert "长剑" not in card.attack_bonuses
        assert "长弓" in card.attack_bonuses

    def test_update_fields_attack_delete_missing(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))  # 无 attack_bonuses
        card, applied = run(cm.update_fields(ev, None, {"attack": "不存在的武器=-"}))
        assert card is not None
        assert "attack" not in applied  # 未删除任何条目

    def test_update_fields_attack_delete_fullwidth_dash(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet(attack_bonuses={"长剑": LayeredStat(bonus=5)})))
        card, applied = run(cm.update_fields(ev, None, {"attack": "长剑=－"}))
        assert card is not None
        assert "attack" in applied and "长剑" not in card.attack_bonuses

    def test_add_spell(self) -> None:
        sheet = CharacterSheet(spells={"戏法": ["火焰箭"]})
        assert sheet.add_spell("一环", "护盾术") is True
        assert sheet.add_spell("1", "法师护甲") is True
        assert sheet.add_spell("cantrip", "光亮术") is True
        assert sheet.spells["戏法"] == ["火焰箭", "光亮术"]
        assert sheet.spells["1"] == ["护盾术", "法师护甲"]
        # 环阶无法识别 / 空名 → False
        assert sheet.add_spell("十环", "祈愿术") is False
        assert sheet.add_spell("1环", "") is False
        # 去重
        assert sheet.add_spell("1环", "护盾术") is True
        assert sheet.spells["1"].count("护盾术") == 1

    def test_remove_spell(self) -> None:
        sheet = CharacterSheet(spells={"戏法": ["火焰箭", "光亮术"], "1": ["护盾术"]})
        assert sheet.remove_spell("戏法", "火焰箭") is True
        assert sheet.spells["戏法"] == ["光亮术"]
        # 环空自动移除
        assert sheet.remove_spell("1环", "护盾术") is True
        assert "1" not in sheet.spells
        # 不存在 → False
        assert sheet.remove_spell("1环", "护盾术") is False
        assert sheet.remove_spell("九环", "祈愿术") is False
        assert sheet.remove_spell("戏法", "") is False

    def test_update_fields_named_roll_delete(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet(named_rolls={"侦察": "1d20+2", "遁逃": "2d10"})))
        card, applied = run(cm.update_fields(ev, None, {"named_roll": "侦察=-"}))
        assert card is not None
        assert "named_roll" in applied
        assert "侦察" not in card.named_rolls
        assert card.named_rolls == {"遁逃": "2d10"}
        # 不存在 → applied 不含 named_roll
        card2, applied2 = run(cm.update_fields(ev, None, {"named_roll": "不存在的=-"}))
        assert card2 is not None
        assert "named_roll" not in applied2


class TestV41IndividualFields:
    """v0.41.0：update_fields 支持六维属性/种族/职业/版本单独设置。"""

    def test_parse_classes_text(self) -> None:
        from astrbot_plugin_trpg_assistant.character import parse_classes_text

        # 完整形态：职业（子职） 等级，+ 分隔兼职
        classes = parse_classes_text("战士 3 + 法师（塑能） 2")
        assert [(c.class_name, c.subclass, c.level) for c in classes] == [
            ("战士", "", 3),
            ("法师", "塑能", 2),
        ]
        # 等级可省略（默认 1）；全角加号
        classes = parse_classes_text("游荡者＋术士")
        assert [(c.class_name, c.level) for c in classes] == [
            ("游荡者", 1),
            ("术士", 1),
        ]
        # 空/纯分隔符 → 空列表（职业名是自由文本，「???」会被当作一个职业名）
        assert parse_classes_text("") == []
        assert parse_classes_text("+") == []
        assert [(c.class_name, c.level) for c in parse_classes_text("???")] == [("???", 1)]

    def test_normalize_edition(self) -> None:
        from astrbot_plugin_trpg_assistant.character import normalize_edition

        assert normalize_edition("2024") == "2024"
        assert normalize_edition("5.5e") == "2024"
        assert normalize_edition("5.5") == "2024"
        assert normalize_edition("5r") == "2024"
        assert normalize_edition("2014") == "2014"
        assert normalize_edition("5e") == "2014"
        assert normalize_edition("5.0") == "2014"
        assert normalize_edition("3.5") == ""
        assert normalize_edition("") == ""

    def test_update_ability_scores(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        # 单独设置力量
        card, applied = run(cm.update_fields(ev, None, {"str": "18"}))
        assert "str" in applied
        assert card.ability_scores.strength == 18
        # 中文键同样生效（update_fields 层不做中文映射，命令层映射；此处验证缩写直达）
        # 越界 clamp
        card, applied = run(cm.update_fields(ev, None, {"dex": "99"}))
        assert card.ability_scores.dexterity == 30
        # 非法值保持原值
        before = card.ability_scores.wisdom
        card, applied = run(cm.update_fields(ev, None, {"wis": "abc"}))
        assert card.ability_scores.wisdom == before
        # 其余属性不受影响
        assert card.ability_scores.intelligence == 16

    def test_update_race_classes_edition(self) -> None:
        cm = CharacterManager(star=_KVStar())
        ev = _Event()
        run(cm.save_card(ev, make_sheet()))
        # 种族
        card, applied = run(cm.update_fields(ev, None, {"race": "半精灵"}))
        assert "race" in applied
        assert card.race == "半精灵"
        # 职业整体替换
        card, applied = run(cm.update_fields(ev, None, {"classes": "战士 2"}))
        assert "classes" in applied
        assert [(c.class_name, c.level) for c in card.classes] == [("战士", 2)]
        # 「-」清空职业
        card, applied = run(cm.update_fields(ev, None, {"classes": "-"}))
        assert "classes" in applied
        assert card.classes == []
        # 纯分隔符（无可解析职业）→ 不应用
        before = list(card.classes)
        card, applied = run(cm.update_fields(ev, None, {"classes": "+"}))
        assert "classes" not in applied
        assert card.classes == before
        # 自由文本职业名直接采纳（如私设职业「???」）
        card, applied = run(cm.update_fields(ev, None, {"classes": "??? 1"}))
        assert "classes" in applied
        assert card.classes[0].class_name == "???"
        # 版本（5.5e → 2024）
        card, applied = run(cm.update_fields(ev, None, {"edition": "5.5e"}))
        assert "edition" in applied
        assert card.edition == "2024"
        # 非法版本不应用
        card, applied = run(cm.update_fields(ev, None, {"edition": "4e"}))
        assert "edition" not in applied
        assert card.edition == "2024"

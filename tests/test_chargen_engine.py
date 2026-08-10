"""chargen_engine（v0.18 规则引擎）单元测试。

用 _FakeKB 替身驱动 recalc_base，不断言真实 dnd_kb.db 内容（库更新会破坏断言）。
覆盖：HP（首职首级满骰/期望值/兼职/体修）、法术位（full/half/third/artificer/
pact 独立/兼职合并/非施法清槽）、AC（护甲类型/盾/2H 冲突/无甲防御取高/龙鳞）、
攻击（灵巧取高/远程敏/法术攻击/bonus 保留/手动条目不丢）。
"""

from __future__ import annotations

from astrbot_plugin_trpg_assistant.character import (
    AbilityScores,
    CharacterSheet,
    ClassLevel,
    EquipmentSlots,
    LayeredStat,
)
from astrbot_plugin_trpg_assistant.chargen_engine import FULL_SLOTS, PACT_SLOTS, recalc_base


class _Row:
    """kb 行替身：鸭子类型属性。"""

    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class _Item:
    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def is_shield(self) -> bool:
        return self.armor_type == "S"

    @property
    def is_ranged(self) -> bool:
        return self.armor_type == "R"

    @property
    def is_finesse(self) -> bool:
        return "F" in self.properties

    @property
    def is_two_handed(self) -> bool:
        return "2H" in self.properties

    @property
    def is_thrown(self) -> bool:
        return "T" in self.properties


class FakeKB:
    """规则引擎知识库替身：class_combat/subclass_caster/item_combat 迷你数据。"""

    CLASSES = {
        # name: (hd_faces, caster, spell_ability)
        "战士": (10, "", ""),
        "法师": (6, "full", "int"),
        "圣武士": (10, "1/2", "cha"),
        "游侠": (10, "1/2", "wis"),
        "魔契师": (8, "pact", "cha"),
        "野蛮人": (12, "", ""),
        "武僧": (8, "", ""),
        "术士": (8, "full", "cha"),
        "奇械师": (8, "artificer", "int"),
    }
    SUBCLASS_CASTER = {
        ("战士", "奥法骑士"): ("1/3", "int"),
    }
    ITEMS = {
        "皮甲": dict(ac=11, armor_type="LA", strength=None, stealth=False, dmg1="", properties=[]),
        "锁子甲": dict(ac=16, armor_type="HA", strength=13, stealth=True, dmg1="", properties=[]),
        "盾牌": dict(ac=2, armor_type="S", strength=None, stealth=False, dmg1="", properties=[]),
        "长剑": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="1d8", properties=["V"]),
        "匕首": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="1d4", properties=["F", "L", "T"]),
        "长弓": dict(ac=None, armor_type="R", strength=None, stealth=False, dmg1="1d8", properties=["A", "2H"]),
        "巨剑": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="2d6", properties=["2H", "H"]),
        "巨锤": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="2d6", properties=["2H"]),
        "雷神之锤": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="2d6", properties=["T"]),
        "+1 巨锤": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="2d6", properties=["2H"]),
    }
    BASE_ITEMS = {
        "雷神之锤": "巨锤",   # 魔法战锤 → 军用
    }
    RACE_SPEED = {
        "人类": 30,
        "矮人": 25,
    }

    def class_combat(self, name: str, edition: str = ""):
        row = self.CLASSES.get(name)
        if row is None:
            return None
        faces, caster, sa = row
        return _Row(hd_faces=faces, caster=caster, spell_ability=sa, saves=["str"])

    def subclass_caster(self, class_name: str, subclass: str, edition: str = ""):
        return self.SUBCLASS_CASTER.get((class_name, subclass))

    def item_combat(self, name: str):
        data = self.ITEMS.get(name)
        return _Item(**data) if data else None

    def item_base_item(self, name: str) -> str:
        # 魔法武器 → 基础武器名（v0.21.1 武器熟练判定用）
        return self.BASE_ITEMS.get(name, "")

    def race_speed(self, name: str, edition: str = "") -> int | None:
        # 种族步行速度（v0.23.0 速度重算用）
        return self.RACE_SPEED.get(name)


def _sheet(**kw) -> CharacterSheet:
    defaults = dict(
        name="测试",
        edition="2014",
        ability_scores=AbilityScores(
            strength=15, dexterity=14, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ),
    )
    defaults.update(kw)
    return CharacterSheet(**defaults)


class TestHp:
    def test_single_class_level1_full_die(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)])
        recalc_base(s, FakeKB())
        # 10 + 体修2 = 12
        assert s.hp_max.base == 12

    def test_single_class_average_after_first(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=3)])
        recalc_base(s, FakeKB())
        # 10 + (10//2+1)×2 + 体修2×3 = 10+12+6 = 28
        assert s.hp_max.base == 28

    def test_multiclass_only_first_class_first_level_full(self) -> None:
        s = _sheet(classes=[ClassLevel("法师", level=5), ClassLevel("圣武士", level=2)])
        recalc_base(s, FakeKB())
        # 法师 6 + 4×4 + 圣武士 (10//2+1)×2 + 体修2×7 = 6+16+12+14 = 48
        assert s.hp_max.base == 48

    def test_empty_classes_just_con_bonus(self) -> None:
        s = _sheet(classes=[])
        recalc_base(s, FakeKB())
        assert s.hp_max.base == 2  # 体修2 × 1 级

    def test_unknown_class_warns(self) -> None:
        s = _sheet(classes=[ClassLevel("不存在职业", level=1)])
        report = recalc_base(s, FakeKB())
        assert s.hp_max.base == 2  # 只有体修
        assert report.warnings


class TestSpeed:
    """v0.23.0：recalc_base 按种族重算步行速度 base（bonus 保留）。"""

    def test_speed_from_race(self) -> None:
        s = _sheet(race="人类")
        recalc_base(s, FakeKB())
        assert s.speed.base == 30

    def test_speed_unknown_race_zero(self) -> None:
        s = _sheet(race="不存在种族")
        recalc_base(s, FakeKB())
        assert s.speed.base == 0

    def test_speed_no_race_zero(self) -> None:
        s = _sheet(race="")
        recalc_base(s, FakeKB())
        assert s.speed.base == 0

    def test_speed_no_kb_keeps_manual_base(self) -> None:
        # kb 不可用时不重算 base（防误删手动值）
        s = _sheet(race="人类", speed=LayeredStat(base=35))
        recalc_base(s, None)
        assert s.speed.base == 35

    def test_speed_bonus_kept(self) -> None:
        s = _sheet(race="矮人", speed=LayeredStat(bonus=5))
        recalc_base(s, FakeKB())
        assert s.speed.base == 25
        assert s.speed.bonus == 5
        assert s.speed.total == 30


class TestSpellSlots:
    def test_full_caster_slots(self) -> None:
        s = _sheet(classes=[ClassLevel("法师", level=5)])
        recalc_base(s, FakeKB())
        assert {k: v.base for k, v in s.spell_slots.items()} == {"1": 4, "2": 3, "3": 2}

    def test_half_caster_level1_no_slots(self) -> None:
        s = _sheet(classes=[ClassLevel("圣武士", level=1)])
        recalc_base(s, FakeKB())
        assert s.spell_slots == {}

    def test_half_caster_level4(self) -> None:
        s = _sheet(classes=[ClassLevel("圣武士", level=4)])
        recalc_base(s, FakeKB())
        # 4//2 = 2 → FULL_SLOTS[1] = (3,0,...)
        assert {k: v.base for k, v in s.spell_slots.items()} == {"1": 3}

    def test_third_caster_subclass(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", subclass="奥法骑士", level=5)])
        recalc_base(s, FakeKB())
        # 5//3 = 1 → 1 环 2 个
        assert {k: v.base for k, v in s.spell_slots.items()} == {"1": 2}

    def test_artificer_rounds_up(self) -> None:
        s = _sheet(classes=[ClassLevel("奇械师", level=1)])
        recalc_base(s, FakeKB())
        # ⌈1/2⌉ = 1 → 1 环 2 个
        assert {k: v.base for k, v in s.spell_slots.items()} == {"1": 2}

    def test_pact_independent(self) -> None:
        s = _sheet(classes=[ClassLevel("魔契师", level=7), ClassLevel("战士", level=1)])
        recalc_base(s, FakeKB())
        slots = {k: v.base for k, v in s.spell_slots.items()}
        assert slots == {"pact": 2, "pact_level": 4}  # 短休 2×4 环，不并入
        assert "1" not in slots

    def test_multiclass_merge(self) -> None:
        s = _sheet(classes=[ClassLevel("法师", level=4), ClassLevel("圣武士", level=4)])
        recalc_base(s, FakeKB())
        # 4 + 4//2 = 6 → FULL_SLOTS[5] = (4,3,3)
        assert {k: v.base for k, v in s.spell_slots.items()} == {"1": 4, "2": 3, "3": 3}

    def test_non_caster_clears_old_slots(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=3)],
            spell_slots={"1": LayeredStat(base=4, bonus=0), "2": LayeredStat(base=3, bonus=1)},
        )
        recalc_base(s, FakeKB())
        # 非施法者：base 清空；带 bonus 的非生成键保留
        assert "1" not in s.spell_slots
        assert s.spell_slots.get("2", LayeredStat()).bonus == 1

    def test_hardcoded_tables_shape(self) -> None:
        assert len(FULL_SLOTS) == 20 and all(len(r) == 9 for r in FULL_SLOTS)
        assert len(PACT_SLOTS) == 20


class TestAc:
    def test_no_armor_10_plus_dex(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)])
        recalc_base(s, FakeKB())
        assert s.ac.base == 12  # 10 + 敏2

    def test_light_armor(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)], equipment=EquipmentSlots(armor="皮甲"))
        recalc_base(s, FakeKB())
        assert s.ac.base == 13  # 11 + 敏2

    def test_heavy_armor_ignores_dex(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)], equipment=EquipmentSlots(armor="锁子甲"))
        recalc_base(s, FakeKB())
        assert s.ac.base == 16

    def test_shield_bonus(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            equipment=EquipmentSlots(main_hand="长剑", off_hand="盾牌", armor="皮甲"),
        )
        recalc_base(s, FakeKB())
        assert s.ac.base == 15  # 11 + 敏2 + 盾2

    def test_two_handed_weapon_disables_shield(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            equipment=EquipmentSlots(main_hand="巨剑", off_hand="盾牌", armor="皮甲"),
        )
        report = recalc_base(s, FakeKB())
        assert s.ac.base == 13  # 盾被 2H 武器顶掉
        assert report.warnings

    def test_barbarian_unarmored(self) -> None:
        s = _sheet(
            classes=[ClassLevel("野蛮人", level=1)],
            ability_scores=AbilityScores(
                strength=16, dexterity=14, constitution=16,
                intelligence=10, wisdom=10, charisma=10,
            ),
        )
        recalc_base(s, FakeKB())
        assert s.ac.base == 15  # 10 + 敏2 + 体3

    def test_barbarian_unarmored_with_shield(self) -> None:
        s = _sheet(
            classes=[ClassLevel("野蛮人", level=1)],
            ability_scores=AbilityScores(
                strength=16, dexterity=14, constitution=16,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(off_hand="盾牌"),
        )
        recalc_base(s, FakeKB())
        assert s.ac.base == 17  # 15 + 盾2

    def test_monk_unarmored(self) -> None:
        s = _sheet(
            classes=[ClassLevel("武僧", level=1)],
            ability_scores=AbilityScores(
                strength=10, dexterity=16, constitution=12,
                intelligence=10, wisdom=16, charisma=10,
            ),
        )
        recalc_base(s, FakeKB())
        assert s.ac.base == 16  # 10 + 敏3 + 感3

    def test_draconic_sorcerer(self) -> None:
        s = _sheet(
            classes=[ClassLevel("术士", subclass="龙族血脉", level=1)],
            ability_scores=AbilityScores(
                strength=10, dexterity=16, constitution=12,
                intelligence=10, wisdom=10, charisma=16,
            ),
        )
        recalc_base(s, FakeKB())
        assert s.ac.base == 16  # 13 + 敏3

    def test_draconic_sorcerer_wearing_armor_uses_armor(self) -> None:
        s = _sheet(
            classes=[ClassLevel("术士", subclass="龙族血脉", level=1)],
            ability_scores=AbilityScores(
                strength=10, dexterity=16, constitution=12,
                intelligence=10, wisdom=10, charisma=16,
            ),
            equipment=EquipmentSlots(armor="皮甲"),
        )
        recalc_base(s, FakeKB())
        assert s.ac.base == 14  # 皮甲 11 + 敏3（龙鳞穿甲失效，取穿甲）

    def test_unknown_armor_warns(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)], equipment=EquipmentSlots(armor="神秘甲"))
        report = recalc_base(s, FakeKB())
        assert s.ac.base == 12  # 回退无甲
        assert report.warnings


class TestAttacks:
    def test_melee_strength(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)], equipment=EquipmentSlots(main_hand="长剑"))
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["长剑"].base == 4  # 力2 + 熟练2

    def test_finesse_takes_higher(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            ability_scores=AbilityScores(
                strength=8, dexterity=16, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(main_hand="匕首"),
        )
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["匕首"].base == 5  # max(力-1, 敏3) + 熟练2

    def test_ranged_uses_dex(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            ability_scores=AbilityScores(
                strength=15, dexterity=16, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(main_hand="长弓"),
        )
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["长弓"].base == 5  # 敏3 + 熟练2（不看力量）

    def test_spell_attack(self) -> None:
        s = _sheet(
            classes=[ClassLevel("法师", level=3)],
            ability_scores=AbilityScores(
                strength=10, dexterity=14, constitution=14,
                intelligence=16, wisdom=10, charisma=10,
            ),
        )
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["法师法术攻击"].base == 5  # 智3 + 熟练2

    def test_subclass_spell_attack(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", subclass="奥法骑士", level=5)],
            ability_scores=AbilityScores(
                strength=10, dexterity=14, constitution=14,
                intelligence=16, wisdom=10, charisma=10,
            ),
        )
        recalc_base(s, FakeKB())
        # 战士无施法属性，子职奥法骑士 int → 法术攻击条目（5 级熟练 +3）
        assert s.attack_bonuses["战士法术攻击"].base == 6  # 智3 + 熟练3

    def test_bonus_preserved_and_manual_entries_kept(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            equipment=EquipmentSlots(main_hand="长剑"),
            attack_bonuses={
                "长剑": LayeredStat(base=0, bonus=2),
                "自定义拳击": LayeredStat(base=7, bonus=1),
            },
        )
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["长剑"].base == 4 and s.attack_bonuses["长剑"].bonus == 2
        assert s.attack_bonuses["自定义拳击"].base == 7 and s.attack_bonuses["自定义拳击"].bonus == 1

    def test_named_magic_weapon_resolves_base(self) -> None:
        # 「警戒武器巨锤」库内无条目（5etools 只有「警戒武器」书条目，具名变体
        # 是搜索索引派生的）→ 后缀解析「巨锤」复用战斗属性，条目名保持原名
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            ability_scores=AbilityScores(
                strength=15, dexterity=14, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(main_hand="警戒武器巨锤"),
        )
        report = recalc_base(s, FakeKB())
        assert s.attack_bonuses["警戒武器巨锤"].base == 4  # 力2 + 熟练2（巨锤近战）
        assert any("按基础武器「巨锤」" in w for w in report.warnings)

    def test_named_magic_weapon_finesse_base(self) -> None:
        # 「警戒武器匕首」→ 基础「匕首」（灵巧）→ 取 max(力,敏)
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            ability_scores=AbilityScores(
                strength=8, dexterity=16, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(main_hand="警戒武器匕首"),
        )
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["警戒武器匕首"].base == 5  # 敏3 + 熟练2

    def test_unknown_weapon_fallback_melee(self) -> None:
        # 词表外且无后缀的原创武器 → 回退近战力量修正生成条目 + 警告（细则交 DM）
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            ability_scores=AbilityScores(
                strength=15, dexterity=14, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(main_hand="自制武器"),
        )
        report = recalc_base(s, FakeKB())
        assert s.attack_bonuses["自制武器"].base == 4  # 力2 + 熟练2
        assert any("未在知识库识别" in w for w in report.warnings)

    def test_non_weapon_still_skipped(self) -> None:
        # 有 item_combat 但 dmg1 为空（盾牌/护甲）→ 仍不生成攻击条目（回归）
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            equipment=EquipmentSlots(main_hand="盾牌"),
        )
        recalc_base(s, FakeKB())
        assert s.attack_bonuses == {}


class TestWriteBack:
    def test_bonus_never_touched(self) -> None:
        s = _sheet(
            classes=[ClassLevel("战士", level=3)],
            hp_max=LayeredStat(base=0, bonus=5),
            ac=LayeredStat(base=0, bonus=1),
        )
        recalc_base(s, FakeKB())
        assert s.hp_max.base == 28 and s.hp_max.bonus == 5
        assert s.ac.base == 12 and s.ac.bonus == 1

    def test_report_changes(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=3)])
        report = recalc_base(s, FakeKB())
        assert "HP" in report.text and "AC" in report.text

    def test_level_up_recalc_updates_hp(self) -> None:
        s = _sheet(classes=[ClassLevel("战士", level=1)])
        recalc_base(s, FakeKB())
        assert s.hp_max.base == 12
        s.classes[0].level = 2
        recalc_base(s, FakeKB())
        assert s.hp_max.base == 20  # 10 + 6 + 体修2×2


class TestWeaponProficiency:
    """v0.21：武器熟练判定影响攻击加值（_weapon_proficient + _calc_attacks）。"""

    def _sheet_with(self, weapon: str, **profs) -> CharacterSheet:
        s = _sheet(
            classes=[ClassLevel("战士", level=1)],
            ability_scores=AbilityScores(
                strength=15, dexterity=14, constitution=14,
                intelligence=10, wisdom=10, charisma=10,
            ),
            equipment=EquipmentSlots(main_hand=weapon),
        )
        s.weapon_proficiencies = set(profs)
        return s

    def test_empty_proficiencies_keeps_legacy_behavior(self) -> None:
        # 旧卡未维护武器熟练 → 攻击加值与现状一致（回归）
        s = self._sheet_with("长剑")
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["长剑"].base == 4  # 力2 + 熟练2

    def test_unproficient_martial_weapon_loses_prof(self) -> None:
        # 只熟简易武器，装备军用长剑 → 不加熟练，且有警告
        s = self._sheet_with("长剑", 简易武器="简易武器")
        report = recalc_base(s, FakeKB())
        assert s.attack_bonuses["长剑"].base == 2  # 力2
        assert any("未熟练" in w for w in report.warnings)

    def test_martial_category_covers_longsword(self) -> None:
        s = self._sheet_with("长剑", 军用武器="军用武器")
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["长剑"].base == 4

    def test_simple_category_covers_dagger(self) -> None:
        s = self._sheet_with("匕首", 简易武器="简易武器")
        recalc_base(s, FakeKB())
        # 灵巧取 max(力2, 敏2) + 熟练2
        assert s.attack_bonuses["匕首"].base == 4

    def test_exact_weapon_name_hit(self) -> None:
        s = self._sheet_with("长剑", 长剑="长剑")
        recalc_base(s, FakeKB())
        assert s.attack_bonuses["长剑"].base == 4

    def test_unlisted_weapon_falls_back_proficient(self) -> None:
        # 词表外武器（火器等）→ 回退熟练，不扣熟练
        kb = FakeKB()
        kb.ITEMS = {**FakeKB.ITEMS, "手炮": dict(ac=None, armor_type="M", strength=None, stealth=False, dmg1="1d12", properties=["2H"])}
        s = self._sheet_with("手炮", 简易武器="简易武器")
        report = recalc_base(s, kb)
        assert s.attack_bonuses["手炮"].base == 4  # 力2 + 熟练2
        assert not any("未熟练" in w for w in report.warnings)

    def test_weapon_proficient_helper_aliases(self) -> None:
        from astrbot_plugin_trpg_assistant.chargen_engine import _weapon_proficient

        def mk(*profs):
            s = _sheet()
            s.weapon_proficiencies = set(profs)
            return s

        assert _weapon_proficient(mk(), "长剑") is True
        assert _weapon_proficient(mk("simple"), "匕首") is True  # 英文类别别名
        assert _weapon_proficient(mk("简易武器"), "长剑") is False
        assert _weapon_proficient(mk("军用"), "长剑") is True
        assert _weapon_proficient(mk("简易武器"), "手枪") is True  # 词表外回退

    def test_magic_weapon_via_base_item(self) -> None:
        # 雷神之锤 base_item=巨锤（军用）：熟军用 → 熟练；熟简易 → 不熟练
        kb = FakeKB()
        s = self._sheet_with("雷神之锤", 军用武器="军用武器")
        recalc_base(s, kb)
        assert s.attack_bonuses["雷神之锤"].base == 4  # 力2 + 熟练2
        s2 = self._sheet_with("雷神之锤", 简易武器="简易武器")
        report = recalc_base(s2, kb)
        assert s2.attack_bonuses["雷神之锤"].base == 2  # 力2，不加熟练
        assert any("未熟练" in w for w in report.warnings)

    def test_magic_weapon_plus_n_via_suffix(self) -> None:
        # +1 巨锤 无 base_item，靠词表后缀「巨锤」解析（军用）
        kb = FakeKB()
        s = self._sheet_with("+1 巨锤", 军用武器="军用武器")
        recalc_base(s, kb)
        assert s.attack_bonuses["+1 巨锤"].base == 4
        s2 = self._sheet_with("+1 巨锤", 简易武器="简易武器")
        report = recalc_base(s2, kb)
        assert s2.attack_bonuses["+1 巨锤"].base == 2
        assert any("未熟练" in w for w in report.warnings)

    def test_magic_weapon_no_kb_falls_back(self) -> None:
        # kb 缺失时魔法武器无法解析类别 → 回退熟练（行为不更严格，细则交 DM）
        from astrbot_plugin_trpg_assistant.chargen_engine import _weapon_proficient

        s = _sheet()
        s.weapon_proficiencies = {"简易武器"}
        assert _weapon_proficient(s, "雷神之锤", None) is True

    def test_base_item_resolver(self) -> None:
        from astrbot_plugin_trpg_assistant.chargen_engine import _resolve_base_weapon

        kb = FakeKB()
        assert _resolve_base_weapon("长剑", kb) == "长剑"          # 本体在词表
        assert _resolve_base_weapon("雷神之锤", kb) == "巨锤"       # kb base_item
        assert _resolve_base_weapon("+1 巨锤", kb) == "巨锤"       # 词表后缀
        assert _resolve_base_weapon("手炮", kb) is None            # 查不到 → 回退
        assert _resolve_base_weapon("雷神之锤", None) is None      # 无 kb → 后缀也无 → 回退


class TestInitiative:
    """v0.30.0：先攻 base = 敏捷修正重算，bonus 房规保留。"""

    def test_initiative_equals_dex_mod(self) -> None:
        s = _sheet(ability_scores=AbilityScores(
            strength=10, dexterity=16, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ))
        recalc_base(s, FakeKB())
        assert s.initiative.base == 3  # 敏捷 16 → +3

    def test_initiative_bonus_preserved(self) -> None:
        from astrbot_plugin_trpg_assistant.character import LayeredStat

        s = _sheet(ability_scores=AbilityScores(
            strength=10, dexterity=16, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ))
        s.initiative.bonus = 5  # 房规（如警觉专长 +5）
        recalc_base(s, FakeKB())
        assert s.initiative.base == 3
        assert s.initiative.bonus == 5
        assert s.initiative.total == 8

    def test_initiative_low_dex_negative(self) -> None:
        s = _sheet(ability_scores=AbilityScores(
            strength=10, dexterity=8, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ))
        recalc_base(s, FakeKB())
        assert s.initiative.base == -1

    def test_initiative_unchanged_no_report_noise(self) -> None:
        from astrbot_plugin_trpg_assistant.character import LayeredStat

        s = _sheet(ability_scores=AbilityScores(
            strength=10, dexterity=16, constitution=14,
            intelligence=10, wisdom=10, charisma=10,
        ))
        s.initiative = LayeredStat(base=3, bonus=0)
        report = recalc_base(s, FakeKB())
        assert not any("先攻" in c for c in report.changes)

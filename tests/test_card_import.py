"""文本角色卡导入解析器单测（card_import.parse_card_text）。

覆盖点：
  - format_sheet 输出 round-trip（2014 多职业+装备+法术位 / 2024 无子职）。
  - LLM 宽松 key:value 格式。
  - 缺字段默认值（属性→10、职业→空、名字→未知冒险者）。
  - 属性越界 clamp 1-30 + note。
  - edition 识别（名字行括号 / 版本键 / 全文关键词 / 默认 2014）。
  - 未知行静默忽略。
  - 有效字段 < 2 或空文本抛 ValueError。
  - 战斗字段（HP/AC/法术位/攻击）一律忽略并记 note。
"""

from __future__ import annotations

import pytest

from astrbot_plugin_trpg_assistant.card_import import ImportResult, parse_card_text
from astrbot_plugin_trpg_assistant.character import (
    AbilityScores,
    CharacterManager,
    CharacterSheet,
    ClassLevel,
    EquipmentSlots,
    LayeredStat,
)


def _make_2014_sheet() -> CharacterSheet:
    return CharacterSheet(
        name="阿尔文",
        edition="2014",
        classes=[
            ClassLevel(class_name="战士", subclass="勇士", level=3),
            ClassLevel(class_name="法师", subclass="塑能", level=2),
        ],
        race="半精灵",
        background="士兵",
        alignment="守序善良",
        ability_scores=AbilityScores(15, 14, 13, 12, 10, 8),
        skill_proficiencies={"athletics", "acrobatics"},
        save_proficiencies={"str", "con"},
        hp_max=LayeredStat(base=25, bonus=3),
        ac=LayeredStat(base=15, bonus=1),
        spell_slots={"1": LayeredStat(base=4), "2": LayeredStat(base=3)},
        attack_bonuses={"长剑": LayeredStat(base=5, bonus=1)},
        equipment=EquipmentSlots(main_hand="长剑", off_hand="木盾", armor="皮甲"),
        backstory="出生在小村庄，父亲是铁匠，少时随商队习武。" * 4,
    )


class TestRoundTrip:
    def test_2014_multi_class_full_sheet(self) -> None:
        sheet = _make_2014_sheet()
        text = CharacterManager.format_sheet(sheet)
        res = parse_card_text(text)
        assert isinstance(res, ImportResult)
        s = res.sheet
        assert s.name == "阿尔文"
        assert s.edition == "2014"
        assert [(c.class_name, c.subclass, c.level) for c in s.classes] == [
            ("战士", "勇士", 3),
            ("法师", "塑能", 2),
        ]
        assert s.race == "半精灵" and s.background == "士兵" and s.alignment == "守序善良"
        assert s.ability_scores.to_dict() == {
            "strength": 15, "dexterity": 14, "constitution": 13,
            "intelligence": 12, "wisdom": 10, "charisma": 8,
        }
        assert s.skill_proficiencies == {"athletics", "acrobatics"}
        assert s.save_proficiencies == {"str", "con"}
        assert s.equipment.main_hand == "长剑"
        assert s.equipment.off_hand == "木盾"
        assert s.equipment.armor == "皮甲"
        # 战斗字段被忽略：base 恒 0，且 notes 有提示
        assert s.hp_max.base == 0 and s.ac.base == 0
        assert s.attack_bonuses == {}
        assert any("战斗字段" in n for n in res.notes)
        # 生平保留（format_sheet 截断 50 字前缀）
        assert "小村庄" in s.backstory

    def test_2024_sheet_round_trip(self) -> None:
        sheet = CharacterSheet(
            name="梅芙",
            edition="2024",
            classes=[ClassLevel(class_name="法师", level=1)],
            race="人类",
            background="贤者",
            ability_scores=AbilityScores(8, 14, 12, 17, 13, 10),
            skill_proficiencies={"arcana", "history"},
            save_proficiencies={"int", "wis"},
        )
        text = CharacterManager.format_sheet(sheet)
        s = parse_card_text(text).sheet
        assert s.name == "梅芙" and s.edition == "2024"
        assert [(c.class_name, c.subclass, c.level) for c in s.classes] == [
            ("法师", "", 1)
        ]
        assert s.ability_scores.to_dict() == {
            "strength": 8, "dexterity": 14, "constitution": 12,
            "intelligence": 17, "wisdom": 13, "charisma": 10,
        }
        assert s.skill_proficiencies == {"arcana", "history"}
        assert s.save_proficiencies == {"int", "wis"}

    def test_reformat_identity(self) -> None:
        """解析后再 format_sheet，关键信息应保持一致（round-trip 二次幂等）。"""
        sheet = _make_2014_sheet()
        s1 = parse_card_text(CharacterManager.format_sheet(sheet)).sheet
        s2 = parse_card_text(CharacterManager.format_sheet(s1)).sheet
        assert s2.name == s1.name and s2.edition == s1.edition
        assert s2.ability_scores.to_dict() == s1.ability_scores.to_dict()
        assert [(c.class_name, c.subclass, c.level) for c in s2.classes] == [
            (c.class_name, c.subclass, c.level) for c in s1.classes
        ]


class TestLooseFormat:
    LOOSE = """名字：阿尔文
职业：战士 3
种族：半精灵
背景：士兵
阵营：守序善良
力量 15
敏捷 14
体质 13
智力 12
感知 10
魅力 8
技能：运动、体操
豁免：体质
主手：长剑
生平：来自北境的流浪剑士
"""

    def test_loose_key_value(self) -> None:
        res = parse_card_text(self.LOOSE)
        s = res.sheet
        assert s.name == "阿尔文"
        assert [(c.class_name, c.subclass, c.level) for c in s.classes] == [
            ("战士", "", 3)
        ]
        assert s.ability_scores.to_dict() == {
            "strength": 15, "dexterity": 14, "constitution": 13,
            "intelligence": 12, "wisdom": 10, "charisma": 8,
        }
        assert s.skill_proficiencies == {"athletics", "acrobatics"}
        assert s.save_proficiencies == {"con"}
        assert s.equipment.main_hand == "长剑"
        assert s.backstory == "来自北境的流浪剑士"
        assert res.notes == []  # 无缺省、无越界、无战斗行

    def test_loose_compact_ability_forms(self) -> None:
        text = "名字：A\n力量15\n敏 14\n体质: 12\n感10\ncha 8\n智力 13"
        s = parse_card_text(text).sheet
        assert s.ability_scores.to_dict() == {
            "strength": 15, "dexterity": 14, "constitution": 12,
            "intelligence": 13, "wisdom": 10, "charisma": 8,
        }

    def test_duplicate_ability_last_wins(self) -> None:
        """同一属性多次出现时，后写的覆盖先写的（宽松策略）。"""
        s = parse_card_text("名字：A\n力量 15\nstr 13\n敏捷 14").sheet
        assert s.ability_scores.strength == 13
        assert s.ability_scores.dexterity == 14

    def test_loose_comma_separated_abilities(self) -> None:
        text = "名字：B\n职业：游荡者 2\n属性值：力量 10、敏捷 16、体质 12、智力 14、感知 10、魅力 8"
        s = parse_card_text(text).sheet
        assert s.ability_scores.dexterity == 16
        assert s.ability_scores.intelligence == 14

    def test_english_class_name_kept(self) -> None:
        """职业/种族/背景留原文（宽松导入不强制知识库存在）。"""
        text = "名字：C\nclass: fighter 1\nrace: elf\nbackground: soldier"
        s = parse_card_text(text).sheet
        assert s.classes[0].class_name == "fighter"
        assert s.race == "elf" and s.background == "soldier"


class TestDefaults:
    def test_missing_abilities_default_10(self) -> None:
        s = parse_card_text("名字：D\n职业：战士 1").sheet
        assert s.ability_scores.to_dict() == {
            "strength": 10, "dexterity": 10, "constitution": 10,
            "intelligence": 10, "wisdom": 10, "charisma": 10,
        }

    def test_missing_name_default_and_note(self) -> None:
        res = parse_card_text("职业：战士 1\n力量 15\n敏捷 14\n体质 13\n智力 12\n感知 10\n魅力 8")
        assert res.sheet.name == "未知冒险者"
        assert any("卡名" in n for n in res.notes)

    def test_missing_class_note(self) -> None:
        res = parse_card_text("名字：E\n力量 15\n敏捷 14\n体质 13\n智力 12\n感知 10\n魅力 8")
        assert res.sheet.classes == []
        assert any("职业" in n for n in res.notes)


class TestClamp:
    def test_ability_out_of_range_clamped(self) -> None:
        res = parse_card_text(
            "📜 **梅芙**（2024）\n职业：法师（塑能） 1\n属性值：力量 45　敏捷 14\n种族 人类"
        )
        s = res.sheet
        assert s.ability_scores.strength == 30
        assert s.ability_scores.dexterity == 14
        assert any("45→30" in n for n in res.notes)


class TestEdition:
    def test_header_2024(self) -> None:
        s = parse_card_text("📜 **A**（2024）\n职业：法师 1\n力量 15").sheet
        assert s.edition == "2024"

    def test_version_key(self) -> None:
        s = parse_card_text("名字：A\n版本：5.5e\n职业：法师 1\n力量 15").sheet
        assert s.edition == "2024"

    def test_fulltext_2014(self) -> None:
        s = parse_card_text("名字：A\n职业：法师 1\n力量 15\n（2014 规则）").sheet
        assert s.edition == "2014"

    def test_default_2014(self) -> None:
        s = parse_card_text("名字：A\n职业：法师 1\n力量 15").sheet
        assert s.edition == "2014"


class TestRobustness:
    def test_unknown_lines_ignored(self) -> None:
        s = parse_card_text(
            "这是一句闲聊\n名字：A\n职业：法师 1\n力量 15\n随便什么行\n敏捷 14"
        ).sheet
        assert s.name == "A"
        assert s.ability_scores.strength == 15
        assert s.ability_scores.dexterity == 14

    def test_combat_lines_ignored_with_note(self) -> None:
        res = parse_card_text(
            "名字：A\n职业：战士 1\n力量 15\n敏捷 14\nHP 25　AC 16\n法术位 1环:2\n攻击 长剑:5"
        )
        assert res.sheet.hp_max.base == 0
        assert res.sheet.spell_slots == {}
        assert res.sheet.attack_bonuses == {}
        assert any("战斗字段" in n for n in res.notes)

    def test_too_few_fields_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_card_text("这是一段无关的闲聊文字，没有任何角色卡内容。")

    def test_single_field_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_card_text("名字：只有名字")

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_card_text("")
        with pytest.raises(ValueError):
            parse_card_text("   \n  ")


class TestNewFields:
    """v0.21：专精 / 专长 / 工具·武器·防具熟练 解析与 round-trip；v0.28.0：语言。"""

    _TEXT = """📜 **安娜**（2024）
职业：吟游诗人（学院派） 3
种族 半精灵　背景 艺人　阵营 中立善良
属性值：力量 10　敏捷 14　体质 12　智力 8　感知 10　魅力 16
熟练：技能 察觉、隐匿★　武器 简易武器、长剑　工具 盗贼工具　防具 轻甲　万事通 +1
专精：隐匿（★技能双倍熟练）
专长：幸运、巨武器大师
语言：通用语、精灵语
"""

    def test_parse_new_sections(self) -> None:
        res = parse_card_text(self._TEXT)
        s = res.sheet
        assert s.skill_proficiencies == {"perception", "stealth"}
        assert s.skill_expertise == {"stealth"}
        assert s.weapon_proficiencies == {"简易武器", "长剑"}
        assert s.tool_proficiencies == {"盗贼工具"}
        assert s.armor_proficiencies == {"轻甲"}
        assert s.feats == ["幸运", "巨武器大师"]
        assert s.languages == {"通用语", "精灵语"}

    def test_format_sheet_round_trip_new_fields(self) -> None:
        s = CharacterSheet(
            name="安娜",
            edition="2024",
            classes=[ClassLevel(class_name="吟游诗人", level=3)],
            ability_scores=AbilityScores(strength=10, dexterity=14, constitution=12,
                                         intelligence=8, wisdom=10, charisma=16),
            skill_proficiencies={"perception", "stealth"},
            skill_expertise={"stealth"},
            tool_proficiencies={"盗贼工具"},
            weapon_proficiencies={"简易武器", "长剑"},
            armor_proficiencies={"轻甲"},
            feats=["幸运"],
            languages={"通用语", "精灵语", "地底通用语"},
        )
        text = CharacterManager.format_sheet(s)
        res = parse_card_text(text)
        s2 = res.sheet
        assert s2.skill_expertise == {"stealth"}
        assert s2.weapon_proficiencies == {"简易武器", "长剑"}
        assert s2.tool_proficiencies == {"盗贼工具"}
        assert s2.armor_proficiencies == {"轻甲"}
        assert s2.feats == ["幸运"]
        assert s2.languages == {"通用语", "精灵语", "地底通用语"}
        # 「万事通 +N」尾部信息不会被误收进熟练项
        assert "万事通" not in s2.weapon_proficiencies
        assert "万事通" not in s2.tool_proficiencies

    def test_old_card_without_new_lines(self) -> None:
        res = parse_card_text("名字：阿尔文\n职业：战士\n力量 15\n熟练：技能 察觉")
        s = res.sheet
        assert s.skill_expertise == set()
        assert s.feats == []
        assert s.tool_proficiencies == set()
        assert s.weapon_proficiencies == set()
        assert s.armor_proficiencies == set()
        assert s.languages == set()


class TestV30Parsing:
    """v0.30.0：玩家纯文本卡完整解析（子职/等级独立行、熟练项键、人物描述、
    基础信息/资源/先攻/已知法术行、占位词过滤）。"""

    PLAYER_CARD = """人物姓名：多萝西·多洛莉丝
人物描述：从马戏团逃出来的少女术士
职业：术士
子职：狂野术法
等级：4
背景：艺人
种族：人类
阵营：混乱善良
信仰：无
年龄：14
性别：女
身高：148cm
体重：40kg
力量：8
敏捷：16
体质：15
智力：8
感知：15
魅力：18
生命值：34/34
生命骰：D6
短休已用生命骰：0/4
激励：1/1
AC：13
先攻：+3
速度：30ft步行
被动感知（察觉）：14
语言： 通用语，精灵语，龙语
熟练加值：+2
熟练项：
武器：简易武器
护甲：无
工具：里拉琴
豁免：体质，魅力
技能：特技，洞悉，察觉，表演，游说
职业特性： 施法，先天术法，魔力泉涌，超魔法，狂野魔法浪涌，混乱之潮
专长：艺术家，健壮，妖精触碰
种族特性：适应力，多才多艺
已知法术：
戏法：魔法技俩，法师之手，光亮术，次级幻象，火焰箭
一环：法师护甲，护盾术，虚假生命，灵敏之赐，银光锐语，咒火闪焰，塔莎狂笑术（妖精触碰）
二环：涡旋翘曲，迷踪步（妖精触碰）
"""

    def test_full_player_card(self) -> None:
        res = parse_card_text(self.PLAYER_CARD)
        s = res.sheet
        assert s.name == "多萝西·多洛莉丝"
        assert s.edition == "2014"
        # 子职/等级独立行并入职业条目
        assert [(c.class_name, c.subclass, c.level) for c in s.classes] == [
            ("术士", "狂野术法", 4)
        ]
        assert s.race == "人类" and s.background == "艺人" and s.alignment == "混乱善良"
        assert s.ability_scores.to_dict() == {
            "strength": 8, "dexterity": 16, "constitution": 15,
            "intelligence": 8, "wisdom": 15, "charisma": 18,
        }
        # 熟练项键：五类熟练全部入库，护甲「无」不出现
        assert s.save_proficiencies == {"con", "cha"}
        assert s.skill_proficiencies == {
            "acrobatics", "insight", "perception", "performance", "persuasion",
        }
        assert s.tool_proficiencies == {"里拉琴"}
        assert s.weapon_proficiencies == {"简易武器"}
        assert s.armor_proficiencies == set()
        assert s.equipment.armor == ""  # 「护甲：无」不误入装备槽
        # 人物描述 → backstory
        assert "马戏团" in s.backstory
        # 人物基础信息
        assert s.deity == "无"
        assert s.age == "14" and s.gender == "女"
        assert s.height == "148cm" and s.weight == "40kg"
        # 资源：激励解析为 1；短休已用生命骰 0
        assert s.inspiration == 1
        assert s.hit_dice_used == 0
        # 先攻/生命骰行被忽略（规则引擎重算）
        assert s.initiative.total == 0
        # 专长 / 语言
        assert s.feats == ["艺术家", "健壮", "妖精触碰"]
        assert s.languages == {"通用语", "精灵语", "龙语"}
        # 已知法术（多行形态，环阶归一，保留括号标注）
        assert s.spells["戏法"] == ["魔法技俩", "法师之手", "光亮术", "次级幻象", "火焰箭"]
        assert s.spells["1"] == [
            "法师护甲", "护盾术", "虚假生命", "灵敏之赐", "银光锐语",
            "咒火闪焰", "塔莎狂笑术（妖精触碰）",
        ]
        assert s.spells["2"] == ["涡旋翘曲", "迷踪步（妖精触碰）"]
        # 战斗字段忽略 note
        assert any("战斗字段" in n for n in res.notes)

    def test_subclass_level_lines_apply_to_latest_class(self) -> None:
        res = parse_card_text(
            "名字：兼职者\n职业：战士\n等级：3\n职业：法师\n子职：塑能\n等级：2\n力量 15\n智力 14"
        )
        s = res.sheet
        assert [(c.class_name, c.subclass, c.level) for c in s.classes] == [
            ("战士", "", 3),
            ("法师", "塑能", 2),
        ]

    def test_level_line_before_class_ignored(self) -> None:
        res = parse_card_text("名字：某人\n等级：5\n职业：战士\n力量 15")
        s = res.sheet
        assert [(c.class_name, c.subclass, c.level) for c in s.classes] == [
            ("战士", "", 1)
        ]

    def test_placeholder_words_filtered(self) -> None:
        res = parse_card_text(
            "名字：甲\n职业：战士\n力量 15\n"
            "熟练：武器 简易武器、无　防具 无　工具 无　技能 察觉\n"
            "专长：无\n语言：无\n装备：护甲 无"
        )
        s = res.sheet
        assert s.weapon_proficiencies == {"简易武器"}
        assert s.armor_proficiencies == set()
        assert s.tool_proficiencies == set()
        assert s.feats == []
        assert s.languages == set()
        assert s.equipment.armor == ""

    def test_v30_round_trip(self) -> None:
        sheet = CharacterSheet(
            name="多萝西",
            edition="2014",
            classes=[ClassLevel(class_name="术士", subclass="狂野术法", level=4)],
            ability_scores=AbilityScores(strength=8, dexterity=16, constitution=15,
                                         intelligence=8, wisdom=15, charisma=18),
            deity="无",
            age="14",
            gender="女",
            height="148cm",
            weight="40kg",
            inspiration=1,
            spells={"戏法": ["火焰箭"], "1": ["护盾术", "塔莎狂笑术（妖精触碰）"]},
        )
        text = CharacterManager.format_sheet(sheet)
        res = parse_card_text(text)
        s2 = res.sheet
        # 人物信息自由文本可 round-trip（format_sheet「人物：」复合行）
        assert s2.deity == "无"
        assert s2.age == "14" and s2.gender == "女"
        assert s2.height == "148cm" and s2.weight == "40kg"
        # 已知法术卡面为统计折叠（全文走 /卡 详情），round-trip 不保法术名；
        # 激励为「激励 1/1」无冒号形态，同样不保（资源类以玩家卡「激励：1」写法解析）
        assert "已知法术" in text

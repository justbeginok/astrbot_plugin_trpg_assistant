"""文本角色卡导入命令级集成测试（/卡 导入、/车卡 导入）与防脱轨守则。

不依赖真实 AstrBot，完整驱动插件指令管线（与 test_character_commands 同体系）。

覆盖点：
  - /卡 导入 多行文本落库：get_card 可查、活跃指针指向、回复含 format_sheet。
  - /车卡 导入 路径；custom_prefix_route（.卡 导入）三入口复用。
  - 导入成功删除该玩家车卡草稿（防残留）。
  - 同名卡覆盖提示；无效文本/缺文本的用法提示。
  - guide_chargen start/answer/status 返回含防脱轨【守则】。
  - manage_character show 无卡文案含「导入」引导。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from astrbot_plugin_trpg_assistant.character import (
    AbilityScores,
    CharacterSheet,
    ClassLevel,
)
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin


class FakeEvent:
    def __init__(
        self,
        message_str: str,
        origin: str = "group:1",
        sender_id: str = "u1",
        sender_name: str = "Alice",
        private: bool = False,
        admin: bool = False,
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._private = private
        self._admin = admin
        self.stopped = False

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_private_chat(self) -> bool:
        return self._private

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self, config: dict | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def make_plugin(config: dict | None = None) -> _MemoryPlugin:
    return _MemoryPlugin(config=config)


def ev(
    message_str: str,
    origin: str = "group:1",
    sender_id: str = "u1",
    sender_name: str = "Alice",
    private: bool = False,
    admin: bool = False,
) -> FakeEvent:
    return FakeEvent(message_str, origin, sender_id, sender_name, private, admin)


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def card_cmd(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.char_cmd(event)))


def chargen_cmd(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.chargen_cmd(event)))


def seed_card(plugin: _MemoryPlugin, name: str = "阿尔文", sender_id: str = "u1") -> None:
    sheet = CharacterSheet(
        name=name,
        edition="2014",
        classes=[ClassLevel(class_name="法师", subclass="塑能", level=1)],
        race="人类",
        background="士兵",
        alignment="守序善良",
        ability_scores=AbilityScores(15, 14, 13, 12, 10, 8),
        skill_proficiencies={"arcana"},
        save_proficiencies={"int"},
    )
    run(plugin.character_manager.save_card(ev("", sender_id=sender_id), sheet))


MULTI_LINE = (
    "📜 **阿尔文**（2014）\n"
    "职业：战士（勇士） 3\n"
    "种族 半精灵　背景 士兵　阵营 守序善良\n"
    "属性值：力量 15　敏捷 14　体质 13　智力 12　感知 10　魅力 8\n"
    "熟练：豁免 力量、体质　技能 运动、体操\n"
    "装备：主手 长剑　副手 木盾"
)


class TestCardImportCommand:
    def test_import_creates_card(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev(f"/卡 导入\n{MULTI_LINE}"))
        joined = "\n".join(out)
        assert "已导入角色卡「阿尔文」" in joined
        assert "📜 **阿尔文**（2014）" in joined
        assert "力量 15（+2）" in joined
        card = run(p.character_manager.get_card(ev("")))
        assert card is not None and card.name == "阿尔文"
        assert card.ability_scores.strength == 15
        assert card.classes[0].class_name == "战士"
        active = run(p.character_manager.get_active_name(ev("")))
        assert active == "阿尔文"

    def test_import_via_chargen_cmd(self) -> None:
        p = make_plugin()
        out = chargen_cmd(p, ev(f"/车卡 导入\n{MULTI_LINE}"))
        assert "已导入角色卡「阿尔文」" in "\n".join(out)
        card = run(p.character_manager.get_card(ev("")))
        assert card is not None

    def test_import_via_custom_prefix(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(f".卡 导入\n{MULTI_LINE}")
        out = run(_collect(p.custom_prefix_route(event)))
        assert "已导入角色卡「阿尔文」" in "\n".join(out)
        assert event.stopped

    def test_import_discards_draft(self) -> None:
        p = make_plugin()
        run(p.chargen_manager.start(ev("")))
        assert run(p.chargen_manager.get_draft(ev(""))) is not None
        out = card_cmd(p, ev(f"/卡 导入\n{MULTI_LINE}"))
        assert "已丢弃未完成的引导草稿" in "\n".join(out)
        assert run(p.chargen_manager.get_draft(ev(""))) is None

    def test_import_overwrite_same_name(self) -> None:
        p = make_plugin()
        seed_card(p)  # 已有「阿尔文」（法师 1）
        out = card_cmd(p, ev(f"/卡 导入\n{MULTI_LINE}"))
        assert "覆盖了同名旧卡" in "\n".join(out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.classes[0].class_name == "战士"

    def test_import_invalid_text_hint(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev("/卡 导入 这是一段无关闲聊"))
        joined = "\n".join(out)
        assert "导入失败" in joined
        assert "key:value" in joined

    def test_import_missing_body_hint(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev("/卡 导入"))
        assert "用法" in "\n".join(out)
        out2 = chargen_cmd(p, ev("/车卡 导入"))
        assert "用法" in "\n".join(out2)

    def test_import_recalc_note_when_kb_missing(self) -> None:
        """无知识库环境（测试替身 kb 不可用）也不阻断落库。"""
        p = make_plugin()
        out = card_cmd(p, ev(f"/卡 导入\n{MULTI_LINE}"))
        assert "已导入角色卡「阿尔文」" in "\n".join(out)


class TestChargenGuardNote:
    def test_start_contains_guard(self) -> None:
        p = make_plugin()
        out = run(p.guide_chargen_tool(ev(""), action="start"))
        assert "禁止直接在对话中输出完整角色卡文本" in out

    def test_answer_non_done_contains_guard(self) -> None:
        p = make_plugin()
        run(p.guide_chargen_tool(ev(""), action="start"))
        out = run(p.guide_chargen_tool(ev(""), action="answer", answer="确认"))
        assert "【守则】" in out
        assert "禁止直接在对话中输出完整角色卡文本" in out

    def test_status_contains_guard(self) -> None:
        p = make_plugin()
        out = run(p.guide_chargen_tool(ev(""), action="status"))
        assert "【守则】" in out

    def test_manage_character_show_no_card_mentions_import(self) -> None:
        p = make_plugin()
        out = run(p.manage_character_tool(ev(""), action="show"))
        assert "导入" in out
        assert "/车卡 导入" in out

    def test_help_block_mentions_import(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev("/卡 未知子命令xyz"))
        assert "导入 <卡文本>" in "\n".join(out)


PLAYER_CARD = (
    "人物姓名：多萝西·多洛莉丝\n"
    "人物描述：从马戏团逃出来的少女术士\n"
    "职业：术士\n"
    "子职：狂野术法\n"
    "等级：4\n"
    "背景：艺人\n"
    "种族：人类\n"
    "阵营：混乱善良\n"
    "信仰：无\n"
    "年龄：14\n"
    "性别：女\n"
    "身高：148cm\n"
    "体重：40kg\n"
    "力量：8\n敏捷：16\n体质：15\n智力：8\n感知：15\n魅力：18\n"
    "生命值：34/34\n生命骰：D6\n短休已用生命骰：0/4\n激励：1/1\nAC：13\n先攻：+3\n"
    "速度：30ft步行\n被动感知（察觉）：14\n"
    "语言： 通用语，精灵语，龙语\n"
    "熟练加值：+2\n"
    "熟练项：\n武器：简易武器\n护甲：无\n工具：里拉琴\n豁免：体质，魅力\n"
    "技能：特技，洞悉，察觉，表演，游说\n"
    "职业特性： 施法，先天术法，魔力泉涌，超魔法，狂野魔法浪涌，混乱之潮\n"
    "专长：艺术家，健壮，妖精触碰\n"
    "种族特性：适应力，多才多艺\n"
    "已知法术：\n"
    "戏法：魔法技俩，法师之手，光亮术，次级幻象，火焰箭\n"
    "一环：法师护甲，护盾术，虚假生命，灵敏之赐，银光锐语，咒火闪焰，塔莎狂笑术（妖精触碰）\n"
    "二环：涡旋翘曲，迷踪步（妖精触碰）\n"
)


class TestV30ImportCommand:
    """v0.30.0：玩家纯文本卡命令级导入 + 卡面展示新字段。"""

    def test_import_player_card_full(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev(f"/卡 导入\n{PLAYER_CARD}"))
        joined = "\n".join(out)
        assert "已导入角色卡「多萝西·多洛莉丝」" in joined
        card = run(p.character_manager.get_card(ev("")))
        assert card is not None
        assert card.classes[0].subclass == "狂野术法"
        assert card.deity == "无" and card.gender == "女" and card.age == "14"
        assert card.spells["戏法"] == ["魔法技俩", "法师之手", "光亮术", "次级幻象", "火焰箭"]
        # 卡面展示：先攻重算（敏捷 16 → +3）、人物信息、激励、已知法术统计
        assert "先攻 +3" in joined
        assert "人物：性别 女　年龄 14　身高 148cm　体重 40kg　信仰 无" in joined
        assert "激励 1/1" in joined
        assert "已知法术：戏法 5 个　1环 7 个　2环 2 个" in joined
        # 防具熟练不含「无」
        assert "防具 无" not in joined

    def test_import_spells_visible_in_detail(self) -> None:
        p = make_plugin()
        card_cmd(p, ev(f"/卡 导入\n{PLAYER_CARD}"))
        out = card_cmd(p, ev("/卡 详情 法术"))
        joined = "\n".join(out)
        assert "已知法术" in joined
        assert "· 戏法（5 个）：" in joined and "魔法技俩" in joined
        assert "· 1环（7 个）：" in joined and "塔莎狂笑术（妖精触碰）" in joined
        assert "· 2环（2 个）：" in joined

    def test_import_person_info_detail(self) -> None:
        p = make_plugin()
        card_cmd(p, ev(f"/卡 导入\n{PLAYER_CARD}"))
        out = card_cmd(p, ev("/卡 详情 人物信息"))
        joined = "\n".join(out)
        assert "性别 女" in joined and "年龄 14" in joined
        assert "身高 148cm" in joined and "体重 40kg" in joined
        assert "信仰 无" in joined

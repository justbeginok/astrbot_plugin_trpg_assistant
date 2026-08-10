"""角色卡/车卡命令级集成测试：不依赖真实 AstrBot，完整驱动插件指令管线。

覆盖点：
  - /卡 全链路：空提示、列表、设字段、熟练增减、命名掷骰、切换/删除。
  - /车卡规则：查询公开、群聊非管理员拒绝、管理员放行、私聊放行、各设置项。
  - custom_prefix_route：.卡 / .车卡 / .车卡规则 路由与 token 优先级。
  - manage_character / guide_chargen LLM 工具各 action。
  - /r 联动：有活跃卡命中别名、无卡回退原报错、紧凑写法不回归。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from astrbot_plugin_trpg_assistant import dice_roller  # noqa: F401  （fixture 注入用）
from astrbot_plugin_trpg_assistant.character import (
    AbilityScores,
    CharacterSheet,
    ClassLevel,
    EquipmentSlots,
    LayeredStat,
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


def chargen_rule(plugin: _MemoryPlugin, event: FakeEvent) -> list[str]:
    return run(_collect(plugin.chargen_rule_cmd(event)))


def seed_card(plugin: _MemoryPlugin, name: str = "阿尔文", sender_id: str = "u1") -> None:
    sheet = CharacterSheet(
        name=name,
        edition="2014",
        classes=[ClassLevel(class_name="法师", subclass="塑能", level=1)],
        race="人类",
        background="士兵",
        alignment="守序善良",
        ability_scores=AbilityScores(strength=15, dexterity=14, constitution=13,
                                     intelligence=12, wisdom=10, charisma=8),
        skill_proficiencies={"arcana"},
        save_proficiencies={"int"},
    )
    run(plugin.character_manager.save_card(ev("", sender_id=sender_id), sheet))


def seed_attack_card(plugin: _MemoryPlugin, name: str = "阿尔文") -> None:
    """建一张带攻击条目与主手武器的卡（v0.22 攻击掷骰测试用）。

    长剑 total = base 7 + bonus 1 = 8。
    """
    sheet = CharacterSheet(
        name=name,
        edition="2014",
        classes=[ClassLevel(class_name="战士", subclass="战斗大师", level=5)],
        race="人类",
        background="士兵",
        alignment="守序善良",
        ability_scores=AbilityScores(strength=18, dexterity=14, constitution=14,
                                     intelligence=10, wisdom=10, charisma=10),
        attack_bonuses={
            "长剑": LayeredStat(base=7, bonus=1),
            "长弓": LayeredStat(base=5),
            "战士法术攻击": LayeredStat(base=6),
        },
        equipment=EquipmentSlots(main_hand="长剑"),
    )
    run(plugin.character_manager.save_card(ev(""), sheet))


# ---------------------------------------------------------------------------
# /卡 命令
# ---------------------------------------------------------------------------


class TestCharCommand:
    def test_empty_hint_without_card(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev("/卡"))
        assert "还没有角色卡" in out[0]
        assert "/车卡" in out[0]

    def test_view_active_card(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡"))
        assert "📜 **阿尔文**（2014）" in out[0]
        assert "法师" in out[0]
        assert "力量 15（+2）" in out[0]
        assert "HP 0" in out[0]

    def test_list_cards_with_active_marker(self) -> None:
        p = make_plugin()
        seed_card(p, "阿尔文")
        seed_card(p, "二号卡")
        out = card_cmd(p, ev("/卡 列表"))
        assert "⭐ 阿尔文" in out[0]
        assert "二号卡" in out[0]

    def test_set_fields(self) -> None:
        # 注入规则引擎替身 kb：装备槽 set 会触发 base 重算（v0.18）
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 hp 25"))
        assert "已更新 hp" in out[0]
        assert "HP 25" in out[0]
        out = card_cmd(p, ev("/卡 设 主手 长剑"))
        assert "主手 长剑" in out[0]
        assert "自动重算" in out[0]
        out = card_cmd(p, ev("/卡 设 攻击 长剑=6"))
        # v0.18：攻击显示 base+bonus（引擎算 base 4，set 写 bonus 6）
        assert "长剑:4+6" in out[0]

    def test_set_unknown_field(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 力量 99"))
        assert "未知字段" in out[0]

    def test_set_backstory_alias(self) -> None:
        # v0.22.2 曾把「背景/背景故事」都当作「生平」别名；
        # v0.27.1 修正：「背景」归位到 background 字段，「背景故事/生平」仍是 backstory。
        p = make_plugin()
        seed_card(p)
        story = "人人都说简确实是银龙生的，但她从未见过自己的亲生父母亲。"
        out = card_cmd(p, ev(f"/卡 设 背景 侍僧"))
        assert "已更新 background" in out[0]
        assert "背景 侍僧" in out[0]  # format_sheet 摘要含背景
        out = card_cmd(p, ev("/卡 详情 背景"))
        assert "侍僧" in out[0]
        out = card_cmd(p, ev(f"/卡 设 生平 {story}"))
        assert "已更新 backstory" in out[0]
        out = card_cmd(p, ev("/卡 详情 生平"))
        assert story in out[0]
        out = card_cmd(p, ev(f"/卡 设 背景故事 {story}"))
        assert "已更新 backstory" in out[0]

    def test_detail_command(self) -> None:
        # v0.22.2：/卡 详情 查看卡面上被折叠的完整字段
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 详情"))
        assert "可查看的完整字段" in out[0]
        assert "生平" in out[0] and "专长" in out[0]
        card_cmd(p, ev("/卡 设 专长 幸运,巨武器大师"))
        out = card_cmd(p, ev("/卡 详情 专长"))
        assert "幸运" in out[0] and "巨武器大师" in out[0]
        card_cmd(p, ev("/卡 设 攻击 长剑=6"))
        out = card_cmd(p, ev("/卡 详情 攻击"))
        assert "长剑" in out[0]
        out = card_cmd(p, ev("/卡 详情 装备"))
        assert "没有装备" in out[0]  # seed_card 无装备槽
        out = card_cmd(p, ev("/卡 详情 力量"))
        assert "未知字段" in out[0]

    def test_set_languages(self) -> None:
        # v0.28.0：/卡 设 语言 多门语言（逗号/空格分隔，整体覆盖）
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 语言 通用语,精灵语 龙语"))
        assert "已更新 languages" in out[0]
        assert "语言：精灵语、通用语、龙语" in out[0]  # format_sheet 排序渲染
        out = card_cmd(p, ev("/卡 详情 语言"))
        assert "· 龙语" in out[0] and "· 精灵语" in out[0]
        assert "（3 门）" in out[0]
        # 再次设置整体覆盖，不残留旧语言
        out = card_cmd(p, ev("/卡 设 语言 地底通用语"))
        assert "已更新 languages" in out[0]
        assert "语言：地底通用语" in out[0]
        assert "精灵语" not in out[0] and "龙语" not in out[0]
        # 中文别名「语言」与英文「languages」等价
        out = card_cmd(p, ev("/卡 设 languages 通用语"))
        assert "语言：通用语" in out[0]

    def test_set_speed(self) -> None:
        # v0.23.0：/卡 设 速度 写 bonus 层，卡面显示 total 尺
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 速度 40"))
        assert "已更新 speed" in out[0]
        assert "速度 40尺" in out[0]

    def test_detail_features_include_class(self) -> None:
        # v0.23.0：/卡 详情 特性 输出职业特性全文（真实库按等级过滤）
        p = make_plugin()
        seed_card(p)  # 人类/法师（塑能）1 级
        out = card_cmd(p, ev("/卡 详情 特性"))
        assert "· 法师（塑能） 1 级：" in out[0]

    def test_proficiency_add_remove(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 熟练 技能 +察觉 -奥秘"))
        assert "已更新技能熟练" in out[0]
        assert "技能 察觉" in out[0]
        assert "奥秘" not in out[0]  # 已被移除
        # 豁免
        out = card_cmd(p, ev("/卡 熟练 豁免 +力 -敏"))
        assert "已更新豁免熟练" in out[0]
        assert "豁免 智力、力量" in out[0]  # 原有智力保留，+力量 -敏捷

    def test_named_roll(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 骰 侦察 1d20+2"))
        assert "已记录命名掷骰" in out[0]

    def test_use_and_delete(self) -> None:
        p = make_plugin()
        seed_card(p, "阿尔文")
        seed_card(p, "二号卡")
        out = card_cmd(p, ev("/卡 用 二号卡"))
        assert "切换为「二号卡」" in out[0]
        out = card_cmd(p, ev("/卡 删 阿尔文"))
        assert "已删除" in out[0]
        out = card_cmd(p, ev("/卡 列表"))
        assert "阿尔文" not in out[0]

    def test_unknown_subcommand_shows_help(self) -> None:
        p = make_plugin()
        out = card_cmd(p, ev("/卡 xyz"))
        assert "角色卡用法" in out[0]


# ---------------------------------------------------------------------------
# /车卡规则 权限与设置
# ---------------------------------------------------------------------------


class TestChargenRuleCommand:
    def test_view_rule_anyone(self) -> None:
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则"))
        assert "开卡规则（群级）" in out[0]
        assert "27 点" in out[0]

    def test_group_non_admin_rejected(self) -> None:
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则 版本 2024", admin=False))
        assert "没有权限" in out[0]
        # 未被修改
        out = chargen_rule(p, ev("/车卡规则"))
        assert "2014" in out[0]

    def test_group_admin_allowed(self) -> None:
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则 版本 2024", admin=True))
        assert "已更新" in out[0]
        assert "2024" in out[0]

    def test_private_chat_allowed(self) -> None:
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则 属性 32buy", private=True))
        assert "已更新" in out[0]
        assert "32 点" in out[0]

    def test_set_alias_and_custom(self) -> None:
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则 属性 dnd5", admin=True))
        assert "掷骰法（4d6kh3×6" in out[0]
        out = chargen_rule(p, ev("/车卡规则 属性 购点 池=32 上限=17", admin=True))
        assert "32 点" in out[0] and "8-17" in out[0]
        out = chargen_rule(p, ev("/车卡规则 子职时机 开", admin=True))
        assert "开卡时确定" in out[0]
        out = chargen_rule(p, ev("/车卡规则 起始等级 3", admin=True))
        assert "起始等级 3" in out[0]

    def test_invalid_dice_expr_rejected(self) -> None:
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则 属性 掷骰 9d9x", admin=True))
        assert "无法解析" in out[0]

    def test_reset(self) -> None:
        p = make_plugin()
        chargen_rule(p, ev("/车卡规则 版本 2024", admin=True))
        out = chargen_rule(p, ev("/车卡规则 重置", admin=True))
        assert "已更新" in out[0]
        assert "2014" in out[0]

    def test_compact_syntax_accepted(self) -> None:
        """紧凑写法（设置项与值间无空格）自动拆分：版本2024 / 子职时机开 / 起始等级3。"""
        p = make_plugin()
        out = chargen_rule(p, ev("/车卡规则 版本2024", admin=True))
        assert "已更新" in out[0] and "2024" in out[0]
        out = chargen_rule(p, ev("/车卡规则 子职时机开", admin=True))
        assert "开卡时确定" in out[0]
        out = chargen_rule(p, ev("/车卡规则 起始等级3", admin=True))
        assert "起始等级 3" in out[0]


# ---------------------------------------------------------------------------
# custom_prefix_route
# ---------------------------------------------------------------------------


class TestCustomPrefixCharacter:
    def test_dot_card_list(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        seed_card(p)
        event = ev(".卡 列表")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "阿尔文" in outputs[0]
        assert event.stopped is True

    def test_dot_chargen_start(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".车卡")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "已开始车卡引导" in outputs[0]
        assert event.stopped is True

    def test_dot_chargen_rule_takes_precedence(self) -> None:
        """「.车卡规则 …」不能被「.车卡」块吞掉（长 token 优先）。"""
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".车卡规则 版本 2024", admin=True)
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert len(outputs) == 1
        assert "已更新" in outputs[0]
        assert "2024" in outputs[0]

    def test_dot_card_unrelated_not_routed(self) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        event = ev(".卡片 不是指令")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert outputs == []
        assert event.stopped is False


# ---------------------------------------------------------------------------
# manage_character / guide_chargen LLM 工具
# ---------------------------------------------------------------------------


class TestManageCharacterTool:
    def test_show_without_card(self) -> None:
        p = make_plugin()
        out = run(p.manage_character_tool(ev("")))
        assert "还没有角色卡" in out

    def test_show_active_card(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = run(p.manage_character_tool(ev("")))
        assert "阿尔文" in out and "力量 15" in out

    def test_list(self) -> None:
        p = make_plugin()
        seed_card(p, "阿尔文")
        seed_card(p, "二号卡")
        out = run(p.manage_character_tool(ev(""), action="list"))
        assert "阿尔文" in out and "二号卡" in out

    def test_set_field(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="set", field="hp", value="30"))
        assert "已更新 hp" in out
        assert "HP 30" in out

    def test_prof(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="prof", field="技能", value="+察觉 -奥秘"))
        assert "已更新技能熟练" in out
        assert "察觉" in out

    def test_use_delete_rename(self) -> None:
        p = make_plugin()
        seed_card(p, "阿尔文")
        seed_card(p, "二号卡")
        out = run(p.manage_character_tool(ev(""), action="use", name="二号卡"))
        assert "切换为「二号卡」" in out
        out = run(p.manage_character_tool(ev(""), action="rename", name="二号卡", new_name="新卡"))
        assert "改名为" in out
        out = run(p.manage_character_tool(ev(""), action="delete", name="阿尔文"))
        assert "已删除" in out

    def test_unknown_action(self) -> None:
        p = make_plugin()
        out = run(p.manage_character_tool(ev(""), action="xyz"))
        assert "未知的 action" in out


class TestGuideChargenTool:
    def test_start(self) -> None:
        p = make_plugin()
        out = run(p.guide_chargen_tool(ev(""), action="start"))
        assert "【进度】" in out and "已开始" in out and "【下一问】" in out

    def test_status_without_start(self) -> None:
        p = make_plugin()
        out = run(p.guide_chargen_tool(ev(""), action="status"))
        assert "未开始" in out

    def test_confirm_step(self) -> None:
        p = make_plugin()
        run(p.guide_chargen_tool(ev(""), action="start"))
        out = run(p.guide_chargen_tool(ev(""), action="answer", answer="确认"))
        assert "已确认开卡规则" in out
        assert "选择种族" in out  # 2014 默认 → 下一问是种族

    def test_cancel(self) -> None:
        p = make_plugin()
        run(p.guide_chargen_tool(ev(""), action="start"))
        out = run(p.guide_chargen_tool(ev(""), action="cancel"))
        assert "已取消" in out

    def test_reject_unknown_race(self) -> None:
        p = make_plugin()
        run(p.guide_chargen_tool(ev(""), action="start"))
        run(p.guide_chargen_tool(ev(""), action="answer", answer="确认"))
        out = run(p.guide_chargen_tool(ev(""), action="answer", answer="不存在的种族"))
        assert "未接受" in out


# ---------------------------------------------------------------------------
# /r 联动
# ---------------------------------------------------------------------------


class TestCharacterRollLink:
    def test_ability_roll_uses_card(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)
        make_rng([10])  # d20 掷出 10 → 力量 +2 → 12
        out = run(_collect(p.roll_cmd(ev("/r 力量"))))
        assert "力量检定" in out[0]
        assert "阿尔文" in out[0]

    def test_skill_roll_uses_proficiency(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)  # 智力12(+1) + 奥秘熟练(1级+2) = +3
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 奥秘"))))
        assert "奥秘" in out[0]
        assert "熟练" in out[0]

    def test_save_roll(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)  # 智力豁免熟练
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 智力豁免"))))
        assert "智力豁免" in out[0]

    def test_roll_with_dc(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 力量 12"))))
        assert "力量检定" in out[0]

    def test_no_card_falls_back_to_parse_error(self, make_rng) -> None:
        p = make_plugin()
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 力量"))))
        assert "解析错误" in out[0]  # 原行为：无卡时报错而不是查卡

    def test_compact_syntax_not_hijacked(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r d20感知15"))))
        # 紧凑写法不触发卡片联动，正常掷骰（含成功判定）
        assert "解析错误" not in out[0]

    def test_custom_prefix_route_character_roll(self, make_rng) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        seed_card(p)
        make_rng([10])
        event = ev(".r 力量")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert "力量检定" in outputs[0]
        assert event.stopped is True

    def test_roll_dice_tool_character_roll(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)
        make_rng([10])
        out = run(p.roll_dice_tool(ev(""), expression="力量"))
        assert "力量检定" in out

    # ------------------------------------------------------------------
    # v0.22：/r 攻击（角色卡攻击检定联动）
    # ------------------------------------------------------------------

    def test_attack_roll_uses_main_hand(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)  # 主手长剑，total +8
        make_rng([10])  # d20=10 → 10+8=18
        out = run(_collect(p.roll_cmd(ev("/r 攻击"))))
        assert "攻击" in out[0]
        assert "长剑" in out[0]
        assert "阿尔文" in out[0]

    def test_attack_roll_by_name(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击 长弓"))))
        assert "攻击" in out[0]
        assert "长弓" in out[0]
        assert "长剑" not in out[0]

    def test_attack_roll_prefix_match(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击 战士法术"))))
        assert "战士法术攻击" in out[0]

    def test_attack_list(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击 列表"))))
        assert "攻击选项" in out[0]
        assert "长剑(+8)" in out[0]
        assert "长弓(+5)" in out[0]

    def test_attack_list_written_to_history(self, make_rng) -> None:
        # 提示类输出（hist_expr=""）不写投掷历史
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        run(_collect(p.roll_cmd(ev("/r 攻击 列表"))))
        assert run(p._history.get_all(ev(""))) == []

    def test_attack_roll_written_to_history(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        run(_collect(p.roll_cmd(ev("/r 攻击"))))
        entries = run(p._history.get_all(ev("")))
        assert len(entries) == 1
        assert "1d20+8" in entries[0].expr

    def test_attack_no_main_hand_lists_options(self, make_rng) -> None:
        p = make_plugin()
        sheet = CharacterSheet(
            name="空手侠",
            classes=[ClassLevel(class_name="战士", level=1)],
            attack_bonuses={"匕首": LayeredStat(base=4)},
        )
        run(p.character_manager.save_card(ev(""), sheet))
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击"))))
        assert "未装备主手武器" in out[0]
        assert "攻击选项" in out[0]
        assert "匕首(+4)" in out[0]

    def test_attack_empty_bonuses_hint(self, make_rng) -> None:
        p = make_plugin()
        seed_card(p)  # 无攻击条目
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击"))))
        assert "无攻击条目" in out[0]

    def test_attack_unknown_name_hints(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击 权杖"))))
        assert "未找到攻击「权杖」" in out[0]
        assert "相近" not in out[0]  # 无相近候选

    def test_attack_roll_with_dc(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击 15"))))  # 主手 + DC15
        assert "攻击" in out[0]
        assert "长剑" in out[0]

    def test_attack_roll_by_name_with_dc(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击 长弓 15"))))
        assert "长弓" in out[0]
        assert "攻击" in out[0]

    def test_attack_no_card_falls_back_to_parse_error(self, make_rng) -> None:
        p = make_plugin()
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 攻击"))))
        assert "解析错误" in out[0]

    def test_attack_custom_prefix_route(self, make_rng) -> None:
        p = make_plugin(config={"default_cmd_prefix": "."})
        seed_attack_card(p)
        make_rng([10])
        event = ev(".r 攻击")
        outputs = run(_collect(p.custom_prefix_route(event)))
        assert "攻击" in outputs[0]
        assert "长剑" in outputs[0]
        assert event.stopped is True

    def test_attack_roll_dice_tool(self, make_rng) -> None:
        p = make_plugin()
        seed_attack_card(p)
        make_rng([10])
        out = run(p.roll_dice_tool(ev(""), expression="攻击"))
        assert "攻击" in out
        assert "长剑" in out


# ---------------------------------------------------------------------------
# v0.18：/卡 升级 + 装备槽重算（注入规则引擎替身 kb）
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class _ItemRow(_Row):
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


class _EngineKB:
    """注入 plugin._kb_manager 的规则引擎替身（避免依赖打包库的 schema 版本）。"""

    available = True

    def search(self, query, kind=None, limit=8):
        return []

    def class_combat(self, name, edition=""):
        if name == "法师":
            return _Row(hd_faces=6, caster="full", spell_ability="int", saves=["int", "wis"])
        if name == "战士":
            return _Row(hd_faces=10, caster="", spell_ability="", saves=["str", "con"])
        return None

    def subclass_caster(self, class_name, subclass, edition=""):
        return None

    def item_combat(self, name):
        if name == "长剑":
            return _ItemRow(ac=None, armor_type="M", strength=None, stealth=False,
                            dmg1="1d8", properties=["V"], range_note="")
        if name == "皮甲":
            return _ItemRow(ac=11, armor_type="LA", strength=None, stealth=False,
                            dmg1="", properties=[], range_note="")
        return None

    def item_base_item(self, name):
        return ""

    def race_ability(self, name, edition=""):
        return None

    def background_ability(self, name):
        return None


def _plugin_with_engine_kb() -> _MemoryPlugin:
    p = make_plugin()
    p._kb_manager = _EngineKB()
    return p


class TestLevelUpCommand:
    def test_level_up_default(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # 法师 1 级
        out = card_cmd(p, ev("/卡 升级"))
        assert any("已升级" in m for m in out)
        assert any("自动重算" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.classes[0].level == 2

    def test_level_up_specified_new_class(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 升级 战士"))
        assert any("已升级" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert [(c.class_name, c.level) for c in card.classes] == [("法师", 1), ("战士", 1)]

    def test_level_up_no_card(self) -> None:
        p = _plugin_with_engine_kb()
        out = card_cmd(p, ev("/卡 升级"))
        assert any("角色卡" in m for m in out)

    def test_level_up_cap_20(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        card = run(p.character_manager.get_card(ev("")))
        card.classes[0].level = 20
        run(p.character_manager.save_card(ev(""), card))
        out = card_cmd(p, ev("/卡 升级"))
        assert any("上限" in m for m in out)

    def test_level_up_tool_action(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="level_up"))
        assert "已升级" in out
        assert "自动重算" in out


class TestLevelDownCommand:
    """v0.24.0：/卡 降级 命令链路（等级 -1 + 重算）。"""

    def test_level_down_default(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # 法师 1 级 → 1 级下限，先升到 2 再降
        card = run(p.character_manager.get_card(ev("")))
        card.classes[0].level = 2
        run(p.character_manager.save_card(ev(""), card))
        out = card_cmd(p, ev("/卡 降级"))
        assert any("已降级" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.classes[0].level == 1

    def test_level_down_specified_class(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        card = run(p.character_manager.get_card(ev("")))
        card.classes = [ClassLevel(class_name="法师", level=1), ClassLevel(class_name="战士", level=3)]
        run(p.character_manager.save_card(ev(""), card))
        out = card_cmd(p, ev("/卡 降级 战士"))
        assert any("已降级" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert [(c.class_name, c.level) for c in card.classes] == [("法师", 1), ("战士", 2)]

    def test_level_down_min_1(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # 法师 1 级
        out = card_cmd(p, ev("/卡 降级"))
        assert any("1 级" in m for m in out)


class TestSetEquipmentRecalc:
    def test_set_weapon_triggers_recalc(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 主手 长剑"))
        assert any("已更新 main_hand" in m for m in out)
        assert any("自动重算" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.attack_bonuses["长剑"].base > 0

    def test_set_armor_triggers_recalc(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 护甲 皮甲"))
        assert any("自动重算" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.ac.base == 10 + AbilityScores.modifier(14) + 1  # 10+敏2+皮甲1

    def test_set_hp_no_recalc(self) -> None:
        """hp/ac 写的是 bonus 房规层，不触发 base 重算。"""
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 hp 5"))
        assert not any("自动重算" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.hp_max.bonus == 5


# ---------------------------------------------------------------------------
# v0.21：专精 / 专长 / 工具·武器·防具熟练 + /r 联动标签
# ---------------------------------------------------------------------------


def seed_bard(plugin: _MemoryPlugin, level: int = 2, sender_id: str = "u1") -> None:
    """建一张吟游诗人卡（用于万事通联动测试）。"""
    sheet = CharacterSheet(
        name="艾拉",
        edition="2014",
        classes=[ClassLevel(class_name="吟游诗人", level=level)],
        ability_scores=AbilityScores(strength=14, dexterity=14, constitution=13,
                                     intelligence=10, wisdom=10, charisma=15),
        skill_proficiencies={"perception"},
    )
    run(plugin.character_manager.save_card(ev("", sender_id=sender_id), sheet))


class TestNewCharSubcommands:
    def test_expertise_add_with_warning(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # 只有 arcana 熟练
        out = card_cmd(p, ev("/卡 专精 +察觉"))
        assert any("已更新技能专精" in m for m in out)
        assert any("尚未熟练" in m for m in out)  # 察觉未熟练提示
        card = run(p.character_manager.get_card(ev("")))
        assert card.skill_expertise == {"perception"}

    def test_expertise_add_remove(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        card_cmd(p, ev("/卡 专精 +奥秘"))
        out = card_cmd(p, ev("/卡 专精 -奥秘"))
        assert any("已更新技能专精" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.skill_expertise == set()

    def test_expertise_unknown_token(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 专精 +不存在的技能"))
        assert any("无法识别" in m for m in out)

    def test_feats_add_listed(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 专长 +巨武器大师"))
        assert any("已更新专长" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.feats == ["巨武器大师"]

    def test_feats_add_remove(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        card_cmd(p, ev("/卡 专长 +幸运 +巨武器大师"))
        out = card_cmd(p, ev("/卡 专长 -幸运"))
        card = run(p.character_manager.get_card(ev("")))
        assert card.feats == ["巨武器大师"]
        assert any("已更新专长" in m for m in out)

    def test_tool_weapon_armor_proficiency(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 熟练 工具 +盗贼工具"))
        assert any("已更新工具熟练" in m for m in out)
        out = card_cmd(p, ev("/卡 熟练 防具 +轻甲 +盾牌"))
        assert any("已更新防具熟练" in m for m in out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.tool_proficiencies == {"盗贼工具"}
        assert card.armor_proficiencies == {"轻甲", "盾牌"}

    def test_weapon_proficiency_triggers_recalc(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 熟练 武器 +简易武器"))
        assert any("已更新武器熟练" in m for m in out)
        assert any("自动重算" in m for m in out)  # 武器熟练变更触发重算
        card = run(p.character_manager.get_card(ev("")))
        assert card.weapon_proficiencies == {"简易武器"}

    def test_proficiency_help_lists_new_categories(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = card_cmd(p, ev("/卡 熟练"))
        assert any("工具|武器|防具" in m for m in out)


class TestCharacterRollTags:
    def test_expertise_tag_in_skill_roll(self, make_rng) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # arcana 熟练；智力12 → +1
        card_cmd(p, ev("/卡 专精 +奥秘"))
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 奥秘"))))
        # 智力+1 + 专精 2×2=4 → +5
        assert "d20+5" in out[0]
        assert "奥秘(阿尔文 专精+4)" in out[0]

    def test_joat_tag_in_ability_roll(self, make_rng) -> None:
        p = _plugin_with_engine_kb()
        seed_bard(p, level=2)  # 力量14 → +2，万事通 +1 → +3
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 力量"))))
        assert "d20+3" in out[0]
        assert "力量检定(艾拉·力量14 万事通+1)" in out[0]

    def test_save_roll_no_joat(self, make_rng) -> None:
        p = _plugin_with_engine_kb()
        seed_bard(p, level=9)  # 熟练+4 → 万事通+2，但豁免不吃
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 敏捷豁免"))))
        # 敏捷14 → +2，无豁免熟练，无万事通
        assert "d20+2" in out[0]
        assert "万事通" not in out[0]

    def test_proficient_skill_no_joat(self, make_rng) -> None:
        p = _plugin_with_engine_kb()
        seed_bard(p, level=2)  # perception 熟练（感知10 → +0）
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 察觉"))))
        assert "d20+2" in out[0]  # 熟练 +2，无万事通
        assert "察觉(艾拉 熟练+2)" in out[0]
        assert "万事通" not in out[0]

    def test_skill_roll_proficiency_tag(self, make_rng) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # arcana 熟练
        make_rng([10])
        out = run(_collect(p.roll_cmd(ev("/r 奥秘"))))
        assert "奥秘(阿尔文 熟练+2)" in out[0]


class TestManageCharacterToolNewActions:
    def test_expertise_action(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="expertise", value="+奥秘"))
        assert "已更新技能专精" in out
        card = run(p.character_manager.get_card(ev("")))
        assert card.skill_expertise == {"arcana"}

    def test_feat_action(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="feat", value="+幸运"))
        assert "已更新专长" in out
        card = run(p.character_manager.get_card(ev("")))
        assert card.feats == ["幸运"]

    def test_prof_weapon_action_triggers_recalc(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="prof", field="武器", value="+简易武器"))
        assert "已更新武器熟练" in out
        assert "自动重算" in out
        card = run(p.character_manager.get_card(ev("")))
        assert card.weapon_proficiencies == {"简易武器"}

    def test_prof_tool_action(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="prof", field="工具", value="+盗贼工具"))
        assert "已更新工具熟练" in out
        card = run(p.character_manager.get_card(ev("")))
        assert card.tool_proficiencies == {"盗贼工具"}

    def test_unknown_action_lists_new_actions(self) -> None:
        p = _plugin_with_engine_kb()
        out = run(p.manage_character_tool(ev(""), action="xxx"))
        assert "expertise" in out and "feat" in out


class TestV30CharCommand:
    """v0.30.0：/卡 设 新字段、/卡 详情 新分支、/ri 角色卡先攻联动。"""

    def test_set_person_info_fields(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 性别 女"))
        assert "已更新 gender" in out[0]
        assert "人物：性别 女" in out[0]
        card_cmd(p, ev("/卡 设 年龄 14"))
        card_cmd(p, ev("/卡 设 信仰 无"))
        out = card_cmd(p, ev("/卡 设 身高 148cm"))
        assert "已更新 height" in out[0]
        out = card_cmd(p, ev("/卡 设 体重 40kg"))
        assert "体重 40kg" in out[0]
        card = run(p.character_manager.get_card(ev("")))
        assert card.deity == "无" and card.age == "14" and card.gender == "女"
        assert card.height == "148cm" and card.weight == "40kg"

    def test_set_resource_and_initiative(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 激励 1"))
        assert "已更新 inspiration" in out[0]
        out = card_cmd(p, ev("/卡 设 生命骰已用 2"))
        assert "已更新 hit_dice_used" in out[0]
        out = card_cmd(p, ev("/卡 设 先攻 1"))
        assert "已更新 initiative" in out[0]
        card = run(p.character_manager.get_card(ev("")))
        assert card.inspiration == 1 and card.hit_dice_used == 2
        assert card.initiative.bonus == 1

    def test_set_spells_and_detail(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 设 法术 戏法:火焰箭,光亮术　1环:护盾术"))
        assert "已更新 spells" in out[0]
        assert "已知法术：戏法 2 个　1环 1 个" in out[0]
        out = card_cmd(p, ev("/卡 详情 法术"))
        assert "· 戏法（2 个）：火焰箭、光亮术" in "\n".join(out)
        out = card_cmd(p, ev("/卡 详情"))
        assert "法术" in out[0] and "人物信息" in out[0]

    def test_detail_person_info(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 设 性别 女"))
        card_cmd(p, ev("/卡 设 年龄 14"))
        out = card_cmd(p, ev("/卡 详情 人物信息"))
        joined = "\n".join(out)
        assert "性别 女" in joined and "年龄 14" in joined

    def test_ri_uses_card_initiative(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)  # 敏捷 14 → 先攻 +2（升级触发重算后）
        card_cmd(p, ev("/卡 升级"))
        p._roll_d20 = lambda: 10
        out = run(_collect(p.ri_cmd(ev("/ri"))))
        joined = "\n".join(out)
        assert "d20+2 → **12**" in joined
        assert "角色卡：阿尔文 先攻 +2" in joined

    def test_ri_explicit_modifier_wins_over_card(self) -> None:
        p = _plugin_with_engine_kb()
        seed_card(p)
        card_cmd(p, ev("/卡 升级"))
        p._roll_d20 = lambda: 10
        out = run(_collect(p.ri_cmd(ev("/ri +5"))))
        assert "d20+5 → **15**" in "\n".join(out)

    def test_ri_without_card_plain_d20(self) -> None:
        p = _plugin_with_engine_kb()
        p._roll_d20 = lambda: 10
        out = run(_collect(p.ri_cmd(ev("/ri"))))
        assert "d20 → **10**" in "\n".join(out)
        assert "角色卡" not in "\n".join(out)


class TestV31EntryCommands:
    """v0.31.0：细项条目级删除——攻击 / 已知法术单条（指令 + LLM 工具）。"""

    def test_set_attack_delete(self) -> None:
        p = _plugin_with_engine_kb()
        seed_attack_card(p)  # 攻击：长剑(7+1)、长弓(5)、战士法术攻击(6)
        out = card_cmd(p, ev("/卡 设 攻击 长弓=-"))
        joined = "\n".join(out)
        assert "已删除" in joined or "已更新" in joined
        card = run(p.character_manager.get_card(ev("")))
        assert "长弓" not in card.attack_bonuses
        assert "长剑" in card.attack_bonuses

    def test_set_attack_delete_missing_hint(self) -> None:
        p = _plugin_with_engine_kb()
        seed_attack_card(p)
        out = card_cmd(p, ev("/卡 设 攻击 不存在的武器=-"))
        assert "未找到该攻击条目" in "\n".join(out)

    def test_set_attack_delete_generated_hint(self) -> None:
        p = _plugin_with_engine_kb()
        seed_attack_card(p)
        out = card_cmd(p, ev("/卡 设 攻击 战士法术攻击=-"))
        joined = "\n".join(out)
        # 生成条目删除成功但提示重算会恢复
        assert "规则引擎按装备/职业自动生成" in joined
        card = run(p.character_manager.get_card(ev("")))
        assert "战士法术攻击" not in card.attack_bonuses

    def test_attack_delete_does_not_resurrect_on_recalc(self) -> None:
        """自建条目删除后重算不复活（生成集外条目整体保留逻辑）。"""
        p = _plugin_with_engine_kb()
        seed_attack_card(p)
        card_cmd(p, ev("/卡 设 攻击 自建招式=9"))
        card_cmd(p, ev("/卡 设 攻击 自建招式=-"))
        card_cmd(p, ev("/卡 升级"))  # 触发重算
        card = run(p.character_manager.get_card(ev("")))
        assert "自建招式" not in card.attack_bonuses

    def test_spell_add_and_remove(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 法术 加 一环 护盾术"))
        joined = "\n".join(out)
        assert "已加入法术「护盾术」（1环）" in joined
        assert "已知法术：1环 1 个" in joined
        card = run(p.character_manager.get_card(ev("")))
        assert card.spells == {"1": ["护盾术"]}
        out = card_cmd(p, ev("/卡 法术 删 1环 护盾术"))
        assert "已删除法术「护盾术」" in "\n".join(out)
        card = run(p.character_manager.get_card(ev("")))
        assert card.spells == {}

    def test_spell_remove_missing_hint(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 法术 加 一环 护盾术"))
        out = card_cmd(p, ev("/卡 法术 删 二环 护盾术"))
        assert "未找到" in "\n".join(out)
        out = card_cmd(p, ev("/卡 法术 删 十环 护盾术"))
        assert "未找到" in "\n".join(out)

    def test_spell_subcommand_usage(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 法术"))
        assert "用法" in "\n".join(out)
        assert "加 <环阶>" in "\n".join(out)

    def test_manage_character_del_attack(self) -> None:
        p = _plugin_with_engine_kb()
        seed_attack_card(p)
        out = run(p.manage_character_tool(ev(""), action="del_attack", field="长弓"))
        assert "已删除攻击条目「长弓」" in out
        card = run(p.character_manager.get_card(ev("")))
        assert "长弓" not in card.attack_bonuses
        # 未找到
        out2 = run(p.manage_character_tool(ev(""), action="del_attack", field="不存在的"))
        assert "没有名为" in out2

    def test_manage_character_spells(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="add_spell", field="一环", value="护盾术"))
        assert "已加入法术「护盾术」（1环）" in out
        out2 = run(p.manage_character_tool(ev(""), action="del_spell", field="1环", value="护盾术"))
        assert "已删除法术「护盾术」" in out2
        card = run(p.character_manager.get_card(ev("")))
        assert card.spells == {}
        # 环阶非法
        out3 = run(p.manage_character_tool(ev(""), action="add_spell", field="十环", value="祈愿术"))
        assert "环阶无法识别" in out3


class TestV32NamedRoll:
    """v0.32.0：命名掷骰全套 CRUD + /r 联动。"""

    def test_roll_named_via_r(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 骰 侦察 1d20+2"))
        out = run(_collect(p.roll_cmd(ev("/r 侦察"))))
        joined = "\n".join(out)
        assert "命名掷骰" in joined
        assert "d20+2" in joined  # formatter 显示省略数量 1
        assert "侦察" in joined

    def test_roll_named_with_dc_suffix(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 骰 侦察 1d20+2"))
        out = run(_collect(p.roll_cmd(ev("/r 侦察 15"))))
        assert "15" in "\n".join(out)

    def test_roll_named_overrides_builtin(self) -> None:
        """登记名与内建别名冲突时，命名掷骰优先（玩家显式意图）。"""
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 骰 察觉 1d20+8"))
        out = run(_collect(p.roll_cmd(ev("/r 察觉"))))
        joined = "\n".join(out)
        assert "d20+8" in joined  # 用登记表达式而非属性+熟练（默认 d20+2）
        assert "命名掷骰" in joined

    def test_roll_named_without_card_falls_back(self) -> None:
        p = make_plugin()
        # 无活跃卡：/r 侦察 走普通掷骰（解析失败报错，不崩）
        out = run(_collect(p.roll_cmd(ev("/r 侦察"))))
        assert "命名掷骰" not in "\n".join(out)

    def test_roll_named_via_llm_tool(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 骰 侦察 1d20+2"))
        out = run(p.roll_dice_tool(ev(""), expression="侦察"))
        assert "d20+2" in out

    def test_named_roll_delete_and_missing(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 骰 侦察 1d20+2"))
        out = card_cmd(p, ev("/卡 骰 侦察 -"))
        assert "已删除命名掷骰「侦察」" in "\n".join(out)
        card = run(p.character_manager.get_card(ev("")))
        assert "侦察" not in card.named_rolls
        out2 = card_cmd(p, ev("/卡 骰 侦察 -"))
        assert "没有名为" in "\n".join(out2)

    def test_named_roll_detail(self) -> None:
        p = make_plugin()
        seed_card(p)
        card_cmd(p, ev("/卡 骰 侦察 1d20+2"))
        out = card_cmd(p, ev("/卡 详情 掷骰"))
        joined = "\n".join(out)
        assert "命名掷骰（1 项）" in joined
        assert "· 侦察：1d20+2" in joined
        # 空卡提示
        p2 = make_plugin()
        seed_card(p2)
        out2 = card_cmd(p2, ev("/卡 详情 掷骰"))
        assert "没有登记命名掷骰" in "\n".join(out2)

    def test_named_roll_usage_hint(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = card_cmd(p, ev("/卡 骰"))
        assert "用法" in "\n".join(out)
        assert "/r <名称>" in "\n".join(out)

    def test_manage_character_named_roll_delete(self) -> None:
        p = make_plugin()
        seed_card(p)
        out = run(p.manage_character_tool(ev(""), action="named_roll", value="侦察=1d20+2"))
        assert "已记录命名掷骰" in out
        out2 = run(p.manage_character_tool(ev(""), action="named_roll", value="侦察=-"))
        assert "已删除命名掷骰" in out2
        card = run(p.character_manager.get_card(ev("")))
        assert card.named_rolls == {}
        out3 = run(p.manage_character_tool(ev(""), action="named_roll", value="侦察=-"))
        assert "没有名为" in out3

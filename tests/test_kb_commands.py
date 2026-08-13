"""知识库命令级集成测试：FakeEvent 驱动 /查X、/kb 与 query_dnd_knowledge 全链路。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

from astrbot_plugin_trpg_assistant.kb import (
    KnowledgeBaseManager,
)
from astrbot_plugin_trpg_assistant.main import (
    TrpgAssistantPlugin,
)
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
# 测试构建时隔离真实补丁目录（kb_patches/），避免真实补丁污染 fixture 计数。
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


class FakeEvent:
    """假消息事件（对齐 test_inventory_commands 的替身形态）。"""

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
    """子类化插件：真实 __init__ + 内存 KV + 注入 fixture 知识库。"""

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


def ev(
    message_str: str,
    origin: str = "group:1",
    private: bool = False,
    admin: bool = False,
) -> FakeEvent:
    return FakeEvent(message_str, origin, sender_id="u1", private=private, admin=admin)


def run(coro):
    return asyncio.run(coro)


async def _collect(gen: AsyncGenerator) -> list[str]:
    return [msg async for msg in gen]


def make_plugin(tmp_path: Path) -> _MemoryPlugin:
    db = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, db, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return _MemoryPlugin(db)


# v0.26.0 专长概要/关键字测试用的富化专长补丁（对 fixture 9 条专长打标）。
_FEAT_ENRICH_FIXTURE: list[dict] = [
    {"name": "箭术", "source": "XPHB", "edition": "2024",
     "summary": "远程武器攻击命中提升。", "keywords": ["远程", "命中"]},
    {"name": "坚韧", "source": "PHB", "edition": "2014",
     "summary": "提升生命值上限。", "keywords": ["生命", "防御"]},
    {"name": "中甲大师", "source": "PHB", "edition": "2014",
     "summary": "中甲穿戴时提升护甲等级。", "keywords": ["防御", "护甲"]},
    {"name": "仪式施法者", "source": "XPHB", "edition": "2024",
     "summary": "获得仪式法术能力。", "keywords": ["施法", "仪式"]},
    {"name": "元素掌控", "source": "PHB", "edition": "2014",
     "summary": "施法时忽略目标对伤害的抗性。", "keywords": ["施法", "伤害", "法术抗性"]},
    {"name": "幸运", "source": "XPHB", "edition": "2024",
     "summary": "获得重掷检定的幸运能力。", "keywords": ["幸运"]},
]

# v0.27.0 法术概要/关键字测试用的富化法术补丁（对 fixture 6 条法术打标）。
_SPELL_ENRICH_FIXTURE: list[dict] = [
    {"name": "火球术", "source": "XPHB", "edition": "2024",
     "summary": "爆裂火球灼烧大范围区域，造成高额火焰伤害。", "keywords": ["伤害"]},
    {"name": "火球术", "source": "PHB", "edition": "2014",
     "summary": "火球在指定点爆裂，灼烧范围内生物。", "keywords": ["伤害"]},
    {"name": "人类定身术", "source": "PHB", "edition": "2014",
     "summary": "定身一名类人生物，使其无法行动。", "keywords": ["控场", "定身"]},
    {"name": "冰霜射线", "source": "PHB", "edition": "2014",
     "summary": "射出一道冰冷射线造成寒冷伤害。", "keywords": ["伤害"]},
    {"name": "燃烧之手", "source": "PHB", "edition": "2014",
     "summary": "双手喷出锥形火焰灼烧近身敌人。", "keywords": ["伤害"]},
    {"name": "枯萎术", "source": "PHB", "edition": "2014",
     "summary": "吸取目标生命精华，造成大量暗蚀伤害。", "keywords": ["伤害", "减益"]},
]

# v0.33.0 职业/子职富化测试用的补丁（对 fixture 职业/子职打标）。
# fixture：战士 PHB（武者）+ 法师 PHB（奥法）+ 冠军武士/奥法骑士（子职）。
_CLASS_ENRICH_FIXTURE: list[dict] = [
    {"name": "战士", "source": "PHB", "edition": "2014", "role": "武者",
     "summary": "精通武器与护甲的战斗大师，爆发输出。", "keywords": ["近战", "重甲", "爆发", "力量"]},
    {"name": "法师", "source": "PHB", "edition": "2014", "role": "奥法",
     "summary": "以法术书研习奥术的博学施法者。", "keywords": ["奥术施法", "戏法", "法术位", "智力"]},
    {"name": "冠军武士", "source": "PHB", "edition": "2014",
     "summary": "专注肉体力量与重击的战士范型。", "keywords": ["近战", "战术", "武器", "爆发"]},
    {"name": "奥法骑士", "source": "PHB", "edition": "2014",
     "summary": "兼修防护法术的战士，武器与法术并用。", "keywords": ["施法", "防护", "武器"]},
]

# v0.34.0 种族/背景富化测试用的补丁（对 fixture 6 种族 + 2 背景打标）。
_RACE_ENRICH_FIXTURE: list[dict] = [
    {"name": "阿斯莫", "source": "DMG", "edition": "2014",
     "summary": "背负神圣血脉的半神后裔，自带圣光治愈之力。",
     "keywords": ["魅力", "神圣", "光耀", "治疗"]},
    {"name": "阿斯莫", "source": "MPMM", "edition": "2014",
     "summary": "神裔血脉的凡人，可选择光耀打击或灵光形态。",
     "keywords": ["魅力", "神圣", "光耀", "天生施法"]},
    {"name": "骷髅", "source": "DMG", "edition": "2014",
     "summary": "被魔法唤醒的不死骸骨，免疫毒素，听命于施法者。",
     "keywords": ["亡灵", "免疫", "抗性", "坚韧"]},
    {"name": "流浆体", "source": "AAG", "edition": "2014",
     "summary": "软泥构成的异怪生命，免疫强酸并擅长潜行突袭。",
     "keywords": ["异怪", "免疫", "强酸", "隐匿", "粘液"]},
    {"name": "半精灵", "source": "PHB", "edition": "2014",
     "summary": "人类与精灵的混血后裔，兼得双亲的社交天赋与精灵传承。",
     "keywords": ["魅力", "多语言", "技能", "精类"]},
    {"name": "矮人", "source": "PHB", "edition": "2014",
     "summary": "坚韧的山地工匠，毒抗与战锤为伴，黑暗视觉穿行地底。",
     "keywords": ["体质", "坚韧", "黑暗视觉", "大地"]},
]
_BACKGROUND_ENRICH_FIXTURE: list[dict] = [
    {"name": "侍僧", "source": "PHB", "edition": "2014",
     "summary": "献身神祇的侍奉者，深谙宗教仪轨与洞悉人心的智慧。",
     "keywords": ["感知", "魅力", "洞悉", "宗教", "书法工具", "教士"]},
    {"name": "侍僧", "source": "XPHB", "edition": "2024",
     "summary": "蒙受神恩的年轻教士，以魔法学徒之姿通晓经文与人心。",
     "keywords": ["智力", "感知", "魅力", "洞悉", "宗教", "起始专长", "教士"]},
]


def make_enriched_plugin(tmp_path: Path) -> _MemoryPlugin:
    """带专长/法术/职业子职/种族/背景概要+关键字补丁的插件（标签反查与概要展示测试用）。"""
    patch_dir = tmp_path / "patch_root" / "kb_patches"
    patch_dir.mkdir(parents=True)
    (patch_dir / "feat_enrich.json").write_text(
        json.dumps(_FEAT_ENRICH_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    (patch_dir / "spell_enrich.json").write_text(
        json.dumps(_SPELL_ENRICH_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    (patch_dir / "class_enrich.json").write_text(
        json.dumps(_CLASS_ENRICH_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    (patch_dir / "race_enrich.json").write_text(
        json.dumps(_RACE_ENRICH_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    (patch_dir / "background_enrich.json").write_text(
        json.dumps(_BACKGROUND_ENRICH_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    db = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, db, commit="fixture-abc123",
          patch_root=tmp_path / "patch_root")
    return _MemoryPlugin(db)


def collect(gen: AsyncGenerator) -> list[str]:
    return run(_collect(gen))


# ---------------------------------------------------------------------------
# /查X 命令
# ---------------------------------------------------------------------------


def test_spell_command_dual_version(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_spell_cmd(ev("/查法术 火球术")))
    text = msgs[0]
    assert "找到 2 个版本" in text
    # v0.44.0：版本标注移到卡片底部
    assert "版本：PHB·2014" in text and "版本：XPHB·2024" in text
    assert "⚠️机翻" in text  # XPHB fixture 为机翻


def test_spell_command_no_arg(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_spell_cmd(ev("/查法术")))
    assert "用法" in msgs[0]


def test_spell_command_fuzzy_single_hit(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    # 「冰霜射线」的英文别名只对应一个条目 → 单一候选直接展开
    msgs = collect(p.kb_spell_cmd(ev("/查法术 ray")))
    assert "冰霜射线" in msgs[0]


def test_spell_command_fuzzy_multi_hit(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_spell_cmd(ev("/查法术 火球")))
    assert "相近候选" in msgs[0]
    assert "火球术" in msgs[0]
    assert "火球法杖" not in msgs[0]  # kind=spell 过滤掉物品
    # 物品通道能命中「火球法杖」
    msgs = collect(p.kb_item_cmd(ev("/查物品 火球")))
    assert "火球法杖" in msgs[0]


def test_monster_command_and_typo(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_monster_cmd(ev("/查怪 少年青铜")))
    assert "少年青铜龙" in msgs[0]
    assert "挑战等级CR3（XP700；PB+2）" in msgs[0]


def test_miss(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_spell_cmd(ev("/查法术 不存在的东西")))
    assert "未找到" in msgs[0]


# ---------------------------------------------------------------------------
# /查职业
# ---------------------------------------------------------------------------


def test_class_command_base(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士")))
    text = msgs[0]
    assert "【战士 Fighter】" in text
    # v0.48.0：分层概要总表（每行「N级 名称：一句话概要」）
    assert "1级 战斗风格：" in text
    assert "【第1层 1-4级】" in text
    assert "可选子职：冠军武士" in text


def test_class_command_with_subclass(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    # 显示名（冠军武士）与短名（冠军）都应能查到子职特性
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 冠军武士")))
    assert "◆ 3 级 精通重击" in msgs[0]
    assert "◆ 7 级 非凡运动家" in msgs[0]
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 冠军")))
    assert "◆ 3 级 精通重击" in msgs[0]


def test_class_command_feature_all(tmp_path: Path) -> None:
    """v0.48.0：/查职业 <职业> 特性 → 按层级段分条发送全文（默认版本）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 特性")))
    # 分条：head + 层级段 + 提示，>= 2 条
    assert len(msgs) >= 2
    assert "【战士 Fighter】" in msgs[0]
    # 层段标题独立一条消息
    assert "【战士·第1层 1-4级】" in msgs[1]
    assert "◆ 1 级 战斗风格：" in msgs[1]
    assert "你采取一种特别的作战风格作为专长。" in msgs[1]


def test_class_command_feature_single(tmp_path: Path) -> None:
    """v0.29.0：/查职业 <职业> 特性 <特性名> → 单个特性正文。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 特性 动作如潮")))
    text = msgs[0]
    assert "特性「动作如潮」" in text
    assert "◆ 2 级 动作如潮：" in text
    assert "额外进行一次动作" in text
    # 只输出目标特性
    assert "战斗风格" not in text


def test_class_command_feature_not_found(tmp_path: Path) -> None:
    """v0.29.0：特性名不存在 → 明确提示而非静默。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 特性 不存在的特性")))
    assert "未找到该职业的「不存在的特性」特性" in msgs[0]


def test_class_command_unknown(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 不存在的职业")))
    assert "未找到" in msgs[0]


def test_class_command_edition_param(tmp_path: Path) -> None:
    """v0.48.0：第二参数 2014/2024 覆盖版本。fixture 仅 2014 → 2024 回退提示。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 2014")))
    assert "1级 战斗风格：" in msgs[0]
    # 2024 版无数据（fixture 只有 2014）→ 提示 + 回退
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 2024")))
    assert any("2024 版无特性数据" in m for m in msgs)
    assert "1级 战斗风格：" in "".join(msgs)


def test_class_command_tier_drill(tmp_path: Path) -> None:
    """v0.48.0：第N层钻取 → 该层特性全文。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 第1层")))
    assert "【战士 Fighter】" in msgs[0]
    joined = "\n".join(msgs)
    assert "【战士·第1层 1-4级】" in joined
    assert "◆ 1 级 战斗风格：" in joined
    # 不在该层的特性不出现
    assert "动作如潮" in joined  # 动作如潮 L2 也在第1层


def test_class_command_level_drill(tmp_path: Path) -> None:
    """v0.48.0：N级 / N-M级 钻取 → 命中等级的特性全文。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 1级")))
    joined = "\n".join(msgs)
    assert "战斗风格" in joined
    assert "动作如潮" not in joined  # 2 级特性不在 1 级
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 1-2级")))
    joined = "\n".join(msgs)
    assert "战斗风格" in joined
    assert "动作如潮" in joined
    # 超出范围
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 19级")))
    assert any("没有" in m and "特性数据" in m for m in msgs)


def test_class_command_subclass_priority(tmp_path: Path) -> None:
    """v0.48.0：子职名精确匹配优先于版本/等级词（子职名不会被误判）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 冠军")))
    assert "◆ 3 级 精通重击" in msgs[0]
    # 不存在的子职 → 明确提示 + 候选
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士 不存在的子职")))
    assert "未找到" in msgs[0]
    assert "可选子职：冠军武士" in msgs[0]


def test_class_command_group_rule_edition(tmp_path: Path) -> None:
    """v0.48.0：默认版本 = 群规则（chargen_rule:{origin}）的 edition。

    fixture 战士仅 2014 → 群规则设 2024 时触发「无数据回退」提示，
    证明群规则生效（否则默认 editions[0]=2014 不会回退）。
    """
    p = make_plugin(tmp_path)
    run(p.put_kv_data(
        "chargen_rule:group:1",
        {"edition": "2024", "ability": {"kind": "point_buy"}, "starting_level": 1},
    ))
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士")))
    assert any("2024 版无特性数据" in m for m in msgs)
    # 群规则改回 2014 → 直接命中，无回退提示
    run(p.put_kv_data(
        "chargen_rule:group:1",
        {"edition": "2014", "ability": {"kind": "point_buy"}, "starting_level": 1},
    ))
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士")))
    joined = "\n".join(msgs)
    assert "1级 战斗风格：" in joined
    assert "无特性数据" not in joined


# ---------------------------------------------------------------------------
# /查询 跨库广搜
# ---------------------------------------------------------------------------


def test_search_command_grouped(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_search_cmd(ev("/查询 火球")))
    text = msgs[0]
    assert "跨库搜索「火球」结果" in text
    assert "【法术】" in text and "火球术" in text
    assert "【物品】" in text and "火球法杖" in text


def test_search_command_fulltext(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    # 不加 -全文：仅逐字缩短的 NAME 命中（火焰舌剑），正文匹配的 火球术 不出现
    msgs = collect(p.kb_search_cmd(ev("/查询 火焰伤害")))
    text = msgs[0]
    assert "火焰舌剑" in text
    assert "火球术" not in text
    # 加 -全文：正文命中 火球术/燃烧之手
    msgs = collect(p.kb_search_cmd(ev("/查询 -全文 火焰伤害")))
    assert "火球术" in msgs[0]
    assert "火焰舌剑" in msgs[0]


def test_search_command_no_arg(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_search_cmd(ev("/查询")))
    assert "用法" in msgs[0]


# ---------------------------------------------------------------------------
# /筛怪 /筛法术 /筛物品
# ---------------------------------------------------------------------------


def test_filter_monster_by_damage_and_cr(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_monster_cmd(ev("/筛怪 火焰")))
    text = msgs[0]
    assert "共 1 条符合条件的怪物" in text
    assert "成年红龙" in text
    assert "CR 17" in text
    # 组合伤害 + CR：成年红龙 CR17 被排除 → 空结果
    msgs = collect(p.kb_filter_monster_cmd(ev("/筛怪 火焰 CR5以下")))
    assert "没有符合条件的怪物" in msgs[0]
    msgs = collect(p.kb_filter_monster_cmd(ev("/筛怪 闪电 CR5以下")))
    assert "少年青铜龙" in msgs[0]


def test_filter_monster_unknown_token(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_monster_cmd(ev("/筛怪 火焰 会飞")))
    text = msgs[0]
    assert "成年红龙" in text  # 已识别条件仍生效
    assert "未识别条件：会飞" in text
    # 全部未识别 → 提示没有识别出条件
    msgs = collect(p.kb_filter_monster_cmd(ev("/筛怪 会飞")))
    assert "没有识别出任何筛选条件" in msgs[0]


def test_filter_spell_conditions(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 专注 4环")))
    assert "枯萎术" in msgs[0]
    assert "（4环）" in msgs[0]
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 锥形")))
    assert "燃烧之手" in msgs[0]
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 暗蚀 单体")))
    assert "枯萎术" in msgs[0]
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 30尺")))
    assert "枯萎术" in msgs[0]  # 30 尺内
    assert "火球术" not in msgs[0]  # 150 尺超出
    # 按学派反查（单字母内部值 → 中文输入）
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 惑控")))
    assert "人类定身术" in msgs[0]
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 死灵")))
    assert "枯萎术" in msgs[0]


def test_filter_item_by_property_and_damage(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 暗蚀 灵巧")))
    text = msgs[0]
    assert "暗蚀之刃" in text
    assert "火焰舌剑" not in text  # AND：暗蚀之刃才有暗蚀
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 灵巧")))
    assert "火焰舌剑" in msgs[0]
    assert "长剑" in msgs[0]


def test_filter_spell_class_prefix(tmp_path: Path) -> None:
    """v0.35.0：/筛法术 职业 <职业名> → 职业法术表反查（含中英文名）。"""
    db = tmp_path / "kb" / "dnd_kb.db"
    lookup = Path(__file__).resolve().parent / "fixtures" / "en_spell_lookup.json"
    build(FIXTURE_DIR, db, commit="fixture-abc123", patch_root=NO_PATCH_DIR,
          en_lookup=lookup)
    p = _MemoryPlugin(db)
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 职业 法师 0环")))
    text = msgs[0]
    assert "冰霜射线" in text or "燃烧之手" in text
    # 英文职业名同样可解析
    msgs2 = collect(p.kb_filter_spell_cmd(ev("/筛法术 职业 Wizard 0环")))
    assert "符合条件的法术" in msgs2[0]
    # 不存在的职业 → 未知条件提示
    msgs3 = collect(p.kb_filter_spell_cmd(ev("/筛法术 职业 不存在的职业")))
    assert "没有识别出任何筛选条件" in msgs3[0] or "职业 不存在的职业" in msgs3[0]


def test_filter_help(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_monster_cmd(ev("/筛怪")))
    assert "筛怪用法" in msgs[0]
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 help")))
    assert "筛法术用法" in msgs[0]


def test_filter_feat(tmp_path: Path) -> None:
    """/筛专长：类型/属性提升/先决条件（v0.25.0）。"""
    p = make_plugin(tmp_path)
    # 类型
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 战斗风格")))
    assert "箭术" in msgs[0]
    # 裸词属性提升
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 力量")))
    assert "幸运" in msgs[0]
    # 裸词前置种族（具体到名字）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 精灵")))
    assert "精灵之准" in msgs[0]
    # 裸词前置专长（去括号基础名）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 巨人打击")))
    assert "云巨人之诡诈" in msgs[0]
    # 前缀 + 属性门槛（连写）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 前置属性 智力13")))
    assert "仪式施法者" in msgs[0]
    # 前缀 + 分离形式属性门槛
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 前置属性 感知 13")))
    assert "仪式施法者" in msgs[0]
    # 组合条件：类型 + 属性提升（AND）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 通用 魅力")))
    assert "幸运" in msgs[0]
    assert "仪式施法者" in msgs[0]
    # 帮助
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长")))
    assert "筛专长用法" in msgs[0]


def test_filter_feat_keyword(tmp_path: Path) -> None:
    """/筛专长 能力标签反查（v0.26.0）：裸词自动消歧 + 前缀词 + 多标签 AND。"""
    p = make_enriched_plugin(tmp_path)
    # 裸词标签（直接命中 feat_keyword 值集）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 远程")))
    assert "箭术" in msgs[0]
    assert "远程武器攻击命中提升" in msgs[0]  # 列表展示 AI 概要
    # 裸词标签别名归一（「射击」→ canonical「远程」）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 射击")))
    assert "箭术" in msgs[0]
    # 前缀词「标签」显式指定
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 标签 生命")))
    assert "坚韧" in msgs[0]
    # 多标签 AND：远程 + 命中 → 箭术
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 远程 命中")))
    assert "箭术" in msgs[0]
    # 多标签 AND：防御 命中 → 无交集（防御专长不含命中）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 防御 命中")))
    assert "没有符合条件的专长" in msgs[0]
    # 标签 + 类型组合（AND）
    msgs = collect(p.kb_filter_feat_cmd(ev("/筛专长 施法 通用")))
    assert "仪式施法者" in msgs[0]
    assert "元素掌控" not in msgs[0]  # PHB 无类型标签


def test_feat_detail_shows_summary(tmp_path: Path) -> None:
    """/查专长 详情带 AI 一句话概要（v0.26.0）。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_feat_cmd(ev("/查专长 箭术")))
    text = "\n".join(msgs)
    assert "概要：远程武器攻击命中提升" in text
    assert "远程武器攻击" in text  # 正文仍在


def test_feat_detail_shows_type_and_ability(tmp_path: Path) -> None:
    """/查专长 详情展示类型与属性提升（v0.26.1）。"""
    p = make_plugin(tmp_path)
    # 幸运 XPHB：category G（通用）+ choose 六属性提升
    msgs = collect(p.kb_feat_cmd(ev("/查专长 幸运")))
    text = "\n".join(msgs)
    assert "类型：通用" in text
    assert "属性提升：" in text
    assert "力量" in text and "敏捷" in text
    # 仪式施法者：属性提升 choose 智力/感知/魅力（仅三选一，展示按中文序）
    msgs = collect(p.kb_feat_cmd(ev("/查专长 仪式施法者")))
    text = "\n".join(msgs)
    line = next(l for l in text.splitlines() if "属性提升：" in l)
    assert {"智力", "感知", "魅力"} <= set(line.split("属性提升：", 1)[1].split("、"))
    # 无属性提升的专长（坚韧）不显示该行
    msgs = collect(p.kb_feat_cmd(ev("/查专长 坚韧")))
    text = "\n".join(msgs)
    assert "属性提升" not in text


def test_tool_filter_feat_keywords(tmp_path: Path) -> None:
    """LLM 工具：filter+专长 用 feat_type/feat_keywords 反查（v0.26.0）。"""
    p = make_enriched_plugin(tmp_path)
    # 标签反查
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="专长", feat_keywords="远程"
    ))
    assert "箭术" in text
    assert "远程武器攻击命中提升" in text  # 概要帮助 LLM 理解
    # 多标签 AND（逗号分隔）+ 别名归一
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="专长", feat_keywords="射击,命中"
    ))
    assert "箭术" in text
    # 类型 + 标签组合
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="专长", feat_type="通用", feat_keywords="施法"
    ))
    assert "仪式施法者" in text
    assert "元素掌控" not in text


def test_filter_spell_keyword(tmp_path: Path) -> None:
    """/筛法术 能力标签反查（v0.27.0）：裸词自动消歧 + 前缀词 + 多标签 AND + 环级组合。"""
    p = make_enriched_plugin(tmp_path)
    # 裸词标签「控场」→ 人类定身术（人类定身术 2环惑控）
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 控场")))
    assert "人类定身术" in msgs[0]
    assert "枯萎术" not in msgs[0]
    # 裸词标签别名归一（「控制」→ canonical「控场」）
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 控制")))
    assert "人类定身术" in msgs[0]
    # 前缀词「标签」显式指定
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 标签 定身")))
    assert "人类定身术" in msgs[0]
    # 标签 + 环级组合（AND）：控场 2环 → 人类定身术
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 控场 2环")))
    assert "人类定身术" in msgs[0]
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 控场 3环")))
    assert "没有符合条件的法术" in msgs[0]
    # 多标签 AND：伤害 + 减益 → 枯萎术（4环死灵）
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 伤害 减益")))
    assert "枯萎术" in msgs[0]
    assert "火球术" not in msgs[0]
    # 伤害 3环 → 火球术（PHB + XPHB 双版本）
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 伤害 3环")))
    assert "火球术" in msgs[0]
    # 无标签法术不误入（冰霜射线只有伤害标签，不命中控场）
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 控场")))
    assert "冰霜射线" not in msgs[0]


def test_spell_filter_summary_shown(tmp_path: Path) -> None:
    """/筛法术 结果带 AI 一句话概要（v0.27.0），/查法术 详情同样展示。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_spell_cmd(ev("/筛法术 伤害 3环")))
    assert "爆裂火球灼烧大范围区域" in msgs[0]  # XPHB 版概要
    msgs = collect(p.kb_spell_cmd(ev("/查法术 人类定身术")))
    text = "\n".join(msgs)
    assert "概要：定身一名类人生物" in text
    assert "定身" in text  # 正文仍在


def test_tool_filter_spell_keywords(tmp_path: Path) -> None:
    """LLM 工具：filter+法术 用 spell_keywords 反查（v0.27.0）。"""
    p = make_enriched_plugin(tmp_path)
    # 标签反查 + 概要帮助 LLM 理解
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", spell_keywords="控场"
    ))
    assert "人类定身术" in text
    assert "定身一名类人生物" in text
    # 多标签 AND（逗号分隔）+ 别名归一（「控制」→「控场」）
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", spell_keywords="控制,定身"
    ))
    assert "人类定身术" in text
    # 标签 + 环级组合（AND）：控场 2环
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", level=2, spell_keywords="控场"
    ))
    assert "人类定身术" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", level=3, spell_keywords="控场"
    ))
    assert "未找到符合条件的条目" in text


# ---------------------------------------------------------------------------
# 物品类型反查：base_item / item_type（v0.13.1）
# ---------------------------------------------------------------------------


def test_item_lookup_appends_base_variants(tmp_path: Path) -> None:
    """/查物品 长剑 → 基础条目详情 + 以长剑为基础的魔法武器列表（两条消息）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_item_cmd(ev("/查物品 长剑")))
    text = "\n".join(msgs)
    assert "【长剑" in text  # 基础条目详情
    assert "📎 以「长剑」为基础的魔法物品" in text
    assert "火焰舌剑" in text
    assert "暗蚀之刃" in text
    # 机翻物品在附加列表中标 ⚠️（回归：曾因 _MACHINE_FLAG 未导入而 NameError）
    assert "机翻魔剑" in text
    assert "⚠️机翻" in text
    # 查魔法物品本身：无基础反查 → 不附加
    msgs = collect(p.kb_item_cmd(ev("/查物品 火球法杖")))
    assert not any("📎" in m for m in msgs)


def test_filter_item_by_base_name(tmp_path: Path) -> None:
    """/筛物品 长剑 → 全部长剑系魔法武器（base_item 兜底解析）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 长剑")))
    text = msgs[0]
    assert "火焰舌剑" in text
    assert "暗蚀之刃" in text
    assert "机翻魔剑" in text
    assert "共 3 条" in text
    # 可与其他条件组合
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 长剑 暗蚀")))
    assert "暗蚀之刃" in msgs[0]
    assert "火焰舌剑" not in msgs[0]


def test_filter_item_by_type(tmp_path: Path) -> None:
    """/筛物品 武器 / 权杖 / 药水 → item_type 码表解析。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 武器")))
    text = msgs[0]
    for n in ("长剑", "手弩", "火焰舌剑", "暗蚀之刃", "机翻魔剑"):
        assert n in text
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 权杖")))
    assert "火球法杖" in msgs[0]
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 药水")))
    assert "治疗药水" in msgs[0]


def test_filter_item_by_rarity(tmp_path: Path) -> None:
    """/筛物品 按稀有度反查：珍稀 / 非魔法物品 / 魔法物品（v0.15.0）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 珍稀")))
    text = msgs[0]
    assert "火球法杖" in text
    assert "（珍稀）" in text  # 列表后缀中文
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 非魔法物品")))
    text = msgs[0]
    assert "长剑" in text
    assert "手弩" in text
    assert "（非魔法物品）" in text
    msgs = collect(p.kb_filter_item_cmd(ev("/筛物品 魔法物品")))
    text = msgs[0]
    assert "火球法杖" in text
    assert "长剑" not in text  # 魔法物品反查排除基础物品


def test_search_command_school_fallback(tmp_path: Path) -> None:
    """/查询 惑控 → 名称无命中时按学派列出法术。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_search_cmd(ev("/查询 惑控")))
    assert "人类定身术" in msgs[0]
    assert "符合条件的法术" in msgs[0]
    # 非学派词仍报未找到
    msgs = collect(p.kb_search_cmd(ev("/查询 不存在的词xyz")))
    assert "未找到" in msgs[0]


# ---------------------------------------------------------------------------
# /kb version
# ---------------------------------------------------------------------------


def test_kb_version_command(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_cmd(ev("/kb version")))
    assert "知识库版本" in msgs[0]
    assert "5etools 中文站" in msgs[0]
    assert "CC BY-NC-SA 4.0" in msgs[0]


def test_kb_update_not_open_yet(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_cmd(ev("/kb update")))
    assert "尚未开放" in msgs[0]


# ---------------------------------------------------------------------------
# custom_prefix_route
# ---------------------------------------------------------------------------


def test_custom_prefix_kb_lookup(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = ev(".查法术 火球术", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs and "火球术" in msgs[0]
    assert e.stopped


def test_custom_prefix_kb_class(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = ev(".查职业 战士", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs and "战士" in msgs[0]
    assert e.stopped


def test_custom_prefix_kb_version(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = ev(".kb version", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs and "知识库版本" in msgs[0]
    assert e.stopped


def test_custom_prefix_kb_search_and_filter(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = ev(".查询 火球", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs and "火球术" in msgs[0]
    assert e.stopped
    e = ev(".筛怪 火焰", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs and "成年红龙" in msgs[0]
    assert e.stopped


def test_custom_prefix_unrelated_message_passthrough(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    run(p.put_kv_data("custom_prefix:group:9", "."))
    e = ev(".hello", origin="group:9")
    msgs = collect(p.custom_prefix_route(e))
    assert msgs == []
    assert not e.stopped


def test_custom_prefix_kb_zh_aliases(tmp_path: Path) -> None:
    """查/筛系列中文别名与英文别名均进自定义前缀路由（v0.41.2）。"""
    cases = [
        (".怪物 少年青铜", "少年青铜龙"),  # 查怪 中文别名
        (".物品 火球", "火球法杖"),  # 查物品 中文别名
        (".专长 幸运", "类型：通用"),  # 查专长 中文别名
        (".背景 侍僧", "侍僧"),  # 查背景 中文别名
        (".状态 目盲", "目盲"),  # 查状态 中文别名
        (".种族 矮人", "矮人"),  # 查种族 中文别名
        (".q 火球", "火球术"),  # 查询 英文别名
        (".筛怪物 火焰", "成年红龙"),  # 筛怪 中文别名
        (".筛魔法 锥形", "燃烧之手"),  # 筛法术 中文别名
        (".筛道具 灵巧", "火焰舌剑"),  # 筛物品 中文别名
        (".筛血统 飞行", "阿斯莫"),  # 筛种族 中文别名
    ]
    p = make_plugin(tmp_path)  # 只 build 一次，循环复用
    run(p.put_kv_data("custom_prefix:group:9", "."))
    for cmd, expect in cases:
        e = ev(cmd, origin="group:9")
        msgs = collect(p.custom_prefix_route(e))
        assert msgs, f"{cmd} 应被自定义前缀路由命中"
        assert expect in msgs[0], f"{cmd} 输出应含 {expect}"
        assert e.stopped, f"{cmd} 应 stop_event"


def test_custom_prefix_kb_filter_background_aliases(tmp_path: Path) -> None:
    """筛背景 主命令与两个别名进自定义前缀路由（特征反查依赖富化标签）。"""
    p = make_enriched_plugin(tmp_path)
    run(p.put_kv_data("custom_prefix:group:9", "."))
    for cmd in (".筛背景 教士", ".bfilter 教士", ".背景筛 教士"):
        e = ev(cmd, origin="group:9")
        msgs = collect(p.custom_prefix_route(e))
        assert msgs, f"{cmd} 应被自定义前缀路由命中"
        assert "侍僧" in msgs[0], f"{cmd} 输出应含 侍僧"
        assert e.stopped, f"{cmd} 应 stop_event"

# ---------------------------------------------------------------------------
# query_dnd_knowledge llm_tool
# ---------------------------------------------------------------------------


def test_tool_detail_dual_version(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(ev(""), action="detail", name="火球术"))
    assert "找到 2 个版本" in text
    assert "不要编造" in text  # 零幻觉约束句


def test_tool_detail_with_kind(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="detail", kind="物品", name="火球法杖"
    ))
    assert "火球法杖" in text
    assert "稀有度：珍稀" in text  # v0.15.0 稀有度中文显示


def test_tool_search(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="search", kind="法术", name="火球"
    ))
    assert "相近候选" in text
    assert "不要编造" in text


def test_tool_filter_dragon_cr3(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""),
        action="filter",
        kind="怪物",
        monster_type="龙类",
        cr_min=3,
        cr_max=3,
    ))
    assert "少年青铜龙" in text
    assert "成年红龙" not in text


def test_tool_filter_spell_level(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", level=0
    ))
    assert "冰霜射线" in text
    assert "火球术" not in text


def test_tool_filter_item_rarity(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="物品", rarity="珍稀"
    ))
    assert "火球法杖" in text


def test_tool_class_features(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="class_features", name="战士"
    ))
    assert "可选子职：冠军武士" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="class_features", name="战士", subclass="冠军武士"
    ))
    assert "精通重击" in text
    # 短名也可命中
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="class_features", name="战士", subclass="冠军"
    ))
    assert "精通重击" in text


def test_tool_class_features_feature(tmp_path: Path) -> None:
    """v0.48.0：LLM 工具 feature 参数细化本职特性（按层级段全文）。"""
    p = make_plugin(tmp_path)
    # 全部本职特性全文
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="class_features", name="战士", feature="*"
    ))
    assert "【战士·第1层 1-4级】" in text
    assert "◆ 1 级 战斗风格：" in text
    # 单个特性（跨版本）
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="class_features", name="战士", feature="动作如潮"
    ))
    assert "特性「动作如潮」" in text
    assert "额外进行一次动作" in text
    # 未匹配
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="class_features", name="战士", feature="不存在的特性"
    ))
    assert "未找到该职业的「不存在的特性」特性" in text


def test_tool_version(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(ev(""), action="version"))
    assert "知识库版本" in text


def test_tool_missing_name(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(ev(""), action="detail"))
    assert "name" in text


def test_tool_invalid_kind_filter(tmp_path: Path) -> None:
    """filter 已支持专长（v0.26.0）；不支持的类别仍报错。"""
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="专长"
    ))
    assert "符合条件的条目" in text
    # 未知类别仍然拒绝（职业走 class_features 提示，状态类未开放筛选）
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="状态"
    ))
    assert "暂不支持筛选" in text


def test_tool_unknown_action(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(ev(""), action="whatever"))
    assert "未知的 action" in text


# ---------------------------------------------------------------------------
# query_dnd_knowledge：特性反查参数（v0.13.0）
# ---------------------------------------------------------------------------


def test_tool_filter_by_damage_type(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="怪物", damage_type="火焰"
    ))
    assert "成年红龙" in text
    assert "少年青铜龙" not in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="物品", damage_type="黯蚀"
    ))
    assert "暗蚀之刃" in text  # 别名「黯蚀」→ canonical「暗蚀」
    assert "不要编造" in text


def test_tool_filter_condition_and_components(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", condition="麻痹"
    ))
    assert "枯萎术" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", concentration=1, level=4
    ))
    assert "枯萎术" in text
    assert "火球术" not in text


def test_tool_filter_item_property(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="物品", weapon_property="灵巧"
    ))
    assert "长剑" in text
    assert "暗蚀之刃" in text


def test_tool_filter_spell_shape_target(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", shape="锥形"
    ))
    assert "燃烧之手" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="法术", target="多体"
    ))
    assert "火球术" in text


def test_tool_filter_base_item_and_item_type(tmp_path: Path) -> None:
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="物品", base_item="长剑"
    ))
    assert "火焰舌剑" in text
    assert "暗蚀之刃" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="物品", item_type="权杖"
    ))
    assert "火球法杖" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="物品",
        base_item="长剑", damage_type="暗蚀"
    ))
    assert "暗蚀之刃" in text
    assert "火焰舌剑" not in text


# ---------------------------------------------------------------------------
# 状态 / 种族（v0.16.0）
# ---------------------------------------------------------------------------


def test_condition_lookup_command(tmp_path: Path) -> None:
    """/查状态 目盲 → 2014/2024 双版本详情。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_condition_cmd(ev("/查状态 目盲")))
    text = "\n".join(msgs)
    assert "【目盲 Blinded】" in text
    assert "无法看见" in text


def test_race_lookup_command(tmp_path: Path) -> None:
    """/查种族 阿斯莫 → 头部信息 + 正文（同名多版本）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_race_cmd(ev("/查种族 阿斯莫")))
    text = "\n".join(msgs)
    assert "【阿斯莫 Aasimar】" in text
    assert "【种族信息】" in text
    assert "体型：中型" in text
    assert "黑暗视觉：60尺" in text


def test_filter_race_by_resist_and_innate(tmp_path: Path) -> None:
    """/筛种族 火焰（天生抗性）+ /筛种族 火球术（法术名兜底天生施法）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 火焰")))
    text = msgs[0]
    assert "阿斯莫" in text
    assert "（步行30尺）" in text  # 列表速度后缀
    # 法术名兜底：未知 token → 查法术库 → innate_spell 反查
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 火球术")))
    assert "阿斯莫" in msgs[0]


def test_filter_race_by_speed_darkvision_size(tmp_path: Path) -> None:
    """/筛种族 飞行 / 黑暗视觉 60尺 / 中型。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 飞行")))
    text = msgs[0]
    assert "阿斯莫" in text
    assert "飞行30尺" in text
    # 黑暗视觉 60尺（词 + 距离 token）
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 黑暗视觉 60尺")))
    assert "半精灵" in msgs[0]
    assert "矮人" not in msgs[0]  # 矮人无黑暗视觉
    # 体型
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 中型")))
    assert "共 6 条符合条件的种族" in msgs[0]


def test_tool_filter_race(tmp_path: Path) -> None:
    """LLM 工具种族筛选：抗性（damage_type→dmg_resist）+ 天生施法 + 速度。"""
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="种族", damage_type="火焰"
    ))
    assert "阿斯莫" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="种族", innate_spell="舞光术"
    ))
    assert "阿斯莫" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="种族",
        speed_type="飞行", speed_min=30,
    ))
    assert "阿斯莫" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="种族",
        speed_type="飞行", speed_min=60,
    ))
    assert "未找到" in text


def test_tool_detail_condition(tmp_path: Path) -> None:
    """LLM 工具状态查询：kind=状态。"""
    p = make_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="detail", kind="状态", name="目盲"
    ))
    assert "无法看见" in text


# ---------------------------------------------------------------------------
# /筛职业 /筛子职（v0.33.0 职业/子职富化反查）
# ---------------------------------------------------------------------------


def test_filter_class_by_role(tmp_path: Path) -> None:
    """裸词定位反查：/筛职业 武者 → 战士（武者定位）。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_class_cmd(ev("/筛职业 武者")))
    text = msgs[0]
    assert "战士" in text
    assert "法师" not in text
    # 前缀词显式指定定位
    msgs = collect(p.kb_filter_class_cmd(ev("/筛职业 定位 奥法")))
    assert "法师" in msgs[0]
    assert "战士" not in msgs[0]


def test_filter_class_by_keyword(tmp_path: Path) -> None:
    """裸词关键字反查（组合 AND）：/筛职业 近战 爆发 → 战士。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_class_cmd(ev("/筛职业 近战 爆发")))
    assert "战士" in msgs[0]
    assert "法师" not in msgs[0]
    # 词表词未打标时无命中
    msgs = collect(p.kb_filter_class_cmd(ev("/筛职业 标签 治疗")))
    assert "未找到" in msgs[0] or "没有符合条件" in msgs[0]


def test_filter_class_unknown(tmp_path: Path) -> None:
    """合法条件 + 未识别词：提示未识别条件。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_class_cmd(ev("/筛职业 武者 完全未知词")))
    assert "未识别条件" in msgs[0]


def test_filter_subclass_by_keyword(tmp_path: Path) -> None:
    """子职裸词/前缀词反查：/筛子职 战术 → 冠军武士；标签 防护 → 奥法骑士。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_subclass_cmd(ev("/筛子职 战术")))
    assert "冠军武士" in msgs[0]
    msgs = collect(p.kb_filter_subclass_cmd(ev("/筛子职 标签 防护")))
    assert "奥法骑士" in msgs[0]


def test_class_command_shows_role_and_summary(tmp_path: Path) -> None:
    """/查职业 头部展示职业定位与 AI 概要。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_class_cmd(ev("/查职业 战士")))
    text = msgs[0]
    assert "定位：武者" in text
    assert "概要：精通武器与护甲的战斗大师" in text


def test_tool_filter_class_keywords(tmp_path: Path) -> None:
    """LLM 工具职业筛选：定位 + 关键字（逗号分隔 AND）。"""
    p = make_enriched_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="职业", class_role="武者"
    ))
    assert "战士" in text
    assert "法师" not in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="职业",
        class_keywords="近战,爆发",
    ))
    assert "战士" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="职业",
        class_keywords="奥术施法",
    ))
    assert "法师" in text
    assert "战士" not in text


def test_tool_filter_subclass_keywords(tmp_path: Path) -> None:
    """LLM 工具子职筛选：关键字（逗号分隔 AND）。"""
    p = make_enriched_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="子职", subclass_keywords="防护"
    ))
    assert "奥法骑士" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="子职", subclass_keywords="战术"
    ))
    assert "冠军武士" in text


def test_filter_class_summary_shown(tmp_path: Path) -> None:
    """筛选结果列表用 AI 概要（class_summary 优先于正文截断）。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_class_cmd(ev("/筛职业 武者")))
    assert "精通武器与护甲的战斗大师" in msgs[0]



# ---------------------------------------------------------------------------
# v0.34.0 种族/背景富化：/筛种族 标签、/筛背景、LLM 工具、概要展示
# ---------------------------------------------------------------------------


def test_filter_race_by_keyword(tmp_path: Path) -> None:
    """种族裸词/前缀词标签反查：/筛种族 神圣 → 阿斯莫；标签 免疫 → 骷髅/流浆体。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 神圣")))
    assert "阿斯莫" in msgs[0]
    assert "矮人" not in msgs[0]
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 标签 免疫")))
    assert "骷髅" in msgs[0]
    assert "流浆体" in msgs[0]
    # 词表外自由词（粘液）裸词也可反查（值集查询不受词表限制）
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 粘液")))
    assert "流浆体" in msgs[0]


def test_filter_race_keyword_combination(tmp_path: Path) -> None:
    """种族标签组合 AND：/筛种族 神圣 治疗 → 仅阿斯莫 DMG（排除半精灵）。

    注：「光耀」等伤害词裸词在种族语境优先走「天生抗性」（dmg_resist，
    v0.16 既有语义），语义大类需前缀词「标签 光耀」——与法术「防护」
    学派/大类同款取舍。
    """
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 神圣 治疗")))
    assert "阿斯莫" in msgs[0]
    assert "半精灵" not in msgs[0]
    # 前缀词显式指定标签维度
    msgs = collect(p.kb_filter_race_cmd(ev("/筛种族 标签 神圣 标签 光耀")))
    assert "阿斯莫" in msgs[0]


def test_filter_background_cmd(tmp_path: Path) -> None:
    """新命令 /筛背景：裸词技能/身份反查（背景此前无筛指令）。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_background_cmd(ev("/筛背景 宗教")))
    assert "侍僧" in msgs[0]
    msgs = collect(p.kb_filter_background_cmd(ev("/筛背景 教士")))
    assert "侍僧" in msgs[0]
    # 组合 AND
    msgs = collect(p.kb_filter_background_cmd(ev("/筛背景 洞悉 起始专长")))
    assert "侍僧" in msgs[0]
    # 未打标词：正常进入筛选（非「未识别」），无命中
    msgs = collect(p.kb_filter_background_cmd(ev("/筛背景 隐匿")))
    assert "没有符合条件" in msgs[0] or "未找到" in msgs[0]


def test_filter_background_prefix_and_alias(tmp_path: Path) -> None:
    """/筛背景 前缀词「标签」+ 别名归一。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_filter_background_cmd(ev("/筛背景 标签 宗教")))
    assert "侍僧" in msgs[0]


def test_race_command_shows_summary(tmp_path: Path) -> None:
    """/查种族 头部展示 AI 概要。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_race_cmd(ev("/查种族 矮人")))
    assert "概要：坚韧的山地工匠" in msgs[0]


def test_background_command_shows_summary(tmp_path: Path) -> None:
    """/查背景 头部展示 AI 概要（2014/2024 双版本各自带出）。"""
    p = make_enriched_plugin(tmp_path)
    msgs = collect(p.kb_background_cmd(ev("/查背景 侍僧")))
    text = msgs[0]
    assert "概要：献身神祇的侍奉者" in text
    assert "概要：蒙受神恩的年轻教士" in text


def test_tool_filter_race_keywords(tmp_path: Path) -> None:
    """LLM 工具种族筛选：race_keywords（逗号分隔 AND）。"""
    p = make_enriched_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="种族", race_keywords="神圣,光耀"
    ))
    assert "阿斯莫" in text
    assert "矮人" not in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="种族", race_keywords="体质"
    ))
    assert "矮人" in text


def test_tool_filter_background_keywords(tmp_path: Path) -> None:
    """LLM 工具背景筛选：background_keywords（逗号分隔 AND）。"""
    p = make_enriched_plugin(tmp_path)
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="背景", background_keywords="起始专长"
    ))
    assert "侍僧" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="背景", background_keywords="宗教,洞悉"
    ))
    assert "侍僧" in text
    text = run(p.query_dnd_knowledge_tool(
        ev(""), action="filter", kind="背景", background_keywords="隐匿"
    ))
    assert "未找到" in text


# ---------------------------------------------------------------------------
# v0.45.0：怪物筛怪新维度（伤害细分/状态免疫/速度/感官/阵营/特性）
# ---------------------------------------------------------------------------


def test_parse_monster_suffix_words() -> None:
    """后缀词解析：伤害四类/免疫双通道（伤害优先）/速度类型。"""
    from astrbot_plugin_trpg_assistant.main import _parse_monster_suffix

    assert _parse_monster_suffix("火焰伤害") == ("dmg_dealt", "火焰")
    assert _parse_monster_suffix("火焰抗性") == ("dmg_resist", "火焰")
    assert _parse_monster_suffix("火焰免疫") == ("dmg_immune", "火焰")
    assert _parse_monster_suffix("火焰易伤") == ("dmg_vuln", "火焰")
    # 「X免疫」伤害词表优先（毒素=伤害），否则落状态（震慑/中毒=状态）
    assert _parse_monster_suffix("毒素免疫") == ("dmg_immune", "毒素")
    assert _parse_monster_suffix("震慑免疫") == ("condition_immune", "震慑")
    assert _parse_monster_suffix("中毒免疫") == ("condition_immune", "中毒")
    # 速度后缀归一为中文 tag 值（构建期 speed_type facet 存中文）
    assert _parse_monster_suffix("掘穴速度") == ("speed_type", "掘穴")
    assert _parse_monster_suffix("飞行速度") == ("speed_type", "飞行")
    # 裸词不落后缀
    assert _parse_monster_suffix("火焰") is None
    assert _parse_monster_suffix("再生") is None
    assert _parse_monster_suffix("守序善良") is None


def test_filter_monster_new_dimensions(tmp_path: Path) -> None:
    """v0.45.0：/筛怪 伤害免疫/抗性/状态免疫/速度/感官/阵营/特性维度。"""
    p = make_plugin(tmp_path)
    # 伤害免疫/抗性细分（少年青铜龙：闪电免疫、寒冷抗性）
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 闪电免疫")))[0]
    assert "少年青铜龙" in text
    assert "成年红龙" not in text
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 寒冷抗性")))[0]
    assert "少年青铜龙" in text
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 火焰免疫")))[0]
    assert "没有符合条件的怪物" in text
    # 状态免疫（少年青铜龙：恐慌免疫）
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 恐慌免疫")))[0]
    assert "少年青铜龙" in text
    # 速度类型（少年青铜龙：飞行速度）
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 飞行速度")))[0]
    assert "少年青铜龙" in text
    # 感官裸词（少年青铜龙：黑暗视觉）
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 黑暗视觉")))[0]
    assert "少年青铜龙" in text
    # 特性名裸词（少年青铜龙：两栖）
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 两栖")))[0]
    assert "少年青铜龙" in text
    # 阵营裸词（少年青铜龙守序善良 / 成年红龙混乱邪恶 / 恐狼无阵营）
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 守序善良")))[0]
    assert "少年青铜龙" in text
    assert "成年红龙" not in text
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 混乱邪恶")))[0]
    assert "成年红龙" in text
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 无阵营")))[0]
    assert "恐狼" in text
    # 结果提示行含新维度引导
    text = collect(p.kb_filter_monster_cmd(ev("/筛怪 闪电免疫")))[0]
    assert "火焰免疫" in text and "真实视觉" in text


# ---------------------------------------------------------------------------
# /查祈唤 /查战技 /查修法 /查风格 /筛选项（v0.50.0 可定制职业选项）
# ---------------------------------------------------------------------------


def test_invocation_command_dual_version(tmp_path: Path) -> None:
    """v0.50.0：/查祈唤 返回该选项全部版本（2014/2024）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_invocation_cmd(ev("/查祈唤 苦痛魔爆")))
    text = "\n".join(msgs)
    assert "苦痛魔爆｜Agonizing Blast" in text
    assert "类型：魔能祈唤" in text
    assert "先决：习得戏法 魔能爆" in text
    assert "版本：PHB·2014" in text and "版本：XPHB·2024" in text


def test_maneuver_command(tmp_path: Path) -> None:
    """v0.50.0：/查战技 伏击 → 战技类型展示。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_maneuver_cmd(ev("/查战技 伏击")))
    text = msgs[0]
    assert "伏击｜Ambush" in text
    assert "类型：战技" in text
    assert "卓越骰" in text


def test_metamagic_command_shows_cost(tmp_path: Path) -> None:
    """v0.50.0：/查修法 消耗行直接展示（非「先决」前缀）。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_metamagic_cmd(ev("/查修法 谨慎法术")))
    text = msgs[0]
    assert "类型：超魔法" in text
    assert "消耗：1术法点" in text


def test_fighting_style_command(tmp_path: Path) -> None:
    """v0.50.0：/查风格 战斗风格专长。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_fighting_style_cmd(ev("/查风格 箭术")))
    text = msgs[0]
    assert "类型：战斗风格" in text
    assert "先决：战斗风格特性" in text


def test_opt_filter_by_type(tmp_path: Path) -> None:
    """v0.50.0：/筛选项 类型 祈唤 / 裸词 战技。"""
    p = make_plugin(tmp_path)
    text = collect(p.kb_filter_opt_cmd(ev("/筛选项 类型 祈唤")))[0]
    assert "魔能祈唤" in text and "苦痛魔爆" in text
    text = collect(p.kb_filter_opt_cmd(ev("/筛选项 战技")))[0]
    assert "战技" in text and "伏击" in text


def test_opt_filter_by_prereq(tmp_path: Path) -> None:
    """v0.50.0：/筛选项 先决 关键词 → 子串匹配。"""
    p = make_plugin(tmp_path)
    text = collect(p.kb_filter_opt_cmd(ev("/筛选项 先决 魔能爆")))[0]
    assert "苦痛魔爆" in text


def test_opt_lookup_miss(tmp_path: Path) -> None:
    """v0.50.0：查不存在的祈唤 → 未找到。"""
    p = make_plugin(tmp_path)
    msgs = collect(p.kb_invocation_cmd(ev("/查祈唤 不存在的祈唤")))
    assert "未找到" in msgs[0]

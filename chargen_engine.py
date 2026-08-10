"""
chargen_engine.py — 规则引擎：自动重算角色卡战斗字段 base 层（v0.18.0）。

输入 CharacterSheet（classes/种族/背景/装备槽），输出重算后的 base 层：

- hp_max.base         生命值：首职首级满骰 + 其余等级期望值 + 体修×总等级
- ac.base             护甲/盾牌/无甲防御（野蛮人/武僧/龙脉术士，取最高）
- spell_slots[k].base 法术位：full/half/third/artificer/pact + 兼职施法者合并
- attack_bonuses[k].base：装备槽武器与各施法职业「法术攻击」加值
- initiative.base     先攻：敏捷修正（v0.30.0）

铁律：**只动 base 不动 bonus**——bonus 是房规调整层，任何重算都不得覆盖。
写回策略：生成的条目重算 base 并按名保留 bonus；生成集外的条目（玩家自建）
整体保留（base+bonus 都不动）。

依赖方向：engine → character（数据模型）与 kb（查询），不 import main/chargen。
法术位表：5etools-cn 镜像的 classTable 行被剥空，按 casterProgression 硬编码
标准表（见模块常量 FULL_SLOTS / PACT_SLOTS）。2024 圣武士/游侠在源数据中
casterProgression 为 "artificer"（1 级即有施法，向上取整），与 2014 的 "1/2"
并存，引擎按字段值双轨处理，无需按版本特判职业名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .character import CharacterSheet

# ---------------------------------------------------------------------------
# 硬编码规则表（源数据 classTable 无法术位列）
# ---------------------------------------------------------------------------

# 全施法者标准法术位表（20 级 × 9 环，索引 = 施法者等级 - 1）。
# 与 2014/2024 PHB 标准表一致（2024 仅改部分特性名，表格数字不变）。
FULL_SLOTS: tuple[tuple[int, ...], ...] = (
    (2, 0, 0, 0, 0, 0, 0, 0, 0),   # 1
    (3, 0, 0, 0, 0, 0, 0, 0, 0),   # 2
    (4, 2, 0, 0, 0, 0, 0, 0, 0),   # 3
    (4, 3, 0, 0, 0, 0, 0, 0, 0),   # 4
    (4, 3, 2, 0, 0, 0, 0, 0, 0),   # 5
    (4, 3, 3, 0, 0, 0, 0, 0, 0),   # 6
    (4, 3, 3, 1, 0, 0, 0, 0, 0),   # 7
    (4, 3, 3, 2, 0, 0, 0, 0, 0),   # 8
    (4, 3, 3, 3, 1, 0, 0, 0, 0),   # 9
    (4, 3, 3, 3, 2, 0, 0, 0, 0),   # 10
    (4, 3, 3, 3, 2, 1, 0, 0, 0),   # 11
    (4, 3, 3, 3, 2, 1, 0, 0, 0),   # 12
    (4, 3, 3, 3, 2, 1, 1, 0, 0),   # 13
    (4, 3, 3, 3, 2, 1, 1, 0, 0),   # 14
    (4, 3, 3, 3, 2, 1, 1, 1, 0),   # 15
    (4, 3, 3, 3, 2, 1, 1, 1, 0),   # 16
    (4, 3, 3, 3, 2, 1, 1, 1, 1),   # 17
    (4, 3, 3, 3, 3, 1, 1, 1, 1),   # 18
    (4, 3, 3, 3, 3, 2, 1, 1, 1),   # 19
    (4, 3, 3, 3, 3, 2, 2, 1, 1),   # 20
)

# 邪术师（魔契师）短休法术位表：20 级 → (位数, 环阶)。
# 2014 与 2024 一致（2024 仅把特性改名，表格数字未变）。
PACT_SLOTS: tuple[tuple[int, int], ...] = (
    (1, 1), (2, 1), (2, 2), (2, 2), (2, 3), (2, 3), (2, 4), (2, 4),
    (2, 5), (2, 5), (3, 5), (3, 5), (3, 5), (3, 5), (3, 5), (3, 5),
    (4, 5), (4, 5), (4, 5), (4, 5),
)

# 无甲防御（职业名 → (加值来源, 可否持盾)）。
# 加值来源：属性缩写 → 10 + 该属性修正；整数 → 固定底值（如龙鳞 13）+ 敏修。
# 仅当未穿甲时适用；与穿甲 AC 取最高值。
UNARMORED: dict[str, tuple[str | int, bool]] = {
    "野蛮人": ("con", True),   # 10 + 敏 + 体，可持盾
    "武僧": ("wis", False),    # 10 + 敏 + 感，不可持盾/穿甲
}

# 按子职触发的无甲防御（子职显示名 → 固定底值）。
# 术士「龙族血脉」：13 + 敏，不可穿甲（持盾按计划不叠加）。
_UNARMORED_SUBCLASS: dict[str, int] = {"龙族血脉": 13}

# ---------------------------------------------------------------------------
# 武器分类静态词表（v0.21 武器熟练判定用）
# ---------------------------------------------------------------------------
# kb 数据库无「简易/军用」分类字段（item_combat.armor_type 的 M/R 是近战/远程），
# 故按 PHB/XPHB 硬编码中文名词表。中文名已与 kb_data/dnd_kb.db 中 2024（XPHB）
# 基础武器条目逐一核对（短棒≠木棒、巨锤≠重锤、链枷≠连枷、钉头锤≠晨星、
# 长戟=halberd、长矛=pike、骑枪=lance）。词表外武器（火器/魔法武器等）走
# 「回退=熟练」分支，默认不扣熟练加值，细则交 DM。

SIMPLE_WEAPONS: frozenset[str] = frozenset({
    "短棒", "匕首", "巨棒", "手斧", "标枪", "轻锤", "硬头锤", "长棍", "镰刀", "矛",
    "轻弩", "飞镖", "短弓", "投石索",
})
MARTIAL_WEAPONS: frozenset[str] = frozenset({
    "战斧", "战镐", "链枷", "长柄刀", "巨斧", "巨剑", "长剑", "弯刀", "短剑",
    "刺剑", "三叉戟", "战锤", "鞭子", "巨锤", "长矛", "长戟", "骑枪",
    "手弩", "重弩", "长弓", "吹箭筒",
})
# 熟练条目别名 → 武器类别名（/卡 熟练 武器 里玩家填的「简易武器/军用武器」等）。
_WEAPON_CATEGORY_ALIASES: dict[str, str] = {
    "简易武器": "simple", "简易": "simple", "simple": "simple",
    "军用武器": "martial", "军用": "martial", "martial": "martial",
}
# 类别名 → 词表映射。
_WEAPON_CATEGORY_TABLE: dict[str, frozenset[str]] = {
    "simple": SIMPLE_WEAPONS,
    "martial": MARTIAL_WEAPONS,
}


def _resolve_base_weapon(weapon_name: str, kb: Any = None) -> str | None:
    """把武器名解析回「基础武器名」（v0.21.1）。

    优先级：武器名本身在词表 → kb 的 base_item（雷神之锤→巨锤）→
    词表后缀（+1 巨锤 → 巨锤）。解析不出（火器/原创武器等）返回 None，
    由调用方回退默认熟练（细则交 DM）。
    """
    name = weapon_name.strip()
    if name in SIMPLE_WEAPONS or name in MARTIAL_WEAPONS:
        return name
    if kb is not None:
        try:
            base = kb.item_base_item(name)
        except Exception:  # noqa: BLE001 — kb 不可用/查询异常不阻断
            base = ""
        if base and (base in SIMPLE_WEAPONS or base in MARTIAL_WEAPONS):
            return base
    for w in SIMPLE_WEAPONS | MARTIAL_WEAPONS:
        if name.endswith(w):
            return w
    return None


def _weapon_proficient(
    sheet: CharacterSheet, weapon_name: str, kb: Any = None
) -> bool:
    """装备武器是否熟练（v0.21.1：魔法武器按基础武器判定）。

    判定顺序（任一满足即熟练）：
    1. weapon_proficiencies 为空 → True（现状兼容：未维护武器熟练的旧卡行为不变）；
    2. 精确命中武器名（空白/大小写归一）→ True；
    3. 能解析出基础武器名（武器本身在词表 / kb base_item / 词表后缀）→
       按熟练项中的类别词（简易武器/军用武器）严格判定；
    4. 解析不出类别（火器、原创武器等）→ True（回退默认熟练，细则交 DM）。
    """
    profs = [p.strip() for p in sheet.weapon_proficiencies if p and p.strip()]
    if not profs:
        return True
    norm_weapon = weapon_name.strip().lower()
    for p in profs:
        if p.lower() == norm_weapon:
            return True
    base = _resolve_base_weapon(weapon_name, kb)
    if base is not None:
        for p in profs:
            category = _WEAPON_CATEGORY_ALIASES.get(p.lower())
            table = _WEAPON_CATEGORY_TABLE.get(category or "")
            if table and base in table:
                return True
        return False
    return True

# 施法进度类型 → 折算函数（兼职合并用；pact 独立处理不并入）。
_CASTER_EFFECTIVE = {
    "full": lambda lv: lv,
    "1/2": lambda lv: lv // 2,        # 2014 圣武士/游侠：1 级无施法
    "1/3": lambda lv: lv // 3,        # 奥法骑士/诡术师：3 级起
    "artificer": lambda lv: (lv + 1) // 2,  # 奇械师/2024 半施法：向上取整
}


@dataclass
class RecalcReport:
    """重算结果：变更摘要 + 警告（供命令/工具返回文本）。"""

    changes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.changes or self.warnings)

    @property
    def text(self) -> str:
        lines = list(self.changes)
        if self.warnings:
            lines.append("注意：" + "；".join(self.warnings))
        return "；".join(lines) if lines else "无变化"


# ---------------------------------------------------------------------------
# 子算法
# ---------------------------------------------------------------------------


def _fmt_change(label: str, old: int, new: int) -> str:
    return f"{label} {old}→{new}" if old != new else f"{label} {new}"


def _calc_hp(sheet: CharacterSheet, kb: Any, report: RecalcReport) -> int:
    """生命值：首职首级满骰，其余等级 ⌊faces/2⌋+1，+体修×总等级。"""
    con_mod = sheet.get_ability_modifier("con")
    total = con_mod * max(sheet.level, 1)
    if not sheet.classes:
        return total
    first = True
    for cl in sheet.classes:
        row = kb.class_combat(cl.class_name, sheet.edition)
        if row is None or not row.hd_faces:
            report.warnings.append(f"未找到 {cl.class_name} 的生命骰数据，该职业未计入 HP")
            continue
        faces = row.hd_faces
        avg = faces // 2 + 1
        for _ in range(cl.level):
            if first:
                total += faces
                first = False
            else:
                total += avg
    return total


def _calc_spell_slots(
    sheet: CharacterSheet, kb: Any, report: RecalcReport
) -> dict[str, int]:
    """法术位：各职业折算有效施法等级合并查 full 表；pact 独立。

    返回 {环阶("1".."9"): 位数}；pact 额外给 "pact"（位数）与 "pact_level"（环阶）。
    """
    merged = 0
    pact_count = 0
    pact_level = 0
    for cl in sheet.classes:
        row = kb.class_combat(cl.class_name, sheet.edition)
        caster = row.caster if row else ""
        # 职业本身无施法进度时，尝试子职进度（奥法骑士/诡术师 1/3）
        if not caster and cl.subclass:
            sub = kb.subclass_caster(cl.class_name, cl.subclass, sheet.edition)
            if sub and sub[0]:
                caster = sub[0]
        if caster == "pact":
            if 1 <= cl.level <= 20:
                pact_count = PACT_SLOTS[cl.level - 1][0]
                pact_level = PACT_SLOTS[cl.level - 1][1]
            continue
        eff = _CASTER_EFFECTIVE.get(caster)
        if eff:
            merged += eff(cl.level)
    slots: dict[str, int] = {}
    merged = min(merged, 20)
    if merged >= 1:
        row = FULL_SLOTS[merged - 1]
        for i, count in enumerate(row):
            if count:
                slots[str(i + 1)] = count
    if pact_count:
        slots["pact"] = pact_count
        slots["pact_level"] = pact_level
    return slots


def _calc_ac(sheet: CharacterSheet, kb: Any, report: RecalcReport) -> int:
    """AC：护甲（轻/中/重）+ 盾牌 + 无甲防御，取最高。

    盾牌 +2 对穿甲与可持盾的无甲防御（野蛮人）均叠加；主手双手武器时
    盾牌加值不计（警告入 report）。
    """
    dex_mod = sheet.get_ability_modifier("dex")
    armor_ac: int | None = None
    armor_name = (sheet.equipment.armor or "").strip()
    if armor_name:
        item = kb.item_combat(armor_name)
        if item and item.ac and item.armor_type in ("LA", "MA", "HA"):
            at = item.armor_type
            if at == "LA":
                base = item.ac + dex_mod
            elif at == "MA":
                base = item.ac + min(dex_mod, 2)
            else:
                base = item.ac
            armor_ac = base
        elif item:
            report.warnings.append(
                f"「{armor_name}」不是护甲（类型 {item.armor_type or '无'}），AC 未计入"
            )
        else:
            report.warnings.append(f"知识库无「{armor_name}」的战斗数据，AC 未计入")

    shield_bonus = 0
    off_name = (sheet.equipment.off_hand or "").strip()
    if off_name:
        item = kb.item_combat(off_name)
        if item and item.is_shield:
            shield_bonus = 2
        elif item and not item.dmg1:
            report.warnings.append(f"「{off_name}」不是盾牌或武器，副手位未生效")

    main_name = (sheet.equipment.main_hand or "").strip()
    if shield_bonus and main_name:
        main_item = kb.item_combat(main_name)
        if main_item and main_item.is_two_handed:
            report.warnings.append(
                f"主手「{main_name}」是双手武器，副手「{off_name}」的盾牌加值不计"
            )
            shield_bonus = 0

    wearing_armor = armor_ac is not None
    candidates: list[int] = []
    if wearing_armor:
        candidates.append(armor_ac + shield_bonus)
    else:
        candidates.append(10 + dex_mod)
        # 无甲防御：仅未穿甲时适用
        for cl in sheet.classes:
            entry = UNARMORED.get(cl.class_name)
            if entry:
                src, with_shield = entry
                if isinstance(src, int):
                    val = src + dex_mod
                else:
                    val = 10 + dex_mod + sheet.get_ability_modifier(src)
                candidates.append(val + (shield_bonus if with_shield else 0))
        for cl in sheet.classes:
            base = _UNARMORED_SUBCLASS.get(cl.subclass)
            if base:
                candidates.append(base + dex_mod)
    return max(candidates)


def _calc_attacks(
    sheet: CharacterSheet, kb: Any, report: RecalcReport
) -> dict[str, int]:
    """攻击加值：装备槽武器 + 各施法职业法术攻击。

    近战 力修+熟练；灵巧取 max(力,敏)；远程/投掷 敏修；法术攻击 施法属性修+熟练。
    返回 {条目名: base}（生成集）；写回策略见 recalc_base。
    """
    prof = sheet.proficiency_bonus
    str_mod = sheet.get_ability_modifier("str")
    dex_mod = sheet.get_ability_modifier("dex")
    generated: dict[str, int] = {}

    # 装备槽武器（主/副手各一，同名去重）
    for slot_name in (sheet.equipment.main_hand, sheet.equipment.off_hand):
        name = (slot_name or "").strip()
        if not name or name in generated:
            continue
        item = kb.item_combat(name)
        if item is not None and not item.dmg1:
            continue  # 库内有记录但非武器（护甲/盾牌/杂物）→ 跳过（原行为）
        combat_src = name
        if item is None:
            # 库内无条目：魔法武器变体（如「警戒武器巨锤」「+1 巨锤」，
            # 5etools 只有「警戒武器」书条目/战利品表，具名变体是搜索索引
            # 派生的）→ 解析基础武器名（词表 / kb base_item / 词表后缀）
            # 复用其战斗属性；仍解析不出（原创武器等）→ 回退「近战力量
            # 修正」生成条目，规则细则交 DM。
            base = _resolve_base_weapon(name, kb)
            if base and base != name:
                item = kb.item_combat(base)
                combat_src = base
            if item is None or not item.dmg1:
                mod = str_mod
                prof_part = prof if _weapon_proficient(sheet, name, kb) else 0
                generated[name] = mod + prof_part
                if not prof_part:
                    report.warnings.append(f"武器「{name}」未熟练，攻击不加熟练加值")
                report.warnings.append(
                    f"武器「{name}」未在知识库识别，按近战力量修正生成攻击条目"
                )
                continue
        if item.is_finesse:
            mod = max(str_mod, dex_mod)
        elif item.is_ranged or item.is_thrown:
            mod = dex_mod
        else:
            mod = str_mod
        prof_part = prof if _weapon_proficient(sheet, name, kb) else 0
        generated[name] = mod + prof_part
        if not prof_part:
            report.warnings.append(f"武器「{name}」未熟练，攻击不加熟练加值")
        if combat_src != name:
            report.warnings.append(
                f"武器「{name}」按基础武器「{combat_src}」计算攻击属性"
            )

    # 法术攻击：每个有施法属性的职业一条「{职业}法术攻击」
    for cl in sheet.classes:
        row = kb.class_combat(cl.class_name, sheet.edition)
        spell_ability = row.spell_ability if row else ""
        if not spell_ability and cl.subclass:
            sub = kb.subclass_caster(cl.class_name, cl.subclass, sheet.edition)
            if sub:
                spell_ability = sub[1]
        if not spell_ability:
            continue
        label = f"{cl.class_name}法术攻击"
        if label in generated:
            continue
        mod = sheet.get_ability_modifier(spell_ability)
        generated[label] = mod + prof
    return generated


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def _calc_speed(sheet: CharacterSheet, kb: Any) -> int:
    """种族基础步行速度（尺/回合）；无种族/查不到 → 0（房规由 bonus 层兜底）。"""
    race = (sheet.race or "").strip()
    if not race or kb is None:
        return 0
    try:
        value = kb.race_speed(race, sheet.edition)
    except Exception:  # noqa: BLE001 — kb 查询失败不阻断重算
        return 0
    return int(value) if value else 0


def recalc_base(sheet: CharacterSheet, kb: Any) -> RecalcReport:
    """重算角色卡战斗字段 base 层（就地修改 sheet，只动 base 不动 bonus）。

    - hp_max / ac / spell_slots / attack_bonuses 按规则重算 base；
    - 每个字段的 bonus 按「生成条目名」原样保留；attack_bonuses 中
      生成集外的条目（玩家自建）整体保留不动。
    - 返回变更摘要与警告。
    """
    report = RecalcReport()

    # HP
    old_hp = sheet.hp_max.base
    sheet.hp_max.base = _calc_hp(sheet, kb, report)
    report.changes.append(_fmt_change("HP", old_hp, sheet.hp_max.base))

    # AC
    old_ac = sheet.ac.base
    sheet.ac.base = _calc_ac(sheet, kb, report)
    report.changes.append(_fmt_change("AC", old_ac, sheet.ac.base))

    # 速度（v0.23.0：种族步行速度；kb 不可用时保持原 base 不动，防误删手动值）
    old_speed = sheet.speed.base
    if kb is not None:
        new_speed = _calc_speed(sheet, kb)
        if new_speed != old_speed:
            sheet.speed.base = new_speed
            report.changes.append(_fmt_change("速度", old_speed, new_speed))

    # 先攻（v0.30.0：base = 敏捷修正；bonus 为房规额外加值，保留不动）
    old_init = sheet.initiative.base
    new_init = sheet.get_ability_modifier("dex")
    if new_init != old_init:
        sheet.initiative.base = new_init
        report.changes.append(_fmt_change("先攻", old_init, new_init))

    # 法术位：重建 dict（生成键 base 重算，键级 bonus 保留；非生成键保留）
    new_slots = _calc_spell_slots(sheet, kb, report)
    rebuilt: dict[str, Any] = {}
    for key, base in sorted(new_slots.items()):
        label = _slot_label(key)
        old_stat = sheet.spell_slots.get(key)
        rebuilt[key] = _rebuild_layered(old_stat, base)
        if old_stat is not None and old_stat.base != base:
            report.changes.append(f"{label} {old_stat.base}→{base}")
        elif old_stat is None and base:
            report.changes.append(f"{label} {base}")
    for key, old_stat in sheet.spell_slots.items():
        if key not in rebuilt and old_stat.bonus:
            # 非生成键但带房规 bonus：保留（旧条目或房规位）
            rebuilt[key] = old_stat
    sheet.spell_slots = rebuilt

    # 攻击加值：生成集内重算 base 保 bonus；生成集外整体保留
    new_attacks = _calc_attacks(sheet, kb, report)
    rebuilt_atk: dict[str, Any] = {}
    for name, base in sorted(new_attacks.items()):
        old_stat = sheet.attack_bonuses.get(name)
        rebuilt_atk[name] = _rebuild_layered(old_stat, base)
        if old_stat is None:
            report.changes.append(f"攻击「{name}」 {base}")
        elif old_stat.base != base:
            report.changes.append(f"攻击「{name}」 {old_stat.base}→{base}")
    for name, old_stat in sheet.attack_bonuses.items():
        if name not in rebuilt_atk:
            rebuilt_atk[name] = old_stat  # 玩家自建条目，整体保留
    sheet.attack_bonuses = rebuilt_atk

    return report


def _rebuild_layered(old_stat: Any, base: int):
    """生成条目：重算 base，按名保留 bonus（无旧条目则 bonus=0）。"""
    from .character import LayeredStat  # 延迟导入避免模块级循环

    if isinstance(old_stat, LayeredStat):
        return LayeredStat(base=base, bonus=old_stat.bonus)
    if isinstance(old_stat, dict):
        bonus = old_stat.get("bonus")
        return LayeredStat(base=base, bonus=int(bonus) if isinstance(bonus, int) else 0)
    return LayeredStat(base=base, bonus=0)


def _slot_label(key: str) -> str:
    """法术位键 → 人类可读标签（pact 键特判）。"""
    if key == "pact":
        return "短休法术位"
    if key == "pact_level":
        return "短休环阶"
    return f"法术位{key}环"

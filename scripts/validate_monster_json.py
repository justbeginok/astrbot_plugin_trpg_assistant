"""validate_monster_json.py — LLM 提取怪物 JSON 的规则校验器（离线脚本）。

用法：
    python scripts/validate_monster_json.py <bestiary-xxx.json> [--report 报告.md]

校验维度（对应 scripts/llm_monster_schema.md）：
- 必填字段与类型（name/ENG_name/source/edition/六属性/ac/hp/speed/cr/type）；
- 数值范围（属性 1~30、AC 1~50、passive 0~40）；
- 词表校验：伤害免疫/状态免疫词、速度键、感官前缀、阵营码、类型码、
  体型码、豁免/技能键、语言；
- cr 格式（数字 / X/Y 分数 / 0，对齐 kb_build_lib._parse_cr）；
- 特性/动作块结构（{"name","entries"}）。

校验结果分级：
- ERROR：必须修复（缺必填、类型错、值不在词表）；
- WARN：建议复核（如 immune 词不在伤害词表但在状态词表等边缘情况）。

退出码：0 = 全部通过或仅 WARN；1 = 存在 ERROR。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent.parent
# 与 build_kb.py 相同的命名空间包规则：插件包目录自身无 __init__.py，
# 需将 workspace 根（包名目录的父目录）加入 path。
_PLUGIN_ROOT = _PKG_DIR.parent
for _p in (_PLUGIN_ROOT, _PKG_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from astrbot_plugin_trpg_assistant.kb_enums import (  # noqa: E402
    CONDITION_CN,
    DAMAGE_TYPE_CN,
    MONSTER_TYPE_CN_REV,
    SENSE_TYPE_CN,
    SPEED_TYPE_CN_REV,
)

# --- 词表 ---
# 伤害类型 canonical（DAMAGE_TYPE_CN 值为英文码？确认键为中文 canonical）
_DMG_CN = set(DAMAGE_TYPE_CN.keys())
# DAMAGE_TYPE_CN 可能 中文→英文码，取键即中文 canonical 伤害名
_COND_CN = set(CONDITION_CN.keys())
_SIZE_OK = {"T", "S", "M", "L", "H", "G"}
_TYPE_OK = set(MONSTER_TYPE_CN_REV.keys())
_ALIGN_OK = {"L", "N", "C", "G", "E", "U", "A", "NX", "NY"}
_ABIL_KEYS = {"str", "dex", "con", "int", "wis", "cha"}
_SAVE_KEYS = _ABIL_KEYS
_SKILL_KEYS = {
    "acrobatics", "animalHandling", "arcana", "athletics", "deception",
    "history", "insight", "intimidation", "investigation", "medicine",
    "nature", "perception", "performance", "persuasion", "religion",
    "sleightOfHand", "stealth", "survival",
}
_SPEED_KEYS = set(SPEED_TYPE_CN_REV.keys())
_BLOCK_KEYS = ("trait", "action", "bonus", "reaction", "legendary", "mythic")
_RE_CR = re.compile(r"^(?:\d+|\d+\s*/\s*\d+|0)$")


class _Issues:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warns: list[str] = []


def _check_block(name: str, blocks: object, issues: _Issues, where: str) -> None:
    if blocks is None:
        return
    if not isinstance(blocks, list):
        issues.errors.append(f"{where}: {name} 应为数组")
        return
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            issues.errors.append(f"{where}: {name}[{i}] 应为对象")
            continue
        bname = b.get("name")
        if not isinstance(bname, str) or not bname.strip():
            issues.errors.append(f"{where}: {name}[{i}] 缺 name（标题）")
        entries = b.get("entries")
        if entries is not None and not isinstance(entries, list):
            issues.errors.append(f"{where}: {name}[{i}] entries 应为数组")


def validate_monster(m: dict, issues: _Issues, where: str) -> None:
    # --- 必填字符串 ---
    for f in ("name", "ENG_name", "source", "edition"):
        v = m.get(f)
        if not isinstance(v, str) or not v.strip():
            issues.errors.append(f"{where}: 缺必填字符串 {f}")
    if m.get("source") != "火炬光下的克苏鲁":
        issues.warns.append(f"{where}: source 非「火炬光下的克苏鲁」: {m.get('source')!r}")
    if m.get("edition") not in ("2024",):
        issues.warns.append(f"{where}: edition 非 2024: {m.get('edition')!r}")

    # --- 属性 ---
    for k in ("str", "dex", "con", "int", "wis", "cha"):
        v = m.get(k)
        if not isinstance(v, int):
            issues.errors.append(f"{where}: 属性 {k} 应为整数，得 {v!r}")
        elif not 1 <= v <= 30:
            issues.errors.append(f"{where}: 属性 {k}={v} 超出 1~30")

    # --- ac / hp / speed / passive / cr ---
    ac = m.get("ac")
    if isinstance(ac, list):
        ac = ac[0].get("ac") if ac and isinstance(ac[0], dict) else None
    elif isinstance(ac, dict):
        ac = ac.get("ac")
    if not isinstance(ac, int) or not 1 <= ac <= 50:
        issues.errors.append(f"{where}: ac 应为 1~50 整数，得 {m.get('ac')!r}")
    hp = m.get("hp")
    if isinstance(hp, dict):
        if not isinstance(hp.get("average"), int):
            issues.errors.append(f"{where}: hp.average 应为整数")
        if not isinstance(hp.get("formula"), str) or not hp.get("formula"):
            issues.warns.append(f"{where}: hp.formula 缺失或非字符串")
    elif not isinstance(hp, int):
        issues.errors.append(f"{where}: hp 应为 dict 或 int，得 {hp!r}")

    spd = m.get("speed")
    if isinstance(spd, int):
        pass
    elif isinstance(spd, dict):
        for k in spd:
            if k not in _SPEED_KEYS:
                issues.warns.append(f"{where}: speed 键 {k!r} 非标准（{_SPEED_KEYS}）")
    else:
        issues.errors.append(f"{where}: speed 应为 dict 或 int，得 {spd!r}")

    passive = m.get("passive")
    if passive is not None and (not isinstance(passive, int) or not 0 <= passive <= 40):
        issues.errors.append(f"{where}: passive 应为 0~40 整数，得 {passive!r}")

    cr = m.get("cr")
    if cr is None:
        issues.errors.append(f"{where}: 缺 cr")
    else:
        crv = cr.get("cr") if isinstance(cr, dict) else cr
        if not _RE_CR.match(str(crv).strip()):
            issues.errors.append(f"{where}: cr 格式非法: {crv!r}")

    # --- size / type / alignment ---
    sz = m.get("size")
    if isinstance(sz, list):
        sz = sz[0] if sz else None
    if sz not in _SIZE_OK:
        issues.errors.append(f"{where}: size 非法: {m.get('size')!r}")
    t = m.get("type")
    tcode = t.get("type") if isinstance(t, dict) else t
    if tcode not in _TYPE_OK:
        issues.errors.append(f"{where}: type 非法: {tcode!r}")
    if isinstance(t, dict) and "tags" in t and not isinstance(t["tags"], list):
        issues.errors.append(f"{where}: type.tags 应为数组")
    align = m.get("alignment")
    if isinstance(align, str):
        align = [align]
    if not isinstance(align, list) or not align:
        issues.errors.append(f"{where}: alignment 缺失或为空")
    else:
        for c in align:
            if c not in _ALIGN_OK:
                issues.errors.append(f"{where}: 阵营码非法: {c!r}")

    # --- 防御/状态词表 ---
    for facet, key in (("immune", "immune"), ("resist", "resist"),
                       ("vulnerable", "vulnerable")):
        for v in _list_of(m.get(key)):
            if v in _COND_CN and facet == "immune":
                issues.warns.append(f"{where}: immune 含状态词 {v!r}（应入 conditionImmune？）")
            elif v not in _DMG_CN:
                issues.errors.append(f"{where}: {facet} 词不在伤害词表: {v!r}")
    for v in _list_of(m.get("conditionImmune")):
        if v not in _COND_CN:
            issues.errors.append(f"{where}: conditionImmune 词不在状态词表: {v!r}")

    # --- senses 前缀 ---
    for s in _list_of(m.get("senses")):
        if not isinstance(s, str) or not s.strip():
            issues.errors.append(f"{where}: senses 项非字符串: {s!r}")
            continue
        if not any(s.startswith(p) for p in SENSE_TYPE_CN):
            issues.warns.append(f"{where}: senses 无已知前缀: {s!r}")

    # --- save / skill 键 ---
    for key, ok in (("save", _SAVE_KEYS), ("skill", _SKILL_KEYS)):
        d = m.get(key)
        if d is None:
            continue
        if not isinstance(d, dict):
            issues.errors.append(f"{where}: {key} 应为 dict")
            continue
        for k in d:
            if k not in ok:
                issues.errors.append(f"{where}: {key} 键非法: {k!r}")

    # --- 特性/动作块 ---
    for k in _BLOCK_KEYS:
        _check_block(k, m.get(k), issues, where)

    # --- 施法块 ---
    sc = m.get("spellcasting")
    if sc is not None:
        if not isinstance(sc, list):
            issues.errors.append(f"{where}: spellcasting 应为数组")
        else:
            for i, s in enumerate(sc):
                if not isinstance(s, dict):
                    issues.errors.append(f"{where}: spellcasting[{i}] 应为对象")
                    continue
                ab = s.get("ability")
                if ab is not None and ab not in ("str", "dex", "con", "int", "wis", "cha"):
                    issues.errors.append(f"{where}: spellcasting[{i}] ability 非法: {ab!r}")
                for f in ("will",):
                    if s.get(f) is not None and not isinstance(s[f], list):
                        issues.errors.append(f"{where}: spellcasting[{i}] {f} 应为数组")
                dly = s.get("daily")
                if dly is not None and not isinstance(dly, dict):
                    issues.errors.append(f"{where}: spellcasting[{i}] daily 应为 dict")

    # --- 语言（自由文本，仅类型检查） ---
    for v in _list_of(m.get("languages")):
        if not isinstance(v, str):
            issues.errors.append(f"{where}: languages 项非字符串")


def _list_of(v: object) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return v
    return [v]


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 怪物 JSON 规则校验器")
    ap.add_argument("json_path", help="bestiary-xxx.json 路径")
    ap.add_argument("--report", help="可选：写报告 markdown 路径")
    args = ap.parse_args()

    data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    monsters = data.get("monster")
    if not isinstance(monsters, list):
        print(f"ERROR: 顶层缺 monster 数组（共 {len(monsters) if monsters is not None else 0} 项）")
        return 1

    all_issues: list[tuple[str, _Issues]] = []
    for m in monsters:
        where = f"[{m.get('name') or '?'}]"
        issues = _Issues()
        validate_monster(m, issues, where)
        all_issues.append((where, issues))

    n_err = sum(len(i.errors) for _, i in all_issues)
    n_warn = sum(len(i.warns) for _, i in all_issues)

    lines = [
        f"# 怪物 JSON 校验报告",
        "",
        f"- 条目数：{len(monsters)}",
        f"- ERROR：{n_err}，WARN：{n_warn}",
        "",
    ]
    for where, issues in all_issues:
        if not issues.errors and not issues.warns:
            lines.append(f"## {where} ✅ 通过")
            continue
        lines.append(f"## {where}")
        for e in issues.errors:
            lines.append(f"- ❌ {e}")
        for w in issues.warns:
            lines.append(f"- ⚠️ {w}")

    report = "\n".join(lines)
    print(report)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main())

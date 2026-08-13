"""align.py — 数值自动对齐：把 LLM 提取的怪物数值字段对齐到 5etools-cn。

5e_chm 源（不全书）正文质量高，但数值偶有 OCR/笔误（如 CHA 5 应为 1）。
5etools-cn 数值来自英文原版机器可读数据，更可靠。本脚本对「按名字匹配到
5etools-cn 同 source 怪物」的条目，把数值字段覆盖为 5etools-cn 值，正文
（trait/action/entries/名称/类型/标签）保留 5e_chm 人工翻译。

用法：
    python scripts/monster_extract/align.py <bestiary-xxx.json> <source码> \
        [--inplace] [--report 对齐报告.md]

覆盖字段：ac、hp(average/formula)、str/dex/con/int/wis/cha、cr、save、skill、passive。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 与 build_kb.py 相同的命名空间包规则：把 workspace 根加入 path 以导入 kb_enums。
_PKG_DIR = Path(__file__).resolve().parent.parent.parent
for _p in (_PKG_DIR.parent, _PKG_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from astrbot_plugin_trpg_assistant.kb_enums import DAMAGE_TYPE_CN  # noqa: E402

_CN_DATA = Path("C:/Users/75957/WorkBuddy/5etools-cn-data/data/bestiary")

# 覆盖的数值字段（键 -> 是否 dict）
_NUM_FIELDS = ("str", "dex", "con", "int", "wis", "cha", "passive")
_DICT_FIELDS = ("save", "skill")


def _load_cn(source: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(_CN_DATA.glob("bestiary-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in d.get("monster", []):
            if m.get("source") == source and m.get("name"):
                out[m["name"]] = m
    return out


def _norm_cr(v):
    if isinstance(v, dict):
        v = v.get("cr")
    return str(v).strip() if v is not None else None


def _norm_formula(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _strip_paren(s: str) -> str:
    """去掉状态/伤害名里的括号注释：『魅惑（对其吸血鬼尊长无效）』→『魅惑』。"""
    return re.sub(r"[（(].*?[)）]", "", s or "").strip()


def _clean_conditions(monster: dict, changes: list[str]) -> None:
    """conditionImmune/immune/resist 值剥离括号注释，使 /筛怪 能按 canonical 名命中。"""
    for key in ("conditionImmune",):
        vals = monster.get(key)
        if not isinstance(vals, list):
            continue
        out = []
        for v in vals:
            if isinstance(v, str) and _strip_paren(v) != v:
                changes.append(f"{monster.get('name')}: {key} {v!r} -> {_strip_paren(v)!r}")
                out.append(_strip_paren(v))
            else:
                out.append(v)
        monster[key] = out


_BLOCK_DEFAULT_NAME = {
    "trait": "特性", "action": "动作", "bonus": "附赠动作",
    "reaction": "反应", "legendary": "传奇动作", "mythic": "神话动作",
}


_DMG_CN = set(DAMAGE_TYPE_CN.keys())

_SENSE_VARIANT = {"盲感": "盲视", "真实视野": "真实视觉"}


def _norm_speed(spd) -> dict | int | None:
    """5etools-cn speed → 我方简化格式（int 或 {number, condition}，丢 canHover）。"""
    if isinstance(spd, (int, float)):
        return spd
    if not isinstance(spd, dict):
        return None
    out = {}
    for k, v in spd.items():
        if k == "canHover":
            continue
        if isinstance(v, dict):
            if v.get("number") is not None:
                out[k] = {"number": v["number"], "condition": v.get("condition")}
        elif isinstance(v, (int, float)):
            out[k] = v
    return out


def _clean_senses(monster: dict, changes: list[str]) -> None:
    """感官前缀归一：盲感→盲视、真实视野→真实视觉。"""
    vals = monster.get("senses")
    if not isinstance(vals, list):
        return
    out = []
    for v in vals:
        if isinstance(v, str):
            for k, rep in _SENSE_VARIANT.items():
                if v.startswith(k):
                    changes.append(f"{monster.get('name')}: senses {v!r} -> {rep + v[len(k):]!r}")
                    v = rep + v[len(k):]
                    break
        out.append(v)
    monster["senses"] = out


def _clean_damage_special(monster: dict, changes: list[str]) -> None:
    """resist/immune/vulnerable 里的非伤害词表字符串（如「法术伤害」）→ {"special": ...}。"""
    for key in ("immune", "resist", "vulnerable"):
        vals = monster.get(key)
        if not isinstance(vals, list):
            continue
        out = []
        for v in vals:
            if isinstance(v, str) and v not in _DMG_CN and _strip_paren(v) not in _DMG_CN:
                changes.append(f"{monster.get('name')}: {key} 特殊词 {v!r} -> special")
                out.append({"special": v})
            else:
                out.append(v)
        monster[key] = out


def _clean_blocks(monster: dict, changes: list[str]) -> None:
    """补齐空 name 的块条目（如传奇动作首条「传奇动作次数」说明）。"""
    for blk, default in _BLOCK_DEFAULT_NAME.items():
        items = monster.get(blk)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and not str(it.get("name") or "").strip():
                it["name"] = default
                changes.append(f"{monster.get('name')}: {blk} 空 name -> {default}")


def align(monster: dict, ref: dict, changes: list[str]) -> None:
    name = monster.get("name", "?")
    # ac
    rac = ref.get("ac")
    if isinstance(rac, list):
        rac = rac[0].get("ac") if rac and isinstance(rac[0], dict) else (rac[0] if rac else None)
    elif isinstance(rac, dict):
        rac = rac.get("ac")
    if isinstance(rac, int) and monster.get("ac") != rac:
        changes.append(f"{name}: ac {monster.get('ac')} -> {rac}")
        monster["ac"] = rac

    # hp
    rhp = ref.get("hp")
    if isinstance(rhp, dict) and "average" in rhp:
        if monster.get("hp"):
            if isinstance(monster["hp"], dict):
                if monster["hp"].get("average") != rhp["average"]:
                    changes.append(f"{name}: hp {monster['hp'].get('average')} -> {rhp['average']}")
                monster["hp"]["average"] = rhp["average"]
                monster["hp"]["formula"] = _norm_formula(rhp.get("formula") or "")
        else:
            monster["hp"] = {"average": rhp["average"],
                             "formula": _norm_formula(rhp.get("formula") or "")}

    # 六属性 + passive
    for k in _NUM_FIELDS:
        rv = ref.get(k)
        if isinstance(rv, int) and isinstance(monster.get(k), int) and monster[k] != rv:
            changes.append(f"{name}: {k} {monster[k]} -> {rv}")
            monster[k] = rv

    # save / skill（对齐键值）
    for k in _DICT_FIELDS:
        rv = ref.get(k)
        if isinstance(rv, dict):
            cur = monster.get(k)
            if not isinstance(cur, dict):
                cur = {}
            if cur != rv:
                changes.append(f"{name}: {k} {cur} -> {rv}")
            monster[k] = rv

    # cr
    rcr = _norm_cr(ref.get("cr"))
    cur_cr = monster.get("cr")
    cur_cr = cur_cr.get("cr") if isinstance(cur_cr, dict) else cur_cr
    cur_cr = str(cur_cr).strip() if cur_cr is not None else None
    if rcr is not None and cur_cr != rcr:
        changes.append(f"{name}: cr {cur_cr} -> {rcr}")
        monster["cr"] = rcr

    # speed（缺则补 5etools-cn）
    if not monster.get("speed") and ref.get("speed") is not None:
        ns = _norm_speed(ref.get("speed"))
        if ns:
            changes.append(f"{name}: speed 补 {ns}")
            monster["speed"] = ns

    # alignment（缺则补 5etools-cn）
    if not monster.get("alignment") and ref.get("alignment"):
        changes.append(f"{name}: alignment 补 {ref['alignment']}")
        monster["alignment"] = ref["alignment"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("source")
    ap.add_argument("--inplace", action="store_true", help="覆盖原文件；否则写 .aligned.json")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    p = Path(args.json_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    monsters = data.get("monster", [])
    cn = _load_cn(args.source)

    changes: list[str] = []
    matched = 0
    for m in monsters:
        _clean_conditions(m, changes)
        _clean_damage_special(m, changes)
        _clean_senses(m, changes)
        _clean_blocks(m, changes)
        ref = cn.get(m.get("name"))
        if not ref:
            continue
        matched += 1
        align(m, ref, changes)

    report = [
        f"# 数值对齐报告 {p.name}（source={args.source}）",
        f"- 条目 {len(monsters)}，匹配 5etools-cn {matched} 条",
        f"- 数值修正 {len(changes)} 处",
    ]
    for c in changes:
        report.append(f"  - {c}")
    print("\n".join(report))
    if args.report:
        Path(args.report).write_text("\n".join(report), encoding="utf-8")

    out_path = p if args.inplace else p.with_name(p.stem + ".aligned.json")
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

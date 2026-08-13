"""reconcile.py — 怪物提取对账器（三层验收的第 1/3 层：全量对账表）。

用法：
    python scripts/monster_extract/reconcile.py <bestiary-xxx.json> \
        [--md <书目录相对5e_chm/md>] [--src <5etools source码>] [--out 报告.md]

输出三部分：
1. 条目数 + 必填字段通过率（轻校验）；
2. 与 md 目录对账：md 文件数 vs 产怪数、疑似漏怪/多产；
3. 与 5etools-cn 对账：按名字匹配，比 AC/HP/六属性/CR 数值，标记不一致；
   并输出「名称重叠率」用于验证 source 映射是否正确。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_MD_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "5e_chm" / "md"
_CN_DATA = Path("C:/Users/75957/WorkBuddy/5etools-cn-data/data/bestiary")


def _load_cn_monsters(source: str) -> dict[str, dict]:
    """按 source 码加载 5etools-cn 怪物（name -> 条目）。"""
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


def _num(m: dict, key: str):
    v = m.get(key)
    if isinstance(v, (int, float)):
        return v
    return None


def _cn_abil(m: dict) -> dict:
    return {k: _num(m, k) for k in ("str", "dex", "con", "int", "wis", "cha")}


def _cn_ac(m: dict):
    v = m.get("ac")
    if isinstance(v, int):
        return v
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return v[0].get("ac")
    if isinstance(v, dict):
        return v.get("ac")
    return None


def _cn_hp(m: dict):
    v = m.get("hp")
    if isinstance(v, dict):
        return v.get("average")
    if isinstance(v, int):
        return v
    return None


def _cn_cr(m: dict):
    v = m.get("cr")
    if isinstance(v, dict):
        v = v.get("cr")
    return str(v).strip() if v is not None else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--md", default=None, help="书目录（相对 5e_chm/md，如 模组/哈欠门）")
    ap.add_argument("--src", default=None, help="5etools source 码（用于数值对账）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    monsters = data.get("monster", [])
    lines: list[str] = [
        f"# 对账报告 {Path(args.json_path).name}",
        "",
        f"- 产怪数：{len(monsters)}",
    ]

    # 1. 轻校验（必填字段）
    missing = []
    for m in monsters:
        for f in ("name", "ENG_name", "source", "edition", "size", "type", "alignment", "cr"):
            if not m.get(f):
                missing.append(f"{m.get('name') or '?'}:缺{f}")
    lines.append(f"- 缺必填字段：{len(missing)}")
    for x in missing[:20]:
        lines.append(f"  - {x}")

    # 2. 与 md 对账
    if args.md:
        md_dir = _MD_ROOT / args.md
        md_files = [p.name for p in sorted(md_dir.rglob("*.md"))] if md_dir.is_dir() else []
        produced_names = {m.get("name") for m in monsters}
        lines.append("")
        lines.append(f"## 与 md 对账（{args.md}）")
        lines.append(f"- md 文件数：{len(md_files)}，产怪数：{len(monsters)}")
        # 用文件名中文词根近似判断漏怪
        stem_to_m = {}
        for m in monsters:
            stem_to_m.setdefault(m.get("name"), m)
        unmatched = []
        for fn in md_files:
            stem = re.match(r"^([\u4e00-\u9fff]+)", fn)
            s = stem.group(1) if stem else fn
            if s not in produced_names and not any(s in n or n in s for n in produced_names):
                unmatched.append(fn)
        lines.append(f"- 疑似漏怪文件：{len(unmatched)}")
        for x in unmatched[:30]:
            lines.append(f"  - {x}")

    # 3. 与 5etools-cn 数值对账
    if args.src:
        cn = _load_cn_monsters(args.src)
        lines.append("")
        lines.append(f"## 与 5etools-cn 对账（source={args.src}，共 {len(cn)} 条）")
        hit = 0
        mismatch = []
        for m in monsters:
            ref = cn.get(m.get("name"))
            if not ref:
                continue
            hit += 1
            diffs = []
            for k, label in (("ac", "AC"), ("hp", "HP"), ("cr", "CR")):
                a = _cn_ac(m) if k == "ac" else (_cn_hp(m) if k == "hp" else _cn_cr(m))
                b = _cn_ac(ref) if k == "ac" else (_cn_hp(ref) if k == "hp" else _cn_cr(ref))
                if a is not None and b is not None and str(a) != str(b):
                    diffs.append(f"{label}:产{a}≠ref{b}")
            for k, label in (("str", "力量"), ("dex", "敏捷"), ("con", "体质"),
                             ("int", "智力"), ("wis", "感知"), ("cha", "魅力")):
                a, b = _num(m, k), _num(ref, k)
                if a is not None and b is not None and a != b:
                    diffs.append(f"{label}:{a}≠{b}")
            if diffs:
                mismatch.append((m.get("name"), diffs))
        overlap = hit / len(monsters) if monsters else 0
        lines.append(f"- 名称命中：{hit}/{len(monsters)}（重叠率 {overlap:.0%}）")
        lines.append(f"- 数值不一致：{len(mismatch)}")
        for name, diffs in mismatch[:40]:
            lines.append(f"  - {name}: {'; '.join(diffs)}")
        if overlap < 0.5:
            lines.append(f"- ⚠️ 重叠率偏低，source 映射（{args.src}）可能错误，需人工复核！")

    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

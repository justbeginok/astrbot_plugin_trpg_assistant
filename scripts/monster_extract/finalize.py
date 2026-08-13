"""finalize.py — 合并分块 + 清洗对齐 + 按名合并进 5etools-cn 数据目录。

流程：
1. 按 slug 合并 out/bestiary-<slug>--*.json 分块，按 name 去重；
2. 清洗（状态括号/特殊抗性/感官归一/空 name 补齐）+ 数值对齐 5etools-cn；
3. 官方（ASCII source）按「名字」与 5etools-cn 合并：5e_chm 覆盖同名家，
   保留 5etools-cn 独有家（5e_chm 不全的书不丢怪）；
   第三方（中文 source）直接新增 bestiary-<slug>.json；
4. 写回 5etools-cn 的 data/bestiary/（写前备份）。

用法：
    python scripts/monster_extract/finalize.py [--no-backup]
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_OUT = _HERE / "out"
_CN = Path("C:/Users/75957/WorkBuddy/5etools-cn-data/data/bestiary")

sys.path.insert(0, str(_HERE))
from align import (  # noqa: E402
    align,
    _clean_blocks,
    _clean_conditions,
    _clean_damage_special,
    _clean_senses,
    _load_cn,
)

# 试点已入库、不重复处理的书（slug → 已存在的 5etools-cn 文件名）
_SKIP_SLUGS = {"tp17"}  # 火炬光下的克苏鲁 → bestiary-thc.json（试点已并入）


def _is_ascii(s: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9\-]+", s or ""))


def merge_chunks(slug: str) -> list[dict]:
    """合并某 slug 的所有分块，按 name 去重（保留首见，重复记录日志）。"""
    files = sorted(_OUT.glob(f"bestiary-{slug}--*.json"))
    merged: dict[str, dict] = {}
    dupes: list[str] = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  !! {f.name} 解析失败: {e}")
            continue
        for m in d.get("monster", []):
            name = m.get("name")
            if not name:
                continue
            if name in merged:
                dupes.append(name)
            else:
                merged[name] = m
    if dupes:
        print(f"  [去重 {slug}] {len(dupes)} 处重名（保留首见）: {', '.join(dupes[:8])}")
    return list(merged.values())


def finalize_one(slug: str, source: str) -> tuple[list[dict], int, int]:
    monsters = merge_chunks(slug)
    cn = _load_cn(source) if _is_ascii(source) else {}
    changes: list[str] = []
    matched = 0
    for m in monsters:
        _clean_conditions(m, changes)
        _clean_damage_special(m, changes)
        _clean_senses(m, changes)
        _clean_blocks(m, changes)
        ref = cn.get(m.get("name"))
        if ref:
            matched += 1
            align(m, ref, changes)
    return monsters, matched, len(changes)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    slug_map = json.loads((_HERE / "slug_map.json").read_text(encoding="utf-8"))

    # 备份 5etools-cn bestiary 目录
    if not args.no_backup and _CN.is_dir():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = _CN.parent / f"bestiary_backup_{stamp}"
        shutil.copytree(_CN, bak)
        print(f"已备份 5etools-cn bestiary → {bak}")

    report: list[str] = ["# 怪物全量重建 finalize 报告", ""]
    total_merged = 0
    for slug, source in sorted(slug_map.items()):
        if slug in _SKIP_SLUGS:
            report.append(f"- {slug}（{source}）: 试点已入库，跳过")
            continue
        monsters, matched, n_chg = finalize_one(slug, source)
        # 官方：按名与 5etools-cn 合并；第三方：直接写
        if _is_ascii(source):
            cn_file = _CN / f"bestiary-{slug.lower()}.json"
            cn_extra = 0
            if cn_file.exists():
                try:
                    cn_data = json.loads(cn_file.read_text(encoding="utf-8"))
                    cn_mons = cn_data.get("monster", [])
                    my_names = {m.get("name") for m in monsters}
                    extra = [m for m in cn_mons if m.get("name") not in my_names]
                    cn_extra = len(extra)
                    monsters = monsters + extra
                except Exception as e:
                    print(f"  !! {cn_file.name} 读取失败: {e}")
            report.append(
                f"- {slug}（{source}）: 5e_chm {len(monsters) - cn_extra} 条 + "
                f"5etools-cn 独有 {cn_extra} 条 = {len(monsters)} 条；对齐修正 {n_chg} 处"
            )
        else:
            report.append(
                f"- {slug}（{source}）: 5e_chm {len(monsters)} 条（第三方新增）；对齐修正 {n_chg} 处"
            )
        # 写回 5etools-cn
        fname = f"bestiary-{slug.lower()}.json"
        (_CN / fname).write_text(
            json.dumps({"monster": monsters}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        total_merged += len(monsters)

    report.append(f"\n- 合并后总怪物数：{total_merged}")
    text = "\n".join(report)
    (_HERE / "finalize_report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

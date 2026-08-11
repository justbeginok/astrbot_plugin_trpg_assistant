# -*- coding: utf-8 -*-
"""audit_chm.py — chm_parser 产物对账工具（「估摸工程量」的第一份量化交付）。

用法：
    python audit_chm.py --spells scripts/_md_cache/spells_chm.json \
        --enrich kb_patches/spell_enrich.json [--out scripts/_md_cache/audit_report.txt]

输出：
1. 总数/官方/第三方/版本分布
2. 各来源详述 join 覆盖率（有详述/无详述）
3. 未映射来源清单（source_5e 以 T: 开头 → 需要补第三方来源码）
4. 无详述法术清单
5. 无 enrich 法术清单（按 (name, source_5e, edition) 与现有 spell_enrich.json 对账）——
   即 B' 阶段需要补 summary+keywords 的缺口清单（同时写 JSON 供消费）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="5e_chm 法术产物对账")
    ap.add_argument("--spells", required=True, help="chm_parser 产物 spells_chm.json")
    ap.add_argument("--enrich", required=True, help="现有 kb_patches/spell_enrich.json")
    ap.add_argument("--out", default="", help="报告输出路径（默认 stdout）")
    args = ap.parse_args()

    spells = load_json(Path(args.spells))
    enrich = load_json(Path(args.enrich))
    enrich_keys = {(e.get("name"), e.get("source"), e.get("edition")) for e in enrich}

    lines: list[str] = []

    def out(msg: str = "") -> None:
        lines.append(msg)

    out("=" * 60)
    out("5e_chm 法术数据源对账报告")
    out("=" * 60)

    # 1. 基础分布
    official = [s for s in spells if s["edition"] != "第三方"]
    third = [s for s in spells if s["edition"] == "第三方"]
    out(f"法术总数: {len(spells)} (官方 {len(official)} + 第三方 {len(third)})")
    out(f"版本分布: {dict(Counter(s['edition'] for s in spells))}")
    out(f"来源分布: {dict(sorted(Counter(s['source'] for s in spells).items()))}")

    # 2. 详述 join 覆盖率
    out("")
    out("[详述 join] 按来源")
    by_src: dict[str, list[dict]] = {}
    for s in spells:
        by_src.setdefault(s["source"], []).append(s)
    total_no_detail = 0
    for src, recs in sorted(by_src.items()):
        nd = sum(1 for r in recs if not r["has_detail"])
        total_no_detail += nd
        flag = "  ⚠️" if nd else ""
        out(f"  {src:<8} {len(recs):>4} 条 | 无详述 {nd}{flag}")
    out(f"无详述合计: {total_no_detail}")
    if total_no_detail:
        out("  清单: " + ", ".join(f"{s['name']}({s['source']})" for s in spells if not s["has_detail"]))

    # 3. 未映射来源（source_5e 以 T: 开头）
    out("")
    unmapped = sorted({s["source"] for s in spells if s["source_5e"].startswith("T:")})
    if unmapped:
        out(f"[来源码] 未映射 {len(unmapped)} 个（需补 THIRD_PARTY_SOURCE_MAP）: {unmapped}")
    else:
        out("[来源码] 全部来源已映射")

    # 4. 无 enrich 法术清单（B' 阶段输入）
    out("")
    missing_enrich: list[dict] = []
    for s in spells:
        key = (s["name"], s["source_5e"], s["edition"])
        if key not in enrich_keys:
            missing_enrich.append(s)
    out(f"[富化缺口] 无 summary/keywords 的法术: {len(missing_enrich)} / {len(spells)}")
    by_edition = Counter(s["edition"] for s in missing_enrich)
    out(f"  缺口版本分布: {dict(by_edition)}")
    by_src2 = Counter(s["source"] for s in missing_enrich)
    out(f"  缺口来源分布: {dict(sorted(by_src2.items()))}")
    has_detail_ratio = sum(1 for s in missing_enrich if s["has_detail"]) / max(len(missing_enrich), 1)
    out(f"  缺口中有详述可生成: {sum(1 for s in missing_enrich if s['has_detail'])} "
        f"({has_detail_ratio:.0%})")

    # 5. 数据质量快速检查
    out("")
    no_cls = [s["name"] for s in spells if not s["classes"]]
    out(f"[质量] classes 空: {len(no_cls)} {no_cls[:12]}")
    bad_lvl = [s["name"] for s in spells if s["level"] == -1]
    out(f"[质量] level 未知: {len(bad_lvl)} {bad_lvl[:12]}")
    bad_eng = [s["name"] for s in spells if not s["eng_name"]]
    out(f"[质量] 英文名缺失: {len(bad_eng)} {bad_eng[:12]}")
    no_comps = [s["name"] for s in spells if not any(s["components"].values())]
    out(f"[质量] 无任何成分: {len(no_comps)} {no_comps[:12]}")

    report = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        # 缺口清单 JSON（B' 阶段消费）
        miss_path = Path(args.out).with_suffix(".missing_enrich.json")
        miss_path.write_text(
            json.dumps([
                {k: s[k] for k in ("name", "eng_name", "source", "source_5e", "edition",
                                   "level", "school", "classes", "has_detail", "detail_source")}
                for s in missing_enrich
            ], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(report)
        print(f"\n[audit_chm] 缺口清单 → {miss_path}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

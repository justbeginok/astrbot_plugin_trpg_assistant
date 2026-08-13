"""run_extract.py — 职业数据重建入口。

用法：
  python scripts/class_extract/run_extract.py
    --md C:/Users/75957/WorkBuddy/可爱骰娘/5e_chm/md
    --cn C:/Users/75957/WorkBuddy/5etools-cn-data/data/class
    --out <临时目录，合并产物>
    [--emit-only]  只跑 5e_chm 提取（emit），不合并
    [--dry-run]    只跑 emit 并打印对账，不写合并产物

流程：emit（5e_chm → chm class-*.json）→ finalize（与 5etools-cn 合并）→
对账报告打印。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from class_extract.emit import emit_all, write_all  # noqa: E402
from class_extract.finalize import finalize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="职业数据从 5e_chm 重建")
    ap.add_argument("--md", required=True, help="5e_chm/md 目录")
    ap.add_argument("--cn", required=True, help="5etools-cn data/class 目录")
    ap.add_argument("--out", default="", help="合并产物输出目录（默认临时目录）")
    ap.add_argument("--chm-dir", default="", help="chm emit 产物目录（固定目录时 LLM 兜底可合并进去）")
    ap.add_argument("--emit-only", action="store_true", help="只提取，不合并")
    ap.add_argument("--no-emit", action="store_true", help="跳过 emit（用已有 chm 产物，LLM 合并后重跑 finalize 用）")
    ap.add_argument("--dry-run", action="store_true", help="只打印报告，不写产物")
    args = ap.parse_args()

    md_root = Path(args.md)
    cn_dir = Path(args.cn)

    if not args.no_emit:
        print("== 1/2 5e_chm 提取 ==")
        merged = emit_all(md_root)
        total_cf = sum(len(v["classFeature"]) for v in merged.values())
        total_sf = sum(len(v["subclassFeature"]) for v in merged.values())
        total_sub = sum(len(v["subclass"]) for v in merged.values())
        print(f"chm 提取: {len(merged)} 职业文件, classFeature={total_cf}, "
              f"subclassFeature={total_sf}, subclass={total_sub}")
    else:
        print("== 1/2 跳过 emit（复用已有 chm 产物）==")

    if args.emit_only:
        chm_dir = Path(args.chm_dir) if args.chm_dir else Path("scripts/class_extract/out/chm")
        write_all(merged, chm_dir)
        print(f"(emit-only) 产物写入: {chm_dir}")
        return

    chm_dir = Path(args.chm_dir) if args.chm_dir else Path("scripts/class_extract/out/chm")
    if not args.no_emit:
        write_all(merged, chm_dir)
    if args.dry_run:
        out_dir = Path("scripts/class_extract/out/merged_dry")
    else:
        out_dir = Path(args.out) if args.out else Path("scripts/class_extract/out/merged")
    print("\n== 2/2 与 5etools-cn 合并 ==")
    report = finalize(chm_dir, cn_dir, out_dir)
    tot = {"chm_cover": 0, "chm_new": 0, "cn_keep": 0, "third_party": 0}
    print(f"{'职业':<12} {'chm覆盖':>6} {'chm新增':>6} {'cn保留':>6} {'第三方':>6}")
    for cls, r in sorted(report.items()):
        print(f"{cls:<12} {r['chm_cover']:>6} {r['chm_new']:>6} "
              f"{r['cn_keep']:>6} {r['third_party']:>6}")
        for k in tot:
            tot[k] += r[k]
    print("-" * 46)
    print(f"{'合计':<12} {tot['chm_cover']:>6} {tot['chm_new']:>6} "
          f"{tot['cn_keep']:>6} {tot['third_party']:>6}")
    if not args.dry_run:
        print(f"\n合并产物: {out_dir}")


if __name__ == "__main__":
    main()

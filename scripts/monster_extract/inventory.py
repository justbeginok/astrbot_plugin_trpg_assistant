"""inventory.py — 扫描 5e_chm/md，产出怪物提取清单（inventory.json）。

用法：
    python scripts/monster_extract/inventory.py [--out inventory.json]

产物结构（每本书一项）：
    {
      "book": "怪物图鉴2025",
      "kind": "official" | "module" | "third_party" | "dndbeyond" | "other",
      "source": "XMM",          # source 码或中文书名；null=跳过
      "edition": "2024",
      "format": "2024" | "2014" | "mtg",
      "n_files": 597,           # 总 md 文件数
      "n_stat": 508,            # 含统计块的文件数
      "files": ["相对路径", ...] # 仅统计块文件
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_MD_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "5e_chm" / "md"

_SKIP_TOP = {"template", "template2", "空白页模板"}
_PARENT_DIRS = {"模组", "第三方", "DNDBeyond", "其他"}

# 怪物统计块特征：几乎必有「生命值/HP」行（种族/魔法物品无此键值行）。
# CR-0 怪可能缺「挑战等级」行，故只以 HP 判定，避免漏怪。
_RE_STAT = re.compile(r"生命值|HP\s*[:：]\s*\d|HP\s+\d")
_RE_2024 = re.compile(r"######\s|<table>")
_RE_MTG = re.compile(r"—{8,}|_{8,}")


def has_stat_block(text: str) -> bool:
    return bool(_RE_STAT.search(text))


def classify_format(files: list[Path]) -> str:
    """按样本判定格式族：2024 / 2014 / mtg（仅作提示，LLM 按文件自辨）。"""
    votes = {"2024": 0, "2014": 0, "mtg": 0}
    for f in files[:8]:
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _RE_2024.search(t):
            votes["2024"] += 1
        elif _RE_MTG.search(t):
            votes["mtg"] += 1
        elif _RE_STAT.search(t):
            votes["2014"] += 1
    return max(votes, key=votes.get)


def _cn_stem(name: str) -> str:
    m = re.match(r"^([\u4e00-\u9fff·（）()、，0-9 ]+)", name.strip())
    return m.group(1).strip() if m else ""


def collect(top: Path) -> list[Path]:
    """收集目录下所有含统计块的 md（相对 5e_chm/md 的路径）。"""
    out = []
    for f in sorted(top.rglob("*.md")):
        if f.name.startswith("New_Item"):
            continue
        try:
            t = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if has_stat_block(t):
            out.append(f.relative_to(_MD_ROOT))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "inventory.json"))
    args = ap.parse_args()

    from book_map import (  # noqa: E402
        resolve_book,
        resolve_module,
        resolve_dndbeyond,
        is_third_party,
    )

    books = []
    for top in sorted(_MD_ROOT.iterdir()):
        if not top.is_dir() or top.name in _SKIP_TOP:
            continue
        if top.name in _PARENT_DIRS:
            for sub in sorted(top.iterdir()):
                if not sub.is_dir():
                    continue
                files = collect(sub)
                if not files:
                    continue
                if top.name == "模组":
                    source, edition = resolve_module(sub.name)
                    kind = "module"
                elif top.name == "第三方":
                    source, edition = sub.name, None
                    kind = "third_party"
                elif top.name == "DNDBeyond":
                    source, edition = resolve_dndbeyond(sub.name)
                    if source is None:
                        source, edition = sub.name, None
                    kind = "dndbeyond"
                else:  # 其他
                    source, edition = sub.name, None
                    kind = "other"
                books.append({
                    "book": f"{top.name}/{sub.name}",
                    "kind": kind,
                    "source": source,
                    "edition": edition,
                    "format": classify_format([_MD_ROOT / p for p in files]),
                    "n_files": len(files),
                    "n_stat": len(files),
                    "files": [str(p) for p in files],
                })
        else:
            files = collect(top)
            source, edition = resolve_book(top.name)
            if source is None and edition is None:
                # 无怪物书：仍记录（n_stat 可能 >0，供人工复核），但不排提取批次
                if not files:
                    continue
            books.append({
                "book": top.name,
                "kind": "official",
                "source": source,
                "edition": edition,
                "format": classify_format([_MD_ROOT / p for p in files]) if files else "2014",
                "n_files": len(files),
                "n_stat": len(files),
                "files": [str(p) for p in files],
            })

    # 附统计信息
    total_stat = sum(b["n_stat"] for b in books)
    summary = {
        "md_root": str(_MD_ROOT),
        "n_books": len(books),
        "n_stat_files": total_stat,
        "books": books,
    }
    Path(args.out).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"清单已写 {args.out}")
    print(f"书数={len(books)}，含统计块文件={total_stat}")
    for b in books:
        src = b["source"] or "(skip)"
        print(f'  [{b["kind"]:11s}] {b["book"]:28s} src={src:12s} '
              f'ed={b["edition"] or "-":4s} fmt={b["format"]:4s} stat={b["n_stat"]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

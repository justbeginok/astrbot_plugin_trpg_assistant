"""make_plan.py — 从 inventory.json 生成分批提取计划（batch_plan.json）。

每本书按 CHUNK 文件数切块；为每本书分配一个 ASCII `slug`（文件名用）。
官方书 slug=source 码；第三方/其他/中文 source 书用 `tp<n>` 递增。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
CHUNK = 38


def _slugify(src: str, used: set[str]) -> str:
    # 已 ASCII 的直接用
    if re.fullmatch(r"[A-Za-z0-9\-]+", src):
        used.add(src)
        return src
    # 中文/混合 → tp<n>
    i = 1
    while f"tp{i:02d}" in used:
        i += 1
    s = f"tp{i:02d}"
    used.add(s)
    return s


def main() -> int:
    inv = json.loads((_HERE / "inventory.json").read_text(encoding="utf-8"))
    plan = []
    used: set[str] = set()
    # 先给官方（ASCII）分配，再第三方
    books = sorted(inv["books"], key=lambda b: (b.get("source") is None, b["book"]))
    for b in books:
        src = b.get("source")
        files = b.get("files") or []
        if src is None or not files:
            continue
        ed = b.get("edition")
        if ed is None:
            ed = "2024" if b["format"] == "2024" else "2014"
        slug = _slugify(src, used)
        for i in range(0, len(files), CHUNK):
            plan.append({
                "book": b["book"],
                "source": src,
                "slug": slug,
                "edition": ed,
                "format": b["format"],
                "chunk": i // CHUNK,
                "n": len(files[i:i + CHUNK]),
                "files": files[i:i + CHUNK],
            })
    (_HERE / "batch_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"任务数={len(plan)}，文件={sum(p['n'] for p in plan)}")
    # 打印 slug -> source 映射（供 merge 用）
    mapping = {}
    for p in plan:
        mapping.setdefault(p["slug"], p["source"])
    (_HERE / "slug_map.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    for s, src in mapping.items():
        print(f"  {s} -> {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

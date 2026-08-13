"""llm_fallback.py — 规则解析失败文件的 LLM 子代理兜底（参照 monster_extract 模式）。

用法：
  1. python scripts/class_extract/llm_fallback.py list
     → 扫描规则解析失败的文件清单（写入 out/llm_fallback_tasks.json）
  2. 对清单中的文件用 LLM 子代理（flash/lite）读 md → 产 5etools 兼容
     classFeature/subclassFeature JSON 片段（见 llm_class_schema.md）
  3. python scripts/class_extract/llm_fallback.py merge <llm_产物目录>
     → 把 LLM 产物合并进 chm emit 结果（同 run_extract 流程）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from class_extract.inventory import scan  # noqa: E402
from class_extract.parser import parse_md  # noqa: E402


def list_failed(md_root: Path, out: Path) -> None:
    plan = [p for p in scan(md_root) if p.kind in ("class", "subclass", "class_options")]
    tasks = []
    for item in plan:
        feats = parse_md(item.path.read_text(encoding="utf-8"))
        if not feats:
            tasks.append({
                "path": str(item.path),
                "book": item.book,
                "source": item.source,
                "kind": item.kind,
                "class_name": item.class_name,
                "subclass_name": item.subclass_name,
            })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"待 LLM 兜底文件: {len(tasks)} 个 → {out}")
    for t in tasks:
        print(f"  [{t['kind']}] {t['class_name']}/{t['subclass_name']} {Path(t['path']).name}")


def merge(llm_dir: Path, out: Path) -> None:
    """把 LLM 产物（{源文件名}.json，含 classFeature/subclassFeature 数组）合并为 class-*.json。"""
    from class_extract.emit import _SLUG, _resolve_class

    merged: dict[str, dict] = {}
    for f in sorted(llm_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        cls = data.get("className") or ""
        key = _SLUG.get(_resolve_class(cls), f"custom-{cls}")
        dst = merged.setdefault(key, {
            "class": [], "subclass": [], "classFeature": [], "subclassFeature": [],
        })
        # 注册 subclass 条目（LLM 产物是子职时）
        sub = (data.get("subclass") or "").strip()
        if sub:
            sub_row = {
                "name": sub, "shortName": sub, "className": cls,
                "source": data.get("source") or "",
            }
            if sub_row not in dst["subclass"]:
                dst["subclass"].append(sub_row)
        for k in ("classFeature", "subclassFeature"):
            for row in data.get(k, []):
                row = dict(row)
                if k == "subclassFeature":
                    row.setdefault("className", cls)
                    row.setdefault("subclassShortName", sub)
                else:
                    row.setdefault("className", cls)
                row.setdefault("source", data.get("source") or "")
                if row not in dst[k]:
                    dst[k].append(row)
    out.mkdir(parents=True, exist_ok=True)
    for key, data in merged.items():
        p = out / f"class-{key}.json"
        existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {
            "class": [], "subclass": [], "classFeature": [], "subclassFeature": []}
        for k in ("classFeature", "subclassFeature"):
            existing[k].extend(data.get(k, []))
        existing["subclass"].extend(data.get("subclass", []))
        p.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"LLM 产物已合并到 {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="职业提取 LLM 兜底")
    ap.add_argument("cmd", choices=("list", "merge"))
    ap.add_argument("--md", default=r"C:/Users/75957/WorkBuddy/可爱骰娘/5e_chm/md")
    ap.add_argument("--out", default="scripts/class_extract/out")
    ap.add_argument("--llm-dir", default="scripts/class_extract/out/llm")
    args = ap.parse_args()
    if args.cmd == "list":
        list_failed(Path(args.md), Path(args.out) / "llm_fallback_tasks.json")
    else:
        merge(Path(args.llm_dir), Path(args.out) / "chm")


if __name__ == "__main__":
    main()

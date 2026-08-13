"""打包插件发布 zip 到 workspace 根 dist/。

用法:
    python scripts/pack_release.py [版本号]

版本号缺省时从 metadata.yaml 读取 version 字段。
产物命名: dist/trpg_assistant-v{版本}.zip
zip 内前缀: astrbot_plugin_trpg_assistant/

排除清单（构建缓存 / 中间产物，运行时与交付均不需要）:
    .git/ __pycache__/ .pytest_cache/ .cache/
    scripts/_md_cache/  kb_data/*.bak*
    scripts/monster_extract/out/  scripts/monster_extract/pilot/
    scripts/_thc_pilot/
"""
from __future__ import annotations

import os
import re
import sys
import zipfile
from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_trpg_assistant"

# 目录名（任意层级命中即排除，如 tests/__pycache__ 也排除）
EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".cache",
}
# 相对插件根的具体目录路径前缀
EXCLUDE_PATHS = {
    "scripts/_md_cache",
    "scripts/monster_extract/out",
    "scripts/monster_extract/pilot",
    "scripts/_thc_pilot",
    "scripts/class_extract/out",
    "scripts/optional_extract/out",
}
EXCLUDE_GLOBS = ("kb_data/*.bak*",)


def _excluded(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    parts = norm.split("/")
    # 任意层级的目录名命中（不含最后的文件名段）
    for seg in parts[:-1]:
        if seg in EXCLUDE_DIRS:
            return True
    # 具体路径前缀命中
    for p in EXCLUDE_PATHS:
        if norm.startswith(p + "/"):
            return True
    for g in EXCLUDE_GLOBS:
        # 简单 glob 匹配（仅支持 * 在段内）
        if Path(norm).match(g):
            return True
    return False


def main() -> int:
    plugin_dir = Path(__file__).resolve().parent.parent
    if plugin_dir.name != PLUGIN_NAME:
        print(f"警告: 脚本所在目录不是 {PLUGIN_NAME}: {plugin_dir}")
    dist_dir = plugin_dir.parent / "dist"
    dist_dir.mkdir(exist_ok=True)

    version = sys.argv[1] if len(sys.argv) > 1 else None
    if not version:
        m = re.search(r"^version:\s*v?([\w.]+)\s*$",
                      (plugin_dir / "metadata.yaml").read_text(encoding="utf-8"),
                      re.M)
        if not m:
            print("无法从 metadata.yaml 读取版本号")
            return 1
        version = m.group(1)

    out = dist_dir / f"trpg_assistant-v{version}.zip"

    files = [p for p in plugin_dir.rglob("*") if p.is_file()]
    added = 0
    skipped = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            rel = os.path.relpath(p, plugin_dir)
            if _excluded(rel):
                skipped += 1
                continue
            arcname = f"{PLUGIN_NAME}/{rel.replace(os.sep, '/')}"
            zf.write(p, arcname)
            added += 1

    size = out.stat().st_size / 1024 / 1024
    print(f"打包完成: {out}")
    print(f"  纳入 {added} 个文件, 排除 {skipped} 个文件, 大小 {size:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

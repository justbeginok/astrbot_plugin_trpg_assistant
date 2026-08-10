"""fetch_cn_data.py — 下载 5etools-cn 中文数据到本地目录（构建知识库前置步骤）。

背景：build_kb.py 需要 5etools-cn（tjliqy/5etools-cn，cn2.0 分支）data/ 目录的
本地副本。此前靠 git clone + sparse-checkout（需 --no-cone），但全局 git 代理
会掐断大传输，且 clone 易丢失。本脚本改用 GitHub API（git trees）+ raw 文件
直连下载，只拉 build_kb.py 需要的文件（约 20MB），并把 commit 固定为内置库
meta.source_commit，保证与随包数据库零数据漂移。

用法：
    python scripts/fetch_cn_data.py <out_dir> [--commit <sha>] [--jobs 8]
    # --commit 省略时读取 kb_data/dnd_kb.db 的 meta.source_commit（推荐）
    # 之后还需运行 scripts/fetch_en_spells.py 下载英文法术源查找表
    # （build_kb --en-spell-lookup 用；cn 仓库同名文件键是中文法术名，不可用）。

注意：raw.githubusercontent.com 需可直连（与 git 代理无关）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = "tjliqy/5etools-cn"
BRANCH = "cn2.0"
RAW = "https://raw.githubusercontent.com"
API = "https://api.github.com"

# build_kb.py 需要的文件（目录用前缀匹配，顶层单文件精确匹配）
_DIR_PREFIXES = (
    "data/bestiary/bestiary-",
    "data/spells/spells-",
    "data/class/class-",
)
_TOP_FILES = {
    "data/items.json",
    "data/items-base.json",
    "data/magicvariants.json",  # 魔法变体（焰舌/霜铭/+N 武器等，build_kb 展开用）
    "data/feats.json",
    "data/backgrounds.json",
    "data/conditionsdiseases.json",
    "data/races.json",
    "data/generated/gendata-spell-source-lookup.json",
}


def _default_commit() -> str:
    """读取内置库 meta.source_commit（构建数据版本的权威来源）。"""
    import sqlite3

    pkg = Path(__file__).resolve().parent.parent
    db = pkg / "kb_data" / "dnd_kb.db"
    if not db.exists():
        return BRANCH
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT value FROM meta WHERE key='source_commit'"
        ).fetchone()
        return (row[0] if row else BRANCH).strip() or BRANCH
    finally:
        con.close()


def _tree(commit: str) -> list[str]:
    """递归文件树 → 文件路径列表。"""
    url = f"{API}/repos/{REPO}/git/trees/{commit}?recursive=1"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return [
        it["path"]
        for it in data.get("tree", [])
        if it.get("type") == "blob"
    ]


def _needed(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        if p in _TOP_FILES:
            out.append(p)
        elif any(p.startswith(pref) for pref in _DIR_PREFIXES):
            out.append(p)
    return out


def _download(commit: str, rel: str, out_dir: Path) -> None:
    dest = out_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return  # 已下载（增量重跑）
    url = f"{RAW}/{REPO}/{commit}/{rel}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", help="输出 data/ 目录路径（build_kb.py 的 data_dir）")
    parser.add_argument("--commit", default=None, help="源仓库 commit（默认取内置库 meta）")
    parser.add_argument("--jobs", type=int, default=8, help="并发下载数")
    args = parser.parse_args()

    commit = args.commit or _default_commit()
    print(f"commit: {commit}")
    paths = _needed(_tree(commit))
    print(f"files: {len(paths)}")
    out_dir = Path(args.out_dir)
    ok = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {
            ex.submit(_download, commit, p, out_dir): p for p in paths
        }
        for fut in as_completed(futs):
            p = futs[fut]
            try:
                fut.result()
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"FAIL {p}: {e}", file=sys.stderr)
    total = sum(
        f.stat().st_size for f in out_dir.rglob("*") if f.is_file()
    )
    print(f"downloaded {ok}/{len(paths)}, total {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()

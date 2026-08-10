"""fetch_en_spells.py — 下载英文 5e.tools 法术源查找表（构建 spell_classes 用）。

自 2024 数据模型起，5e.tools 不再在 spell 条目内嵌 classes 字段，而是发布
站点生成的法术源查找表 gendata-spell-source-lookup.json（主职业法术表来源）：
    https://5e.tools/data/generated/gendata-spell-source-lookup.json

用法：
    python scripts/fetch_en_spells.py [--out .cache/5etools-en/data/generated/gendata-spell-source-lookup.json]

下载后供 build_kb.py 的 --en-spell-lookup 参数使用。
本脚本不随插件加载，仅作为开发/发版工具。
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import tempfile
import urllib.request
from pathlib import Path

_URL = "https://5e.tools/data/generated/gendata-spell-source-lookup.json"
_DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / ".cache"
    / "5etools-en"
    / "data"
    / "generated"
    / "gendata-spell-source-lookup.json"
)


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        enc = r.headers.get("Content-Encoding")
        if enc == "gzip":
            raw = gzip.decompress(raw)
        elif enc == "br":
            try:
                import brotli  # noqa: PLC0415

                raw = brotli.decompress(raw)
            except ImportError:  # 5e.tools 走 gzip，此分支理论不触发
                raise SystemExit("收到 br 编码但未安装 brotli，请 pip install brotli")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 5e.tools 法术源查找表")
    parser.add_argument("--out", default=str(_DEFAULT_OUT), help="输出 json 路径")
    args = parser.parse_args()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[fetch_en_spells] 下载 {_URL}")
    raw = _fetch(_URL)
    # 校验可解析且含 phb/xphb 键，避免抓到错误页面
    payload = json.loads(raw)
    for need in ("phb", "xphb"):
        if need not in payload:
            raise SystemExit(f"下载内容缺少 {need} 键，疑似非预期响应，已中止")

    # 先写临时文件再原子替换
    fd, tmp_path = tempfile.mkstemp(
        prefix="spell_lookup_", suffix=".json", dir=str(out_path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp_path, out_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    n = sum(len(v) for v in payload.values())
    print(f"[fetch_en_spells] 完成: {out_path}（{len(payload)} 个数据源，{n} 条法术名）")


if __name__ == "__main__":
    main()

"""fetch_chm_patch.py — 从「5E 不全书」CHM 站抓取 5e.tools 缺失条目，生成构建补丁。

背景：Weapon of Warning（警戒武器）等物品存在于官方书（DMG 2014 p.213 / XDMG
2024 p.324）与「不全书」人工翻译，但 5e.tools（英文站/中文站）从未收录。本脚本
从 CHM 站抓取这些缺失条目，解析为标准 5etools 条目 JSON，供 build_kb 合并入库。

用法：
    python scripts/fetch_chm_patch.py <patch_json 输出路径> [--base https://5echm.kagangtuya.top]

抓取清单（页面 URL 与解析锚点）在下方 PATCH_SOURCES 定义；产物结构：
    {
      "items": [ {kind-ish 标准条目, name/ENG_name/source/rarity/type/entries...}, ... ]
    }

注意：
- CHM 站由 WinCHM 生成，页面按书目/类别组织；物品页 `<P><STRONG><FONT>名 英文名
  </FONT></STRONG><BR><EM>类型，稀有度（需同调）</EM><BR>正文</P>`。
- 页面为 UTF-8，但 GitHub 源码仓库为 GBK；本脚本从部署站抓取（UTF-8）更稳。
- 补丁条目是人工维护的「白名单」——只抓明确缺失、且已在官方书确认存在的条目，
  不做全站盲抓（CHM 数据与 5etools 高度重叠，全抓无意义且引入排版噪声）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

# 待补条目清单：CHM 页面相对路径 + 物品锚名（页面内 STRONG 文本）→ 标准条目字段。
# 字段对齐 5etools items.json：name/ENG_name/source/type/rarity/reqAttune/entries。
PATCH_SOURCES: list[dict] = [
    {
        "page": "topics/城主指南/宝藏/魔法物品/武器/非普通.htm",
        "anchor": "警戒武器",
        "item": {
            "name": "警戒武器",
            "ENG_name": "Weapon of Warning",
            "source": "DMG",
            "page": 213,
            "type": "M|DMG",
            "rarity": "uncommon",
            "reqAttune": True,
            "entries": [],
        },
    },
    {
        "page": "topics/城主指南2024/7.宝藏/魔法物品详述/武器/非普通.htm",
        "anchor": "警戒武器",
        "item": {
            "name": "警戒武器",
            "ENG_name": "Weapon of Warning",
            "source": "XDMG",
            "page": 324,
            "type": "M|XDMG",
            "rarity": "uncommon",
            "reqAttune": True,
            "entries": [],
        },
    },
]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _fetch(base: str, page: str) -> str:
    """用 curl 子进程抓取。

    注意：不要加 --compressed——该站（EdgeOne CDN）对压缩请求返回截断内容，
    去掉后返回完整 UTF-8 页面（GBK 源被服务端转成 UTF-8）。
    """
    url = f"{base.rstrip('/')}/{quote(page)}"
    proc = subprocess.run(
        [
            "curl", "-sL", "--max-time", "60",
            "-A", _UA, url,
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"curl 抓取失败 ({proc.returncode}): {url}")
    return proc.stdout.decode("utf-8", errors="replace")


def _strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _extract_item(html: str, anchor: str, item: dict) -> dict:
    """在页面 HTML 中定位锚点物品段落并解析出正文。

    两种结构（依站点版本）：
    - 2014 站：<P><STRONG>名 英文名</STRONG><BR><EM>类型，稀有度（需同调）</EM><BR>正文</P>
    - 2024 站：<H6>名 英文名</H6><P><EM>类型，稀有度（需同调）</EM><BR>正文</P>
    统一策略：取锚点前最近的段落容器（H6 或 P）作为标题段起点；正文段取其后的
    第一个 <P>…</P>（若标题段本身即 <P> 且含正文，则取该段）。剔除首行 EM 类型行。
    """
    idx = html.find(anchor)
    if idx < 0:
        raise ValueError(f"页面中未找到锚点「{anchor}」")
    h6 = html.rfind("<H6", 0, idx)
    p_open = html.rfind("<P", 0, idx)
    starts = [s for s in (h6, p_open) if s >= 0]
    title_start = max(starts) if starts else idx
    # 若标题段是 <H6>，正文在下一个 <P>；若是 <P>，正文就在本段内
    is_h6 = title_start == h6 and h6 >= 0
    if is_h6:
        title_end = html.find("</H6>", title_start)
        body_start = html.find("<P>", title_end)
        body_end = html.find("</P>", body_start)
        if body_start < 0 or body_end < 0 or body_end < body_start:
            raise ValueError(f"锚点「{anchor}」正文段定位失败")
        seg = html[body_start + 3:body_end]
    else:
        # 同段结构：<P>...<STRONG>标题</STRONG><BR><EM>类型</EM><BR>正文...</P>
        seg_end = html.find("</P>", title_start)
        if seg_end < 0 or seg_end < title_start:
            raise ValueError(f"锚点「{anchor}」段落定位失败")
        seg = html[title_start + 2:seg_end]
    lines = re.split(r"<BR>", seg)
    body_parts = []
    for i, ln in enumerate(lines[1:]):  # 跳过标题行
        text = _strip_tags(ln)
        if not text:
            continue
        # 剔除首行类型行：EM 包裹的「武器（…），非普通（需同调）」且不含句号
        if i == 0 and re.match(r"^(武器|护甲|奇物|魔杖|法杖|戒指|药水|卷轴|装备)", text) and "。" not in text:
            continue
        body_parts.append(text)
    body = " ".join(body_parts).strip()
    if not body:
        raise ValueError(f"锚点「{anchor}」正文为空")
    item = dict(item)
    item["entries"] = [body]
    return item


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取 CHM 缺失条目生成构建补丁")
    ap.add_argument("out", type=Path, help="输出 JSON 路径")
    ap.add_argument("--base", default="https://5echm.kagangtuya.top", help="CHM 站根 URL")
    args = ap.parse_args()

    items = []
    for src in PATCH_SOURCES:
        print(f"[fetch_chm_patch] 抓取 {src['page']} 锚点 {src['anchor']}")
        html = _fetch(args.base, src["page"])
        item = _extract_item(html, src["anchor"], src["item"])
        items.append(item)
        print(f"  ✓ {item['name']}（{item['source']}）正文 {len(item['entries'][0])} 字")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"items": items}
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[fetch_chm_patch] 写入 {args.out}（{len(items)} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""emit.py — 解析结果 → 5etools 兼容 class-*.json。

按职业聚合输出，文件结构对齐 5etools-cn data/class/class-*.json：
{
  "class": [{name, source, ...}],
  "subclass": [{name, shortName, className, source}],
  "classFeature": [{name, className, level, source, entries: [str]}],
  "subclassFeature": [{name, className, subclassShortName, level, source, entries: [str]}],
}
body 直接作为 entries 的单元素（纯文本，build_kb 的 _flatten_entries 幂等）。

class/class_options → classFeature；subclass/multi → subclassFeature + subclass。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .inventory import PlanItem, scan
from .parser import clean_body, parse_md

_SLUG = {
    "野蛮人": "barbarian", "吟游诗人": "bard", "牧师": "cleric",
    "德鲁伊": "druid", "战士": "fighter", "武僧": "monk", "圣武士": "paladin",
    "游侠": "ranger", "游荡者": "rogue", "术士": "sorcerer",
    "邪术师": "warlock", "魔契师": "warlock", "法师": "wizard",
    "奇械师": "artificer", "秘术师": "mystic", "血族": "vampire",
    "铳士": "gunslinger", "拳斗士": "brawler", "血猎手": "bloodhunter",
    "邪狱使": "fiendlock",
}

# 魔契师 = 邪术师（2024 新名）；库内 className 统一为「魔契师」（5etools-cn 基准）
_CLASS_ALIAS = {"邪术师": "魔契师"}


def _resolve_class(cls: str) -> str:
    return _CLASS_ALIAS.get(cls, cls)


def _clean_entries_body(body: str) -> list[str]:
    """body → entries（清洗空行；body 是纯文本，单元素即可）。"""
    body = clean_body(body)
    if not body:
        return []
    return [body]


def _feature_rows(f, cls: str, source: str, subclass_short: str | None) -> list[dict]:
    """把单个 ParsedFeature 展开为 1..N 行（按职业表全部出现等级）。

    「属性值提升」表里 [4,6,8,12,14,16,19] → 7 行，body 相同，
    与 5etools-cn 的「同名多等级各一行」形态一致，保证
    「查职业 X N级」等级钻取不丢条目。
    """
    levels = f.levels or [f.level] if f.level else []
    if not levels:
        levels = [1]
    rows = []
    for lv in levels:
        row = {
            "name": f.name,
            "className": cls,
            "level": lv,
            "source": source,
            "entries": _clean_entries_body(f.body),
        }
        if subclass_short is not None:
            row["subclassShortName"] = subclass_short
        rows.append(row)
    return rows


def emit_one(item: PlanItem, text: str) -> tuple[str, dict]:
    """解析单个文件并返回 (class_key, {classFeature|subclassFeature|subclass|class: []})。

    class_key 用于聚合到同名职业文件（如「战士」→ fighter.json）。
    """
    feats = parse_md(text)
    cls = _resolve_class(item.class_name)
    key = _SLUG.get(cls, f"custom-{cls}")
    out: dict = {"class": [], "subclass": [], "classFeature": [], "subclassFeature": []}

    if item.kind == "class":
        # 职业主体：本职特性 → classFeature
        for f in feats:
            rows = _feature_rows(f, cls, item.source, None)
            out["classFeature"].extend(r for r in rows if r["entries"])
    elif item.kind in ("subclass",):
        # 子职：subclass 条目 + subclassFeature
        out["subclass"].append({
            "name": item.subclass_name,
            "shortName": item.subclass_name,
            "className": cls,
            "source": item.source,
        })
        for f in feats:
            rows = _feature_rows(f, cls, item.source, item.subclass_name)
            out["subclassFeature"].extend(r for r in rows if r["entries"])
    elif item.kind == "multi":
        # 多子职合体文件（拉尼卡 子职选项 等）：解析后按「职业：子职」标题拆分
        for seg in _split_multi(text):
            out["subclass"].append({
                "name": seg["subclass"],
                "shortName": seg["subclass"],
                "className": seg["class"],
                "source": item.source,
            })
            for f in seg["feats"]:
                rows = _feature_rows(f, seg["class"], item.source, seg["subclass"])
                out["subclassFeature"].extend(r for r in rows if r["entries"])
    return key, out


def _split_multi(text: str) -> list[dict]:
    """按「职业：子职」标题拆分多子职合体文件（如拉尼卡 子职选项.md）。"""
    import re as _re

    segs: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        s = line.strip()
        m = _re.match(r"^([\u4e00-\u9fa5]{2,4})[：:]\s*(.+?)\s*$", s)
        if m:
            cls, sub = m.group(1), m.group(2)
            if current and current["feats"]:
                segs.append(current)
            current = {"class": cls, "subclass": sub, "feats": []}
            continue
        if current and s and not s.startswith("（"):
            # 当前子职段落内的裸标题行 → 尝试特性解析（简单方式：整段收集由 parse_md 处理）
            pass
    if current and current["feats"]:
        segs.append(current)
    # 简化：直接对整个文件 parse_md，再按标题归属（降级方案）
    if not segs:
        feats = parse_md(text)
        if feats:
            cls = _re.match(r"^([\u4e00-\u9fa5]{2,4})[：:]", text).group(1)
            segs.append({"class": cls, "subclass": "多子职", "feats": feats})
    return segs


def emit_all(md_root: Path) -> dict[str, dict]:
    """扫描 + 解析全部职业文件，按职业聚合输出。

    返回 {class_key: {class/subclass/classFeature/subclassFeature}}。
    """
    plan = scan(md_root)
    merged: dict[str, dict] = {}
    errors: list[str] = []
    for item in plan:
        if item.kind not in ("class", "subclass", "multi", "class_options"):
            continue
        try:
            text = item.path.read_text(encoding="utf-8")
            key, out = emit_one(item, text)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{item.path.name}: {e}")
            continue
        dst = merged.setdefault(key, {
            "class": [], "subclass": [], "classFeature": [], "subclassFeature": [],
        })
        for k in ("class", "subclass", "classFeature", "subclassFeature"):
            dst[k].extend(out[k])
    return merged


def write_all(merged: dict[str, dict], out_dir: Path) -> list[Path]:
    """把聚合结果写到 out_dir/class-{key}.json。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for key, data in sorted(merged.items()):
        p = out_dir / f"class-{key}.json"
        p.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        written.append(p)
    return written

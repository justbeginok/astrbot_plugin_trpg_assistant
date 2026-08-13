"""finalize.py — 5e_chm 提取结果与 5etools-cn 原 class JSON 按名合并。

合并策略（参照怪物重建 ADR-0021）：
- 5e_chm 覆盖同 (className, subclassName/空, source, level, name) 的特性行
  （5e_chm 人工校对优先；同一 source 同名的正文替换为 chm 版）；
- 5etools-cn 独有保留：5e_chm 未覆盖的 source（EGW/FRHoF/FTD/DSotDQ/
  VRGR/PSA/PSK/DMG/UA…）与 5e_chm 里没有的子职/职业原样保留；
- 第三方书（自定义 source 码）：5e_chm 全量新增。

输出：合并后的 class-*.json（覆盖 5etools-cn data/class/ 前先备份），
并对账报告每职业「chm 覆盖 / cn 独有 / 新增第三方」行数。
"""

from __future__ import annotations

import json
from pathlib import Path

# 5e_chm 覆盖的官方 source（其特性行以 chm 为准）
CHM_SOURCES = {"PHB", "XPHB", "XGE", "TCE", "SCAG", "GGR", "ERLW", "EFA"}

# 第三方自定义 source（chm 全量新增）
THIRD_PARTY_SOURCES = {
    "VTM", "SHO", "GTS", "WYM", "HMW", "GVG", "VDS", "XER", "BF", "BH",
    "DF", "TR", "THC", "ACT", "LOO", "DRK", "DCM", "ODA", "HMS", "VBO",
}


def _feature_key(row: dict) -> tuple:
    """classFeature/subclassFeature 的对齐键：name+className+level+source(+subclass)。"""
    key = (row.get("name", ""), row.get("className", ""),
           row.get("level", 0), row.get("source", ""))
    if "subclassShortName" in row:
        key += (row.get("subclassShortName", ""),)
    return key


def _subclass_key(row: dict) -> tuple:
    return (row.get("name", ""), row.get("shortName", ""),
            row.get("className", ""), row.get("source", ""))


def _drop_pseudo_subclass(data: dict) -> None:
    """过滤 2024 伪子职：subclass.name == className（如「战士/战士」）。

    5etools 2024 数据模型中，XPHB 部分职业把「子职业」占位声明为
    subclass.name=职业名（如战士的 subclass「战士」），其 subclassFeature
    实为 2024 本职特性（已由 chm classFeature 提供），保留会污染子职列表。
    """
    pseudo = {
        (s.get("name", ""), s.get("className", ""), s.get("source", ""))
        for s in data.get("subclass", [])
        if s.get("name") == s.get("className")
    }
    if not pseudo:
        return
    data["subclass"] = [
        s for s in data["subclass"]
        if (s.get("name", ""), s.get("className", ""), s.get("source", "")) not in pseudo
    ]
    data["subclassFeature"] = [
        r for r in data.get("subclassFeature", [])
        if (r.get("subclassShortName", ""), r.get("className", ""),
            r.get("source", "")) not in pseudo
    ]


def merge_one(chm_path: Path | None, cn_path: Path) -> tuple[dict, dict]:
    """合并单个职业文件。返回 (merged_data, report)。"""
    cn = json.loads(cn_path.read_text(encoding="utf-8"))
    _drop_pseudo_subclass(cn)  # 过滤 2024 伪子职（战士/战士）
    merged: dict = {"class": [], "subclass": [], "classFeature": [], "subclassFeature": []}
    for k in ("class", "subclass", "classFeature", "subclassFeature"):
        merged[k] = list(cn.get(k, []))

    if chm_path is None or not chm_path.exists():
        return merged, {"chm_cover": 0, "chm_new": 0,
                        "cn_keep": _count(cn), "third_party": 0}

    chm = json.loads(chm_path.read_text(encoding="utf-8"))
    _drop_pseudo_subclass(chm)

    # ---- 1) 替换 5etools-cn 中 chm 覆盖的同名行（保持顺序）----
    chm_cf = {_feature_key(r): r for r in chm.get("classFeature", [])}
    chm_sf = {_feature_key(r): r for r in chm.get("subclassFeature", [])}
    chm_sub = {_subclass_key(r): r for r in chm.get("subclass", [])}
    cover_cf = cover_sf = cover_sub = 0

    new_cf: list[dict] = []
    for r in merged["classFeature"]:
        key = _feature_key(r)
        if key in chm_cf and r.get("source") in CHM_SOURCES:
            new_cf.append(chm_cf.pop(key))
            cover_cf += 1
        else:
            new_cf.append(r)
    merged["classFeature"] = new_cf

    new_sf: list[dict] = []
    for r in merged["subclassFeature"]:
        key = _feature_key(r)
        if key in chm_sf and r.get("source") in CHM_SOURCES:
            new_sf.append(chm_sf.pop(key))
            cover_sf += 1
        else:
            new_sf.append(r)
    merged["subclassFeature"] = new_sf

    new_sub: list[dict] = []
    for r in merged["subclass"]:
        key = _subclass_key(r)
        if key in chm_sub and r.get("source") in CHM_SOURCES:
            new_sub.append(chm_sub.pop(key))
            cover_sub += 1
        else:
            new_sub.append(r)
    merged["subclass"] = new_sub

    # ---- 2) 追加 chm 独有（chm 里 cn 没有的，如 2024 新特性/第三方）----
    # class 数组（v0.50.1）：chm 职业主体条目（血族/拳斗士等 cn 独有）补入，
    # 使 entries.kind='class' 存在 → editions/英文名/富化/广搜可用。
    chm_class_keys = {
        (r.get("name", ""), r.get("source", "")) for r in chm.get("class", [])
    }
    merged_class_keys = {
        (r.get("name", ""), r.get("source", "")) for r in merged["class"]
    }
    for r in chm.get("class", []):
        k = (r.get("name", ""), r.get("source", ""))
        if k in chm_class_keys and k not in merged_class_keys:
            merged["class"].append(r)
    for r in chm.get("classFeature", []):
        if _feature_key(r) not in {_feature_key(x) for x in merged["classFeature"]}:
            merged["classFeature"].append(r)
    for r in chm.get("subclassFeature", []):
        if _feature_key(r) not in {_feature_key(x) for x in merged["subclassFeature"]}:
            merged["subclassFeature"].append(r)
    for r in chm.get("subclass", []):
        if _subclass_key(r) not in {_subclass_key(x) for x in merged["subclass"]}:
            merged["subclass"].append(r)

    third = sum(
        1 for r in merged["classFeature"] + merged["subclassFeature"]
        if r.get("source") in THIRD_PARTY_SOURCES
    )
    report = {
        "chm_cover": cover_cf + cover_sf + cover_sub,
        "chm_new": len(chm.get("classFeature", [])) + len(chm.get("subclassFeature", []))
                   + len(chm.get("subclass", [])) - (cover_cf + cover_sf + cover_sub),
        "cn_keep": _count(cn) - (cover_cf + cover_sf + cover_sub),
        "third_party": third,
    }
    return merged, report


def _count(data: dict) -> int:
    return (len(data.get("class", [])) + len(data.get("subclass", []))
            + len(data.get("classFeature", [])) + len(data.get("subclassFeature", [])))


def finalize(chm_dir: Path, cn_dir: Path, out_dir: Path) -> dict:
    """合并全部职业文件到 out_dir，返回对账报告。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, dict] = {}
    for cn_path in sorted(cn_dir.glob("class-*.json")):
        chm_path = chm_dir / cn_path.name
        merged, rep = merge_one(chm_path, cn_path)
        (out_dir / cn_path.name).write_text(
            json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        report[cn_path.stem.removeprefix("class-")] = rep
    # 5e_chm 独有职业文件（cn 里没有的，如血族/铳士/拳斗士）
    for chm_path in sorted(chm_dir.glob("class-*.json")):
        if not (cn_dir / chm_path.name).exists():
            data = json.loads(chm_path.read_text(encoding="utf-8"))
            (out_dir / chm_path.name).write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            report[chm_path.stem.removeprefix("class-")] = {
                "chm_cover": 0, "chm_new": _count(data), "cn_keep": 0, "third_party": _count(data),
            }
    return report

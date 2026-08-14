"""optional_extract — 可定制职业选项（魔能祈唤/战技/超魔法/战斗风格）提取。

5e_chm/md 中这些「选项」散落在多个文件，格式与 XGE/SCAG 子职同构：
裸标题（中文+空格+英文 / 中文English 无空格 / #### 标题）+ 可选先决行
（`*先决：...*` / `*消耗：N术法点*` / `*战斗风格专长（先决：...）*`）+ 正文。

特例：
- PHB 2014 战斗大师：战技嵌在 `#### 战技Maneuvers` 特性正文里，
  每条战技以 `*中文English。正文` 斜体行起始 → 从正文剥离；
- PHB 2014 术士：超魔法选项是 `超魔法Metamagic` 特性下的 `#### 标题`；
- TCE 祈唤：裸标题带 `*Legacy*` 尾注（旧版标记）。

输出 5etools 兼容 optionalfeatures.json：
  {"optionalfeature": [{"name","ENG_name","featureType","source","edition",
                        "prerequisite","entries","class","legacy"}, ...]}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# featureType 码（5etools 官方枚举）
FT = {"EI": "魔能祈唤", "MV": "战技", "MM": "超魔法", "FS": "战斗风格"}

# 标题正则
_NAKED_TITLE_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5][^A-Za-z\n]{0,24}?)\s+([A-Za-z][A-Za-z0-9'’\- ()]{1,40})$"
)
# 无空格：中文English（可带句号/尾注）。中文名排除 `*`（正文内 *法术名eng* 斜体引用防误判）
_NOSPACE_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5][^A-Za-z*\n]{0,24}?)([A-Za-z][A-Za-z0-9'’\- ]{1,40})(?:[*。．]|$)"
)
_H4_RE = re.compile(r"^#{4}\s+(.+?)\s*$")

# 先决/消耗行：*先决：…* / *先决条件：…* / *消耗：…* / *战斗风格专长（先决：…）*
# 2024 版先决行无尾星号（*消耗：1术法点 后直接换行正文）。
_PREREQ_RE = re.compile(
    r"^\s*(?:\*)?(?:先决(?:条件)?|消耗|战斗风格专长（先决：[^）]*）)[：:]?\s*(.*?)(?:\*)?\s*$"
)
# 2014 战斗大师正文战技：*中文English。正文（整段一条战技）
_INLINE_MANEUVER_RE = re.compile(
    r"^\s*\*([\u4e00-\u9fa5][^A-Za-z\n]{0,24}?)([A-Za-z][A-Za-z0-9'’\- ]{1,40})[。．.]\s*(.+)$"
)

# 源文件清单：(相对 md 根路径, source 码, featureType, 关联职业中文名, edition)
SOURCES: list[tuple[str, str, str, str, str]] = [
    # 魔能祈唤
    ("玩家手册/职业/邪术师/魔能祈唤.md", "PHB", "EI", "魔契师", "2014"),
    ("珊娜萨的万事指南/角色选项/邪术师/魔能祈唤.md", "XGE", "EI", "魔契师", "2014"),
    ("塔莎的万事坩埚/玩家选项/职业/邪术师（TCE）/魔能祈唤.md", "TCE", "EI", "魔契师", "2014"),
    ("玩家手册2024/角色职业/魔契师/魔能祈唤选项.md", "XPHB", "EI", "魔契师", "2024"),
    # 战技
    ("玩家手册/职业/战士/战斗大师.md", "PHB", "MV", "战士", "2014"),
    ("塔莎的万事坩埚/玩家选项/职业/战士（TCE）/战技选项.md", "TCE", "MV", "战士", "2014"),
    ("玩家手册2024/角色职业/战士/战斗大师.md", "XPHB", "MV", "战士", "2024"),
    # 超魔法
    ("玩家手册/职业/术士.md", "PHB", "MM", "术士", "2014"),
    ("玩家手册2024/角色职业/术士/超魔法选项.md", "XPHB", "MM", "术士", "2024"),
    # 战斗风格
    ("玩家手册2024/专长/战斗风格专长.md", "XPHB", "FS", "战士", "2024"),
    # v0.50.3：第三方职业选项（铳士战技项=战技；邪狱使禁令恩惠 IB；血猎手血咒 BC）
    ("第三方/瓦尔达的秘密尖塔/铳士/战技项.md", "VDS", "MV", "铳士", "2014"),
    ("第三方/邪狱使/禁令恩惠选项.md", "DF", "IB", "邪狱使", "2014"),
    ("第三方/血猎手/血咒.md", "BH", "BC", "血猎手", "2014"),
]


def _strip_eng(name: str) -> str:
    """去掉行尾英文名（含空格）。"""
    m = re.search(r"[A-Za-z]", name)
    if not m:
        return name.strip().rstrip("：:").strip()
    return name[: m.start()].strip().rstrip("：:").strip()


def _split_eng(title: str) -> tuple[str, str]:
    """从标题行拆出 (中文名, 英文名)。支持 中文 English / 中文English。"""
    title = title.strip()
    # 中文+空格+英文
    m = re.match(r"^([\u4e00-\u9fa5][^A-Za-z\n]{0,24}?)\s+([A-Za-z][A-Za-z0-9'’\- ()]{1,40})$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # 中文English 无空格
    m = re.match(r"^([\u4e00-\u9fa5][^A-Za-z\n]{0,24}?)([A-Za-z][A-Za-z0-9'’\- ]{1,40})$", title)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return _strip_eng(title), ""


def _is_skip_title(name: str) -> bool:
    """标题黑名单：选项文件里非选项条目。"""
    skip = {"魔能祈唤", "魔能祈唤选项", "战技选项", "超魔法选项", "战斗风格专长",
            "魔能祈唤Eldritch", "战技Maneuvers", "超魔法Metamagic",
            "血咒", "禁令恩惠", "禁令恩惠选项"}
    return name.strip() in skip or len(name) < 2


def _clean_body(body: str) -> str:
    """清理正文：去掉首行先决/消耗斜体、尾部 Legacy 尾注。"""
    lines = body.splitlines()
    # 去掉开头先决/消耗行
    while lines and _PREREQ_RE.match(lines[0].strip()):
        lines.pop(0)
    # 去掉开头 Legacy 尾注行
    while lines and re.match(r"^\s*\*?Legacy\*?\s*$", lines[0].strip()):
        lines.pop(0)
    return "\n".join(l for l in lines if l.strip()).strip()


def parse_blocks(text: str) -> list[dict]:
    """通用选项块解析（裸标题/无空格/#### 三格式）。

    返回 [{"name", "eng", "prerequisite", "body", "legacy"}]
    """
    out: list[dict] = []
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        title = None
        legacy = False
        if _H4_RE.match(s):
            title = _H4_RE.match(s).group(1)
        else:
            m = _NAKED_TITLE_RE.match(s)
            if m and len(s) < 60:
                title = m.group(0)
            else:
                m = _NOSPACE_RE.match(s)
                if m and len(s) < 60:
                    title = m.group(0)
        if not title:
            i += 1
            continue
        name, eng = _split_eng(title)
        # TCE Legacy 尾注：裸标题后直接跟 *Legacy* 或标题含 *Legacy
        if "*" in name or name.endswith("Legacy"):
            name = name.replace("*Legacy", "").strip()
            legacy = True
        if _is_skip_title(name):
            i += 1
            continue
        # 收集正文
        body: list[str] = []
        i += 1
        while i < n:
            l = lines[i].strip()
            if _H4_RE.match(l) or _NAKED_TITLE_RE.match(l) \
                    or (_NOSPACE_RE.match(l) and len(l) < 60):
                break
            body.append(l)
            i += 1
        body_txt = "\n".join(b for b in body if b).strip()
        # 先决行
        prereq = ""
        for l in body:
            m = _PREREQ_RE.match(l.strip())
            if m:
                prereq = m.group(1).strip()
                break
        out.append({
            "name": name, "eng": eng,
            "prerequisite": prereq,
            "body": _clean_body(body_txt),
            "legacy": legacy,
        })
    return out


def extract_maneuvers_phb2014(text: str) -> list[dict]:
    """PHB 2014 战斗大师：从 `#### 战技Maneuvers` 正文剥离 `*中文English。正文` 斜体战技。"""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        m = _H4_RE.match(s)
        if m and _split_eng(m.group(1))[0] == "战技":
            break
        i += 1
    if i >= n:
        return []
    i += 1  # 进入战技段
    out: list[dict] = []
    while i < n:
        s = lines[i].strip()
        if _H4_RE.match(s) or s.startswith("####"):
            break
        m = _INLINE_MANEUVER_RE.match(s)
        if m:
            name = _strip_eng(m.group(1))
            eng = m.group(2).strip()
            body = m.group(3).strip()
            out.append({"name": name, "eng": eng, "prerequisite": "",
                        "body": body, "legacy": False})
        i += 1
    return out


def extract_maneuvers_xphb2024(text: str) -> list[dict]:
    """XPHB 2024 战斗大师：`### 战技选项 Maneuver Options` 段后 ` 伏击Ambush。正文`。"""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("###") and "战技选项" in s:
            break
        i += 1
    if i >= n:
        return []
    i += 1
    out: list[dict] = []
    while i < n:
        s = lines[i].strip()
        if s.startswith("#") or s.startswith("---"):
            break
        # 战技段内行即战技（标题+正文同行，无 60 字符限制）
        m = _NOSPACE_RE.match(s)
        if m:
            name = m.group(1).strip().rstrip("：:")
            eng = m.group(2).strip()
            body = s[m.end():].strip().lstrip("。．")
            out.append({"name": name, "eng": eng, "prerequisite": "",
                        "body": body, "legacy": False})
        i += 1
    return out


def extract_metamagic_phb2014(text: str) -> list[dict]:
    """PHB 2014 术士：`超魔法Metamagic` 特性之后的 `#### 标题` 即超魔法选项。"""
    lines = text.splitlines()
    i, n = 0, len(lines)
    # 定位 超魔法Metamagic 特性（裸标题无空格）
    while i < n:
        s = lines[i].strip()
        if re.match(r"^超魔法[A-Za-z]", s):
            break
        i += 1
    if i >= n:
        return []
    i += 1
    # 该特性正文（含简介段落），之后的 #### 是选项
    out: list[dict] = []
    while i < n:
        s = lines[i].strip()
        m = _H4_RE.match(s)
        if m:
            name, eng = _split_eng(m.group(1))
            if not _is_skip_title(name):
                body: list[str] = []
                i += 1
                while i < n:
                    l = lines[i].strip()
                    if _H4_RE.match(l):
                        break
                    body.append(l)
                    i += 1
                out.append({"name": name, "eng": eng, "prerequisite": "",
                            "body": _clean_body("\n".join(body)), "legacy": False})
                continue
        i += 1
    return out


def extract_interdict_boons(text: str) -> list[dict]:
    """邪狱使禁令恩惠（v0.50.3）：`### N级禁令恩惠`（H3 等级段）→ 段内裸标题选项。

    段等级（2/7/13 级）写入 prerequisite「邪狱使等级N级+」（可反查等级）。
    """
    lines = text.splitlines()
    i, n = 0, len(lines)
    out: list[dict] = []
    level = 0
    while i < n:
        s = lines[i].strip()
        m = re.match(r"^###\s*(\d+)级禁令恩惠", s)
        if m:
            level = int(m.group(1))
            i += 1
            continue
        if _NAKED_TITLE_RE.match(s) and len(s) < 60:
            name, eng = _split_eng(s)
            if _is_skip_title(name):
                i += 1
                continue
            body: list[str] = []
            i += 1
            while i < n:
                l = lines[i].strip()
                if _NAKED_TITLE_RE.match(l) and len(l) < 60 \
                        or re.match(r"^###\s*\d+级禁令恩惠", l):
                    break
                body.append(l)
                i += 1
            prereq = ""
            for l in body:
                m2 = _PREREQ_RE.match(l.strip())
                if m2:
                    prereq = m2.group(1).strip()
                    break
            out.append({
                "name": name, "eng": eng,
                "prerequisite": f"邪狱使等级{level}级+" if level else prereq,
                "body": _clean_body("\n".join(body)),
                "legacy": False,
            })
            continue
        i += 1
    return out


def extract_all(md_root: Path) -> list[dict]:
    """全量提取。返回 5etools 兼容 optionalfeature 列表。"""
    rows: list[dict] = []
    for rel, source, ftype, cls, edition in SOURCES:
        path = md_root / rel
        if not path.exists():
            print(f"  [skip] 源文件缺失: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        blocks: list[dict] = []
        if rel.endswith("战斗大师.md") and source == "PHB":
            blocks = extract_maneuvers_phb2014(text)
        elif rel.endswith("战斗大师.md") and source == "XPHB":
            blocks = extract_maneuvers_xphb2024(text)
        elif rel == "玩家手册/职业/术士.md":
            blocks = extract_metamagic_phb2014(text)
        elif rel.endswith("禁令恩惠选项.md"):
            blocks = extract_interdict_boons(text)
        else:
            blocks = parse_blocks(text)
        for b in blocks:
            if not b["body"]:
                continue
            rows.append({
                "name": b["name"],
                "ENG_name": b["eng"],
                "featureType": ftype,
                "source": source,
                "edition": edition,
                "prerequisite": b["prerequisite"],
                "entries": [b["body"]],
                "class": [cls],
                "legacy": b["legacy"],
            })
        print(f"  {rel} → {len(blocks)} 条")
    return rows


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="可定制职业选项提取")
    ap.add_argument("--md", default=r"C:/Users/75957/WorkBuddy/可爱骰娘/5e_chm/md")
    ap.add_argument("--out", default="scripts/optional_extract/out/optionalfeatures.json")
    args = ap.parse_args()
    rows = extract_all(Path(args.md))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"optionalfeature": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    from collections import Counter
    print(f"\n合计 {len(rows)} 条: {dict(Counter(r['featureType'] for r in rows))}")
    print(f"写入: {out}")


if __name__ == "__main__":
    main()

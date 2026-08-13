"""parser.py — 从单个 5e_chm 职业 md 解析特性块。

支持五种格式族：
1. 显式等级（2024 / 多数第三方）：「N级：特性名 English」
2. 裸标题行（XGE / SCAG / TCE 子职）：「心灵之刃 Psychic Blades」，
   中文在前 + 空格 + 英文名在行尾；仅在「特性表」之后启用（避免误抓简介段）
3. #### 标题（2014 玩家手册）：「#### 特性名English」，等级在正文首句推断
4. ### 标题（2024 分节特性标题）
5. TCE 斜体标记：「*第N级XX特性*」前缀行

等级来源优先级：显式等级标题 > TCE 斜体 > 特性表（等级→特性名映射）> 正文首句推断。

输出：[{level, name, body}]，level=None 表示未推断出（留给 LLM 兜底或对账）。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 正则

# 显式等级标题：1级：战斗风格 Fighting Style / 10级: 战斗风格（二）
_EXPLICIT_LEVEL_RE = re.compile(r"^\s*(\d{1,2})\s*级\s*[：:]\s*(.+?)\s*$")
_H2_RE = re.compile(r"^#{2}\s+(.+?)\s*$")
_H4_RE = re.compile(r"^#{4}\s+(.+?)\s*$")
_H3_RE = re.compile(r"^#{3}\s+(.+?)\s*$")
_TCE_LEVEL_RE = re.compile(r"^\*{1,2}第\s*(\d{1,2})\s*级[^*]{0,12}\*{1,2}\s*$")

# 裸标题行（XGE/SCAG/TCE 子职）：中文 + 空格 + 英文名在行尾
_NAKED_TITLE_RE = re.compile(
    r"^([\u4e00-\u9fa5][^A-Za-z]{0,24}?)\s+([A-Za-z][A-Za-z0-9'’\- ()]{1,40})$"
)
# 无空格变体（2014 职业主体：「回气Second Wind」）—— 必须经特性表确认才收
_NAKED_TITLE_NOSPACE_RE = re.compile(
    r"^([\u4e00-\u9fa5]{2,8})([A-Za-z][A-Za-z0-9'’\- ()]{1,40})$"
)
# 特性表里的等级单元格（3rd/7th/10th…或纯数字）
_TABLE_LEVEL_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?")

# 正文首句等级推断（2014 格式）
_LEVEL_IN_BODY = [
    re.compile(r"从你?\s*第?\s*(\d{1,2})\s*级\s*选择[^。；]{0,16}(?:时|起|后)"),
    re.compile(r"在\s*第?\s*(\d{1,2})\s*级\s*选择本[^。；]{0,10}(?:时|起)"),
    re.compile(r"第?\s*(\d{1,2})\s*级\s*(?:起|时|后)"),
    re.compile(r"从(\d{1,2})\s*级"),
    re.compile(r"在?你?(\d{1,2})\s*级\s*选择"),
    re.compile(r"(\d{1,2})\s*级\s*(?:起|时|后)"),
    re.compile(r"在(\d{1,2})\s*级时"),
]
_EN_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’\- ]*$")

# 非特性块标题黑名单
_SKIP_TITLES = {
    "职业表", "快速建卡", "生命值", "熟练项", "装备", "创建战士", "创建角色",
    "创建德鲁伊", "创建武僧", "创建圣武士", "创建游侠", "创建游荡者", "创建术士",
    "创建吟游诗人", "创建野蛮人", "创建法师", "创建牧师", "创建邪术师", "创建奇械师",
    "职业特性", "施法", "法术位", "已知法术", "施法属性", "法术攻击", "施法能力",
    "灵能", "灵魂火花", "狂暴", "战斗风格选项", "起始装备", "转职", "兼职",
}
_SUB_SKIP = {
    "子职特性", "职业特性", "可选职业特性", "战斗风格选项", "范例", "示例",
    "阵营", "背景", "新法术", "专长", "变形", "学习野兽形态", "超魔法选项",
    "魔能祈唤选项", "魔能祈唤", "战技选项", "战斗大师构筑", "驯兽师伙伴",
}


class ParsedFeature:
    __slots__ = ("level", "name", "body", "raw_title", "levels")

    def __init__(self, level, name, body, raw_title="", levels=None):
        self.level = level
        self.name = name
        self.body = body
        self.raw_title = raw_title
        # 该特性在职业表中的全部出现等级（属性值提升→[4,6,8,12,14,16,19]）；
        # emit 据此展开为多行，保持「查职业 X N级」钻取与 5etools-cn 一致。
        self.levels = levels or ([level] if level else [])


def _strip_eng(name: str) -> str:
    name = name.strip()
    name = _EN_NAME_RE.sub("", name).strip().rstrip("：:：") or name
    return name.strip()


def _guess_level_from_body(body: str) -> int | None:
    head = body[:120]
    for pat in _LEVEL_IN_BODY:
        m = pat.search(head)
        if m:
            lv = int(m.group(1))
            if 1 <= lv <= 20:
                return lv
    return None


_HTML_TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _find_feature_table(lines: list[str]) -> int:
    """定位「特性表」（含 等级/特性 列的表）位置。

    返回表格起始行号；找不到返回 -1。
    5e_chm 的子职/职业特性表：表头含「等级」与「特性」列，行含「3rd」等
    等级单元格。markdown 表格以 `|` 开头；部分 2014 文件用 HTML <table>。
    """
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|"):
            if "等级" in s and "特性" in s:
                return i
        elif s.lower().startswith("<table") or "<tr" in s.lower():
            j = i
            while j < len(lines) and not lines[j].strip().lower().startswith("</table"):
                if "等级" in lines[j] and "特性" in lines[j]:
                    return j
                j += 1
    return -1


def _parse_html_table_rows(lines: list[str], start: int) -> list[list[str]]:
    """解析从 start 开始的 HTML 表格（<table>…</table>）为单元格矩阵。"""
    rows: list[list[str]] = []
    i = start
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s.lower().startswith("</table"):
            break
        if "<tr" in s.lower():
            cells = [c.strip() for c in _HTML_TD_RE.findall(s)]
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if cells and any(cells):
                rows.append(cells)
        i += 1
    return rows


def _table_add(mapping: dict[str, list[int]], name: str, level: int) -> None:
    """写入表映射：精确名 + 去括号别名（动作如潮（一次）→动作如潮）。
    mapping[name] = 该特性出现过的等级列表（升序去重）。
    「属性值提升」在 4/6/8/12/14/16/19 级 → [4,6,8,12,14,16,19]。"""
    lst = mapping.setdefault(name, [])
    if level not in lst:
        lst.append(level)
        lst.sort()
    # 去括号别名：动作如潮（一次）/ 不屈（两次）/ 额外攻击（2）→ 动作如潮 / 不屈 / 额外攻击
    m = re.match(r"^(.*?)[（(][^）)]*[）)]$", name)
    if m and m.group(1).strip():
        alias = m.group(1).strip()
        alst = mapping.setdefault(alias, [])
        if level not in alst:
            alst.append(level)
            alst.sort()


def _table_level_map(lines: list[str], table_start: int) -> dict[str, list[int]]:
    """解析特性表：特性名 → 等级。支持 markdown 与 HTML 表格。"""
    mapping: dict[str, list[int]] = {}
    s0 = lines[table_start].strip().lower()
    if s0.startswith("<table") or "<tr" in s0:
        rows = _parse_html_table_rows(lines, table_start)
        if not rows:
            return mapping
        header = next((r for r in rows if any("等级" in c for c in r)), rows[0])
        lv_idx = next((j for j, c in enumerate(header) if "等级" in c), -1)
        feat_idx = next((j for j, c in enumerate(header) if "特性" in c), -1)
        if lv_idx < 0 or feat_idx < 0:
            return mapping
        for cells in rows:
            if len(cells) <= max(lv_idx, feat_idx):
                continue
            m = _TABLE_LEVEL_RE.search(cells[lv_idx])
            if not m:
                continue
            level = int(m.group(1))
            for feat_name in re.split(r"[，,、]", cells[feat_idx]):
                feat_name = _strip_eng(feat_name.strip())
                if feat_name:
                    _table_add(mapping, feat_name, level)
        return mapping
    # markdown 表格
    i = table_start
    n = len(lines)
    header = None
    while i < n:
        s = lines[i].strip()
        if s.startswith("|"):
            header = s
            break
        i += 1
    if header is None:
        return mapping
    cols = [c.strip() for c in header.strip("|").split("|")]
    lv_idx = next((j for j, c in enumerate(cols) if "等级" in c), -1)
    feat_idx = next((j for j, c in enumerate(cols) if "特性" in c), -1)
    if lv_idx < 0 or feat_idx < 0:
        return mapping
    i += 1
    while i < n:
        s = lines[i].strip()
        if not s.startswith("|"):
            break
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) <= max(lv_idx, feat_idx):
            i += 1
            continue
        lv_cell = cells[lv_idx]
        m = _TABLE_LEVEL_RE.search(lv_cell)
        if not m:
            i += 1
            continue
        level = int(m.group(1))
        feat_cell = cells[feat_idx]
        for feat_name in re.split(r"[，,、]", feat_cell):
            feat_name = _strip_eng(feat_name.strip())
            if feat_name:
                _table_add(mapping, feat_name, level)
        i += 1
    return mapping


def _is_skip(name: str) -> bool:
    return name in _SKIP_TITLES or name in _SUB_SKIP


def _collect_body(lines: list[str], start: int, stop_re) -> tuple[str, int]:
    body: list[str] = []
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            body.append("")
            i += 1
            continue
        if s.startswith("---") or s.startswith("| ---"):
            break
        if any(r.match(s) for r in stop_re):
            break
        if s.startswith("|"):
            if re.match(r"^\|[\s\-:|]+\|?$", s):
                i += 1
                continue
        body.append(s)
        i += 1
    while body and body[-1] == "":
        body.pop()
    return "\n".join(body), i


def parse_md(text: str) -> list[ParsedFeature]:
    lines = text.splitlines()
    # 特性表锚点：表内特性名 → 等级（补充等级推断；表存在时也用于裸标题确认）
    table_start = _find_feature_table(lines)
    table_map: dict[str, int] = {}
    if table_start >= 0:
        table_map = _table_level_map(lines, table_start)

    feats: list[ParsedFeature] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        # ---- 1) 显式等级 ----
        m = _EXPLICIT_LEVEL_RE.match(s)
        if m:
            level = int(m.group(1))
            name = _strip_eng(m.group(2))
            body, i = _collect_body(lines, i + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
            if name and not _is_skip(name):
                feats.append(ParsedFeature(level, name, body, raw_title=s, levels=table_map.get(name)))
            continue
        # ---- 2) TCE 斜体等级 ----
        m = _TCE_LEVEL_RE.match(s)
        if m:
            level = int(m.group(1))
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n:
                nm_line = lines[j].strip()
                name = _strip_eng(nm_line)
                if nm_line.startswith("#"):
                    name = _strip_eng(re.sub(r"^#+\s*", "", nm_line))
                body, i = _collect_body(lines, j + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
                if name and not _is_skip(name):
                    feats.append(ParsedFeature(level, name, body, raw_title=s, levels=table_map.get(name)))
            continue
        # ---- 3) #### 标题 ----
        m = _H4_RE.match(s)
        if m:
            name = _strip_eng(m.group(1))
            body, i = _collect_body(lines, i + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
            if name and not _is_skip(name):
                lv = (table_map.get(name) or [None])[0] or _guess_level_from_body(body)
                # 无等级线索的 #### 块（如战斗风格选项箭术/防御）→ 非独立特性，跳过
                if lv is None:
                    continue
                feats.append(ParsedFeature(lv, name, body, raw_title=s, levels=table_map.get(name)))
            continue
        # ---- 4) ### 标题 ----
        m = _H3_RE.match(s)
        if m:
            name = _strip_eng(m.group(1))
            if _is_skip(name) or "成为" in name or "职业特性" in name:
                i += 1
                continue
            body, i = _collect_body(lines, i + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
            if name and not _is_skip(name):
                lv = (table_map.get(name) or [None])[0] or _guess_level_from_body(body)
                if lv is None:
                    continue
                feats.append(ParsedFeature(lv, name, body, raw_title=s, levels=table_map.get(name)))
            continue
        # ---- 4.5) ## 标题（血猎手/铳士等第三方：## 特性名 English）----
        m = _H2_RE.match(s)
        if m:
            name = _strip_eng(m.group(1))
            if _is_skip(name) or "成为" in name or "职业特性" in name \
                    or "创作人员" in name or "翻译" in name:
                i += 1
                continue
            body, i = _collect_body(lines, i + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
            if name and not _is_skip(name):
                lv = (table_map.get(name) or [None])[0] or _guess_level_from_body(body)
                if lv is None:
                    continue
                feats.append(ParsedFeature(lv, name, body, raw_title=s, levels=table_map.get(name)))
            continue
        # ---- 5) 裸标题行（中文+空格+英文行尾；需带等级线索）----
        m = _NAKED_TITLE_RE.match(s)
        if m and len(s) < 60 and i > 5:  # 跳过文件头简介
            name = _strip_eng(m.group(1))
            if not name or _is_skip(name):
                i += 1
                continue
            if any(f.name == name for f in feats):
                i += 1
                continue
            body, i = _collect_body(lines, i + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
            lv = (table_map.get(name) or [None])[0] or _guess_level_from_body(body)
            if lv is None:
                continue
            feats.append(ParsedFeature(lv, name, body, raw_title=s, levels=table_map.get(name)))
            continue
        # ---- 6) 无空格裸标题（2014「回气Second Wind」/ 第三方「引导神力Channel Divinity」）----
        # 等级来源：特性表确认（2014）或正文首句推断（第三方）。
        # 防正文内嵌英文误判：正文行首句无等级线索（如「陷入恐慌Frightened」）→ 跳过。
        m = _NAKED_TITLE_NOSPACE_RE.match(s)
        if m and len(s) < 50:
            name = _strip_eng(m.group(1))
            if name and not _is_skip(name) \
                    and not any(f.name == name for f in feats):
                body, i = _collect_body(lines, i + 1, stop_re=(
                _EXPLICIT_LEVEL_RE, _H2_RE, _H4_RE, _H3_RE, _TCE_LEVEL_RE, _NAKED_TITLE_RE, _NAKED_TITLE_NOSPACE_RE,
            ))
                lv = (table_map.get(name) or [None])[0] \
                    or _guess_level_from_body(body)
                if lv is None:
                    continue
                feats.append(ParsedFeature(
                    lv, name, body, raw_title=s,
                    levels=table_map.get(name) or [lv]))
                continue
        i += 1
    return feats


def clean_body(body: str) -> str:
    import re as _re

    return _re.sub(r"\n{3,}", "\n\n", body.strip())

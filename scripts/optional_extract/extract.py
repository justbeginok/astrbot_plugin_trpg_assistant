"""optional_extract — 可定制职业选项提取（v0.53.0 扩至 11 类）。

5e_chm/md 中这些「选项」散落在多个文件，格式与 XGE/SCAG 子职同构：
裸标题（中文+空格+英文 / 中文English 无空格 / #### 标题）+ 可选先决行
（`*先决：...*` / `*消耗：N术法点*` / `*战斗风格专长（先决：...）*`）+ 正文。

类型（featureType 码沿用 5etools 官方枚举）：
- EI 魔能祈唤 / MV 战技 / MM 超魔法 / FS 战斗风格（v0.50.0）
- IB 禁令恩惠 / BC 血咒（v0.50.3 第三方）
- AI 注法 / AS 奥术射击 / ED 元素戒律 / RN 符文 / PB 契约恩赐（v0.53.0）

特例：
- PHB 2014 战斗大师：战技嵌在 `#### 战技Maneuvers` 特性正文里，
  每条战技以 `*中文English。正文` 斜体行起始 → 从正文剥离；
- PHB 2014 术士：超魔法选项是 `超魔法Metamagic` 特性下的 `#### 标题`；
- TCE 祈唤：裸标题带 `*Legacy*` 尾注（旧版标记）。
- XGE 魔射手：奥术射击选项在 `奥术射击选项 Arcane Shot Options` 段后（裸标题）；
- PHB 2014 四象宗：元素戒律在 `#### 法门Elemental Disciplines` 后（斜体行，
  标题内带 `（需N级）` 先决）；
- TCE 符文骑士：符文在子职特性正文后（裸标题，`山丘/风暴符文` 标题带
  `*（第7级或更高）*` 先决），`*第N级符文骑士特性*` 开头的子职特性排除；
- PHB 2014 邪术师：契约恩赐是 `魔契恩泽Pact Boon` 特性下的 `#### 标题`
  （链/刃/书魔契），`#### 边栏：` 段排除；
- TCE 邪术师：护符魔契在 `魔契恩泽选项 Pact Boon Option` 段后（裸标题 1 条）。

输出 5etools 兼容 optionalfeatures.json：
  {"optionalfeature": [{"name","ENG_name","featureType","source","edition",
                        "prerequisite","entries","class","legacy"}, ...]}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# featureType 码（5etools 官方枚举）
FT = {"EI": "魔能祈唤", "MV": "战技", "MM": "超魔法", "FS": "战斗风格",
      "IB": "禁令恩惠", "BC": "血咒", "AI": "注法", "AS": "奥术射击",
      "ED": "元素戒律", "RN": "符文", "PB": "契约恩赐"}

# 标题正则
_NAKED_TITLE_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5][^A-Za-z\n]{0,24}?)\s+([A-Za-z][A-Za-z0-9'’\- ()]{1,40})$"
)
# 无空格：中文English（可带句号/尾注）。中文名排除 `*`（正文内 *法术名eng* 斜体引用防误判）
_NOSPACE_RE = re.compile(
    r"^\s*([\u4e00-\u9fa5][^A-Za-z*\n]{0,24}?)([A-Za-z][A-Za-z0-9'’\- ]{1,40})(?:[*。．]|$)"
)
_H4_RE = re.compile(r"^#{4}\s+(.+?)\s*$")

# 先决/消耗行：*先决：…* / *先决条件：…* / *先决条件；…* / *消耗：…* / *战斗风格专长（先决：…）*
# 2024 版先决行无尾星号（*消耗：1术法点 后直接换行正文）。
_PREREQ_RE = re.compile(
    r"^\s*(?:\*)?(?:先决(?:条件)?|消耗|战斗风格专长（先决：[^）]*）)[：:；]?\s*(.*?)(?:\*)?\s*$"
)
# 符文等级先决行：*（第7级或更高）*（山丘/风暴符文标题后）
_RUNE_PREREQ_RE = re.compile(r"^\s*\*?（第(\d+)级或更高）\*?\s*$")
# 选项正文斜体行：*中文English（需N级）。*正文（2014 战技 / 元素戒律同构；
# 元素戒律标题带 （需N级） 等级先决，正文前可能有 `*`）。
_INLINE_OPTION_RE = re.compile(
    r"^\s*\*([\u4e00-\u9fa5][^A-Za-z\n]{0,24}?)([A-Za-z][A-Za-z0-9'’\- ]{1,40})"
    r"(?:（需(\d+)级）)?[。．.]\*?\s*(.+)$"
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
    # v0.53.0：奇械师注法（独立文件，裸标题）
    ("塔莎的万事坩埚/玩家选项/职业/奇械师/奇械师注法.md", "TCE", "AI", "奇械师", "2014"),
    # v0.53.0：奥术射击（XGE 魔射手子职正文段，裸标题）
    ("珊娜萨的万事指南/角色选项/战士/魔射手.md", "XGE", "AS", "魔射手", "2014"),
    # v0.53.0：元素戒律（PHB 2014 四象宗特性正文，斜体行 + （需N级）先决）
    ("玩家手册/职业/武僧/四象宗.md", "PHB", "ED", "武僧", "2014"),
    # v0.53.0：符文（TCE 符文骑士子职正文段，裸标题 + （第N级或更高）先决）
    ("塔莎的万事坩埚/玩家选项/职业/战士（TCE）/符文骑士.md", "TCE", "RN", "战士", "2014"),
    # v0.53.0：契约恩赐（PHB 2014 邪术师 #### 段 3 条 + TCE 护符魔契 1 条）
    ("玩家手册/职业/邪术师.md", "PHB", "PB", "魔契师", "2014"),
    ("塔莎的万事坩埚/玩家选项/职业/邪术师（TCE）.md", "TCE", "PB", "魔契师", "2014"),
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
            "血咒", "禁令恩惠", "禁令恩惠选项",
            "魔契恩泽", "魔契恩泽选项", "替换魔能", "边栏",
            "奇械师注法", "奥术射击选项",
            "豁免", "动作", "反应", "护甲等级", "生命值"}
    nm = name.strip()
    if nm in skip or len(nm) < 2:
        return True
    # 非选项行：含冒号/加号/纯数字（如「豁免：敏捷+2」「护甲等级：13+PB」）
    if "：" in nm or ":" in nm or "+" in nm:
        return True
    if nm.startswith("边栏") or nm.startswith("属性值提升"):
        return True
    return False


def _clean_body(body: str) -> str:
    """清理正文：去掉首行先决/消耗斜体、`*物品：…*` 星号（注法）、尾部 Legacy 尾注。"""
    lines = body.splitlines()
    # 去掉开头先决/消耗行
    while lines and _PREREQ_RE.match(lines[0].strip()):
        lines.pop(0)
    # 去掉开头 Legacy 尾注行
    while lines and re.match(r"^\s*\*?Legacy\*?\s*$", lines[0].strip()):
        lines.pop(0)
    # 注法物品行 `*物品：…*`：去掉星号保留文本（渲染为普通行）
    out: list[str] = []
    for l in lines:
        s = l.strip()
        if re.match(r"^\*物品：[^*]*\*?$", s):
            out.append(s.strip("*").strip())
        else:
            out.append(l)
    return "\n".join(x for x in out if x.strip()).strip()


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
        m = _INLINE_OPTION_RE.match(s)
        if m:
            name = _strip_eng(m.group(1))
            eng = m.group(2).strip()
            body = m.group(4).strip()
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


def extract_arcane_shots_xge(text: str) -> list[dict]:
    """XGE 魔射手：`奥术射击选项 Arcane Shot Options` 段后的裸标题选项（8 条）。"""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        m = _NAKED_TITLE_RE.match(s)
        if m and _strip_eng(m.group(1)) == "奥术射击选项":
            break
        i += 1
    if i >= n:
        return []
    # 从段说明后的第一条裸标题开始
    out: list[dict] = []
    while i < n:
        s = lines[i].strip()
        m = _NAKED_TITLE_RE.match(s)
        if m and len(s) < 60 and not _is_skip_title(_split_eng(s)[0]):
            name, eng = _split_eng(s)
            body: list[str] = []
            i += 1
            while i < n:
                l = lines[i].strip()
                if _NAKED_TITLE_RE.match(l) and len(l) < 60:
                    break
                body.append(l)
                i += 1
            out.append({"name": name, "eng": eng, "prerequisite": "",
                        "body": _clean_body("\n".join(body)), "legacy": False})
            continue
        i += 1
    return out


def extract_elemental_disciplines_phb(text: str) -> list[dict]:
    """PHB 2014 四象宗：`#### 法门Elemental Disciplines` 后的斜体行戒律（17 条）。

    `*寒冬之息Breath of Winter（需17级）。*正文`：标题内（需N级）→ prerequisite。
    """
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        m = _H4_RE.match(s)
        if m and _split_eng(m.group(1))[0] == "法门":
            break
        i += 1
    if i >= n:
        return []
    i += 1
    out: list[dict] = []
    while i < n:
        s = lines[i].strip()
        if s.startswith("####"):
            break
        m = _INLINE_OPTION_RE.match(s)
        if m:
            name = _strip_eng(m.group(1))
            eng = m.group(2).strip()
            lvl = m.group(3)
            prereq = f"武僧等级{lvl}级+" if lvl else ""
            body = m.group(4).strip()
            out.append({"name": name, "eng": eng, "prerequisite": prereq,
                        "body": body, "legacy": False})
        i += 1
    return out


def extract_runes_tce(text: str) -> list[dict]:
    """TCE 符文骑士：子职正文后的裸标题符文（6 条）。

    `山丘符文Hill Rune*（第7级或更高）*` 标题带等级先决 → prerequisite；
    `*第N级符文骑士特性*` 开头的子职特性（巨人之力等）排除。
    """
    # 山丘/风暴符文标题：Xxx符文Eng*（第N级或更高）*（含等级先决）
    _RUNE_TITLE_RE = re.compile(
        r"^([\u4e00-\u9fa5]{2,6}符文)([A-Za-z][A-Za-z0-9'’\- ]{1,30})\*?（第(\d+)级或更高）\*?$"
    )
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        m = _RUNE_TITLE_RE.match(s) or _NAKED_TITLE_RE.match(s) \
            or _NOSPACE_RE.match(s)
        if m and (_split_eng(m.group(0))[0] in ("云雾符文", "火焰符文", "寒霜符文")):
            break
        i += 1
    if i >= n:
        return []
    out: list[dict] = []
    while i < n:
        s = lines[i].strip()
        if s.startswith("*第") and "级符文骑士特性" in s:
            break
        if re.match(r"^巨人之力|^符文之盾|^奇伟身躯|^符文大师", s):
            break
        m = _RUNE_TITLE_RE.match(s)
        if m:
            name, eng, lvl = m.group(1), m.group(2), m.group(3)
            prereq = f"战士等级{lvl}级+"
        else:
            m = _NAKED_TITLE_RE.match(s) or _NOSPACE_RE.match(s)
            name, eng, prereq = "", "", ""
        if m and len(s) < 60:
            if not name:
                name, eng = _split_eng(s)
            if _is_skip_title(name) or not re.search(r"符文$", name):
                i += 1
                continue
            body: list[str] = []
            i += 1
            while i < n:
                l = lines[i].strip()
                if re.match(r"^[一-龥]{2,6}符文[^\n]{0,40}", l) and len(l) < 60:
                    break
                if re.match(r"^巨人之力|^符文之盾|^奇伟身躯|^符文大师", l):
                    break
                if l.startswith("*第") and "级符文骑士特性" in l:
                    break
                body.append(l)
                i += 1
            # 首行 *（第N级或更高）* → prerequisite
            while body:
                m2 = _RUNE_PREREQ_RE.match(body[0].strip())
                if m2:
                    prereq = f"战士等级{m2.group(1)}级+"
                    body.pop(0)
                    continue
                break
            out.append({"name": name, "eng": eng, "prerequisite": prereq,
                        "body": _clean_body("\n".join(body)), "legacy": False})
            continue
        i += 1
    return out


def extract_pact_boons_phb2014(text: str) -> list[dict]:
    """PHB 2014 邪术师：`魔契恩泽Pact Boon` 特性后的 `#### 标题` 契约恩赐（3 条）。

    `#### 边栏：你的魔契恩泽 Your Pact Boon` 段排除。
    """
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if re.match(r"^魔契恩泽[A-Za-z]", s):
            break
        i += 1
    if i >= n:
        return []
    i += 1
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


def extract_pact_talisman_tce(text: str) -> list[dict]:
    """TCE 邪术师：`魔契恩泽选项 Pact Boon Option` 段后的护符魔契（1 条）。"""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        m = _NAKED_TITLE_RE.match(s)
        if m and _strip_eng(m.group(1)) == "魔契恩泽选项":
            break
        i += 1
    if i >= n:
        return []
    i += 1
    while i < n:
        s = lines[i].strip()
        m = _NAKED_TITLE_RE.match(s)
        if m and _split_eng(s)[0] == "符之魔契":
            name, eng = _split_eng(s)
            body: list[str] = []
            i += 1
            while i < n:
                l = lines[i].strip()
                if _NAKED_TITLE_RE.match(l) and len(l) < 60:
                    break
                body.append(l)
                i += 1
            return [{"name": name, "eng": eng, "prerequisite": "",
                     "body": _clean_body("\n".join(body)), "legacy": False}]
        i += 1
    return []


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
        elif rel.endswith("魔射手.md"):
            blocks = extract_arcane_shots_xge(text)
        elif rel.endswith("四象宗.md"):
            blocks = extract_elemental_disciplines_phb(text)
        elif rel.endswith("符文骑士.md"):
            blocks = extract_runes_tce(text)
        elif rel.endswith("邪术师.md"):
            blocks = extract_pact_boons_phb2014(text)
        elif rel.endswith("邪术师（TCE）.md"):
            blocks = extract_pact_talisman_tce(text)
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
    # 去重：同名+类型+来源（如注法「人工生命仆从」出现两处），保留正文最长
    dedup: dict[tuple, dict] = {}
    for r in rows:
        key = (r["name"], r["featureType"], r["source"])
        cur = dedup.get(key)
        if cur is None or len(r["entries"][0]) > len(cur["entries"][0]):
            dedup[key] = r
    deduped = list(dedup.values())
    if len(deduped) != len(rows):
        print(f"  [dedup] {len(rows)} → {len(deduped)} 条（同名条目合并）")
    return deduped


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

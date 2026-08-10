"""kb_tags.py — 5etools 内联标签清洗（纯函数）。

5etools 数据条目正文使用形如 {@spell 火球术|PHB}、{@damage 8d6}、{@dc 15} 的
内联标签语法。这些标签面向网页渲染（链接/掷骰器），直接交给 LLM 或打印会造成
噪音。本模块提供 clean_5etools_tags() 将标签降级为纯文本：

- 通用规则：取「|」分隔的首段作为显示文本（{@spell 火球术|PHB} → 火球术）。
- 特例表：{@dc}/{@hit}/{@h}/{@atk}/{@recharge}/{@chance} 有专门译文。
- {@table}/{@list} 展开为多行纯文本。
- 嵌套标签（如 {@dice 1d4 + {@damage 2d6}}）通过迭代替换处理。
- 未知标签取首段兜底，绝不抛出异常。

构建脚本（scripts/build_kb.py）与查询层共用本模块，保证清洗口径一致。
"""

from __future__ import annotations

import re

# 标签通用形态：{@tag 内容}，内容内不允许再出现花括号（嵌套由迭代处理）。
_TAG_RE = re.compile(r"\{@([A-Za-z]+)(?:\s+([^{}]*))?\}")

# 迭代轮数上限：正文嵌套深度通常 <= 3，20 轮足够；兜底后再做一次清理。
_MAX_PASSES = 20

# 攻击类型标记映射（{@atk mw,rw}）。
_ATK_MAP = {
    "mw": "近战武器攻击",
    "rw": "远程武器攻击",
    "ms": "近战法术攻击",
    "rs": "远程法术攻击",
    "m": "近战攻击",
    "r": "远程攻击",
}

# 2024 版攻击距离（{@atkr m|15}）与豁免动作（{@actSave dex}）映射。
_ATKR_MAP = {"m": "近战", "r": "远程"}
_SAVE_MAP = {
    "str": "力量豁免",
    "dex": "敏捷豁免",
    "con": "体质豁免",
    "int": "智力豁免",
    "wis": "感知豁免",
    "cha": "魅力豁免",
    "strdex": "力量或敏捷豁免",
    "strcon": "力量或体质豁免",
    "intwis": "智力或感知豁免",
    "all": "所有属性豁免",
}

# 名称类标签：语法为 {@tag 名称|来源|显示文本}，第三段是可选的显示覆盖。
# 只有这类标签才取第三段；数据类标签（damage/dice/dc/…）一律取首段。
_NAME_TAGS = frozenset(
    {
        "spell", "creature", "item", "condition", "class", "feat", "race",
        "background", "psionic", "deity", "language", "action", "reward",
        "object", "vehicle", "trap", "hazard", "variantrule",
        "optionalfeature", "sense", "status", "skill", "cult", "boon",
        "renown", "itemsearch",
    }
)

# 从显示文本中剥离的「[...]」限定（如「球状 [效应区域]」→「球状」）。
_BRACKET_QUALIFIER_RE = re.compile(r"\[[^\]]*\]")


def _display_text(tag: str, inner: str) -> str:
    """按标签类型决定显示文本。

    名称类标签优先取第三段（{@tag 名称|来源|显示文本}），并剥离「[...]」限定；
    其余标签取首段。保证 {@variantrule 球状 [效应区域]|XPHB|球状} → 球状，
    同时 {@scaledamage 8d6|3-9|1d6} 仍取首段 8d6。
    """
    parts = inner.split("|")
    if tag in _NAME_TAGS and len(parts) >= 3 and parts[2].strip():
        text = parts[2].strip()
    else:
        text = parts[0].strip()
    if tag in _NAME_TAGS:
        text = _BRACKET_QUALIFIER_RE.sub("", text).strip()
    return text


def _replace_tag(match: re.Match[str]) -> str:
    tag = match.group(1).lower()
    inner = (match.group(2) or "").strip()

    # --- 特例：无内容标签 ---
    if tag == "h":  # 命中（Hit）标题前缀
        return "命中："
    if tag == "actsavefail":  # 2024 版「豁免失败」
        return "豁免失败"
    if tag == "actsavesuccess":  # 2024 版「豁免成功」
        return "豁免成功"

    if not inner:
        return ""

    # --- 特例：数字类标签 ---
    if tag == "dc":
        return f"DC {_num(inner)}"
    if tag == "hit":
        raw = _num(inner)
        return f"+{raw}" if not raw.startswith("+") else raw
    if tag == "recharge":
        try:
            n = int(float(inner.split("|", 1)[0]))
        except (ValueError, TypeError):
            return inner
        return f"（充能 {n}–{min(n + 1, 6)}）"
    if tag == "chance":
        return f"{_num(inner)}%"

    # --- 特例：攻击类型 ---
    if tag == "atk":
        parts = [p.strip().lower() for p in inner.split(",")]
        names = [_ATK_MAP.get(p, p) for p in parts if p]
        return "或".join(names) if names else ""

    # --- 特例：2024 版攻击距离（{@atkr m|15} = 近战 触及 15 尺） ---
    if tag == "atkr":
        parts = [p.strip() for p in inner.split("|") if p.strip()]
        kind = _ATKR_MAP.get(parts[0].lower(), "") if parts else ""
        if len(parts) > 1 and parts[1].isdigit():
            kind = f"{kind}（触及{parts[1]}尺）"
        return kind

    # --- 特例：2024 版豁免动作（{@actSave dex} / 失败 / 成功） ---
    if tag == "actsave":
        key = inner.strip().lower()
        return _SAVE_MAP.get(key, inner.strip())

    # --- 特例：缩放伤害/骰（base|等级区间|每环增量） ---
    # 5etools 语法 {@scaledamage 10d4|4-9|2d4} = 基础伤害|适用等级|每环增量。
    # 该标签只出现在「升环施法」描述（“……伤害增加 X”），X 应为每环增量
    # （第三段），而非基础伤害（第一段）。scaledice 同构。
    if tag in ("scaledamage", "scaledice"):
        parts = [p.strip() for p in inner.split("|")]
        if len(parts) >= 3 and parts[2]:
            return parts[2]
        return parts[0] if parts else ""

    # --- 特例：表格展开为多行 ---
    if tag == "table":
        return _expand_table(inner)

    # --- 特例：列表展开为多行 ---
    if tag == "list":
        items = [s.strip() for s in inner.split("、") if s.strip()]
        if len(items) > 1:
            return "\n".join(items)
        return _display_text(tag, inner)

    # --- 通用：按标签类型取显示文本 ---
    return _display_text(tag, inner)


def _num(raw: str) -> str:
    """提取字符串开头的数字/符号数字部分（{@dc 15|…} 之类取第一段再剥离）。"""
    seg = raw.split("|", 1)[0].strip()
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)", seg)
    return match.group(1) if match else seg


def _expand_table(inner: str) -> str:
    """{@table 标题|表头、…|行、…|…} → 「标题：\n表头\n行…」多行文本。

    cells 的分隔符因条目而异（、 或 , ），统一按「|」切行，行内原样保留。
    """
    parts = [p.strip() for p in inner.split("|")]
    if not parts or not parts[0]:
        return ""
    title = parts[0]
    rows = [p for p in parts[1:] if p]
    lines = [f"{title}："]
    lines.extend(rows)
    return "\n".join(lines)


def _strip_leftover(text: str) -> str:
    """兜底：清除循环后仍残留的未嵌套 {@…} 标签。"""
    return _TAG_RE.sub(_replace_tag, text)


def clean_5etools_tags(text: str) -> str:
    """将 5etools 内联标签降级为纯文本。

    迭代替换处理嵌套标签；空段压缩；返回去除首尾空白后的文本。
    """
    if not text:
        return ""
    for _ in range(_MAX_PASSES):
        new = _TAG_RE.sub(_replace_tag, text)
        if new == text:
            break
        text = new
    if "{@" in text:
        text = _strip_leftover(text)
    # 压缩连续空行（保留段落分隔）。
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 2024 版写法「{@h}：」会与「命中：」合成双冒号，折叠为单个。
    text = text.replace("：：", "：")
    return text.strip()

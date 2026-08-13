"""source_map.py — 5e_chm 书目录 → 5etools source 码。

官方书映射到 5etools 标准码；第三方书用自定义码（仿既有惯例：
黯潮→ACT、胧忆岛→LOO、歪月→WYM，见 CONTEXT.md「来源码」）。
"""

from __future__ import annotations

# 官方书：目录名 → source 码
OFFICIAL_BOOKS: dict[str, str] = {
    "玩家手册": "PHB",
    "玩家手册2024": "XPHB",
    "珊娜萨的万事指南": "XGE",
    "塔莎的万事坩埚": "TCE",
    "剑湾冒险者指南": "SCAG",
    "拉尼卡公会长指南": "GGR",
    "艾伯伦：从终末战争中崛起": "ERLW",
    "艾伯伦：奇械锻炉": "EFA",
    "荒洲探险家指南": "EGW",
    "范·里希腾的鸦阁魔域指南": "VRGR",
    "龙枪：龙后之影": "DSotDQ",
    "巨人之荣耀": "BGG",
    "塞洛斯之神话奥德赛": "MOT",
    "斯翠海文：混沌研习": "STX",
    "印记城与外域": "SATO",
    "星界冒险者指南": "AAG",
    "启封奥秘": "AOF",
    "城主指南": "DMG",
    "城主指南2024": "XDMG",
    "艾奎兹玄有限责任公司": "RHW",
    "莫提的位面游记": "MPP",
    "贤者谏言2025": "SAC2025",
    "费资本的巨龙宝库": "FTFT",
    "鸦阁魔域：魔障深藏": "VRGR-V",
    "多元宇宙的怪物": "MPMM",
}

# 第三方书：目录名 → 自定义 source 码（与项目第三方法术惯例同风格）
THIRD_PARTY_BOOKS: dict[str, str] = {
    "吸血鬼：避世潜藏": "VTM",
    "斯坦哈德的诡怖猎杀指南": "SHO",
    "狮鹫的鞍中珍宝Ⅱ": "GTS",
    "歪曲之月": "WYM",
    "谦卑林": "HMW",
    "鬼魅幽谷": "GVG",
    "瓦尔达的秘密尖塔": "VDS",
    "探秘艾伯伦": "XER",
    "拳斗士": "BF",
    "血猎手": "BH",
    "邪狱使": "DF",
    "塔尔多雷": "TR",
    "火炬光下的克苏鲁": "THC",
    "黯潮之书": "ACT",
    "胧忆岛": "LOO",
    "德拉肯海姆": "DRK",
    "德城怪物": "DCM",
    "惊奇单次冒险": "ODA",
    "谦卑林故事集": "HMS",
    "万兽图志": "VBO",
}

# 全部「职业数据书」目录 = 官方 + 第三方
BOOK_SOURCES: dict[str, str] = {**OFFICIAL_BOOKS, **THIRD_PARTY_BOOKS}


def source_for(rel_path: str, base_dir: str = "md") -> str:
    """按 md 文件的相对路径推断 source 码。

    rel_path 形如「md/玩家手册/职业/战士.md」或「md/第三方/斯坦哈德…/…」。
    """
    parts = rel_path.replace("\\", "/").split("/")
    if parts and parts[0] == base_dir:
        parts = parts[1:]
    if not parts:
        return ""
    book = parts[0]
    if book == "第三方" and len(parts) > 1:
        third = parts[1]
        return THIRD_PARTY_BOOKS.get(third, f"3P-{third}")
    return BOOK_SOURCES.get(book, f"3P-{book}")

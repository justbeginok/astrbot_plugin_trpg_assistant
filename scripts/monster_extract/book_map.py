"""book_map.py — 5e_chm 书 → 5etools source 码映射（怪物全量重建用）。

官方书映射到 5etools source 码（以便 INSERT OR REPLACE 覆盖机翻）；
第三方书用中文书名（新增条目）。所有 `需验证` 标注的映射在 reconcile
阶段按名称重叠复核，重叠过低的会被改判为第三方（中文书名）。

格式族（"2024" / "2014" / "mtg"）由 inventory.py 按 md 结构自动识别，
不在此硬编码。
"""

from __future__ import annotations

# 顶层官方书 → (source 码, edition)。
# source=None 表示该书无怪物（规则书/速查/问答），inventory 会跳过其怪物提取。
BOOK_MAP: dict[str, tuple[str | None, str | None]] = {
    # --- 2024/2025 ---
    "怪物图鉴2025": ("XMM", "2024"),
    "被遗忘的国度": ("FRAiF", "2024"),   # 需验证
    "鸦阁魔域：魔障深藏": ("RHW", "2024"),  # 需验证
    "艾伯伦：奇械锻炉": ("ERLW", "2024"),   # 需验证（或 EKtW）
    "城主指南2024": ("XDMG", "2024"),
    "玩家手册2024": ("XPHB", "2024"),
    # --- 2014 ---
    "怪物图鉴": ("MM", "2014"),
    "多元宇宙的怪物": ("MPMM", "2014"),
    "瓦罗怪物指南": ("VGM", "2014"),
    "魔邓肯的众敌卷册": ("MTF", "2014"),
    "万象无常书": ("BMT", "2014"),
    "巨人之荣耀": ("BGG", "2014"),
    "费资本的巨龙宝库": ("FTD", "2014"),
    "布布的星界怪兽展": ("BAM", "2014"),
    "艾奎兹玄有限责任公司": ("AI", "2014"),
    "艾伯伦：从终末战争中崛起": ("ERLW", "2014"),
    "艾伯伦寻路者指南": ("WGtE", "2014"),  # 需验证
    "范·里希腾的鸦阁魔域指南": ("VRGR", "2014"),
    "荒洲探险家指南": ("EGW", "2014"),
    "印记城与外域": ("PaBTSO", "2014"),
    "剑湾冒险者指南": ("SCAG", "2014"),
    "星界冒险者指南": ("AAG", "2014"),   # 需验证（Spelljammer 盒装）
    "莫提的位面游记": ("MPP", "2014"),
    "龙枪：龙后之影": ("DSotDQ", "2014"),
    "城主指南": ("DMG", "2014"),
    "玩家手册": ("PHB", "2014"),
    "塔莎的万事坩埚": ("TCE", "2014"),
    "珊娜萨的万事指南": ("XGE", "2014"),
    # --- MTG ---
    "塞洛斯之神话奥德赛": ("MOT", "2014"),
    "拉尼卡公会长指南": ("GGR", "2014"),
    "斯翠海文：混沌研习": ("SCC", "2014"),
    # --- 无怪物（跳过）---
    "启封奥秘": (None, None),
    "速查": (None, None),
    "贤者谏言2025": (None, None),
}

# 模组子目录 → source 码（全部 2014）。
MODULE_MAP: dict[str, tuple[str | None, str | None]] = {
    "CR溟渊": ("CRCotN", "2014"),
    "冰塔峰": ("DIP", "2014"),
    "冰风谷": ("IDRotF", "2014"),
    "命运之轮": ("ToFW", "2014"),      # 需验证
    "哈欠门": ("TftYP", "2014"),
    "坠入塔莎洞": ("QftIS", "2014"),    # 需验证
    "坠入阿弗纳斯": ("BGDIA", "2014"),
    "夸力许": (None, None),            # 需核实内容
    "妖眼魔窟": (None, None),           # 需核实内容
    "寻找蛇蜥": (None, None),           # 需核实内容
    "巨龙僭政": ("HotDQ", "2014"),      # 需验证（Tyranny of Dragons 合集，或与龙后宝藏/提亚马特重复）
    "巨龙迷城": (None, None),           # 需核实内容
    "巫光": ("WBtW", "2014"),
    "提亚马特的崛起": ("RoT", "2014"),
    "施特拉德的诅咒": ("CoS", "2014"),
    "松溪险境": (None, None),           # 需核实内容
    "毁灭亲王": ("PotA", "2014"),
    "洛卡鱼人的崛起": ("GoS", "2014"),
    "深水城：疯法师": ("WDMM", "2014"),
    "深水城：龙金劫": ("WDH", "2014"),
    "湮灭之墓": ("ToA", "2014"),
    "炼狱机器": ("IMR", "2014"),        # 需验证
    "烛堡": ("CM", "2014"),            # 需验证
    "瑞克地城": (None, None),           # 需核实内容
    "盐沼幽魂": ("GoS", "2014"),
    "矿坑": ("LMoP", "2014"),
    "矿坑方尖碑": ("PaBTSO", "2014"),
    "红龙传说": ("LRDT", "2014"),       # 需验证
    "耀光城": ("JttRC", "2014"),
    "萨光": ("LoX", "2014"),
    "逃离深渊": ("OotA", "2014"),
    "风暴君王之雷霆": ("SKT", "2014"),
    "风骸岛": ("DoSI", "2014"),
    "黄金宝库": ("KftGV", "2014"),
    "龙后宝藏": ("HotDQ", "2014"),
    "龟人书": ("TTP", "2014"),          # 需验证
}

# 第三方书：source=中文书名（新增条目，不覆盖 5etools-cn）。
# edition 按格式族自动判定：2024 格式→"2024"，2014/MTG→"2014"。
THIRD_PARTY_BOOKS: tuple[str, ...] = (
    "万兽图志", "人人死", "吸血鬼：避世潜藏", "塔尔多雷", "德城怪物",
    "德拉肯海姆", "惊奇单次冒险", "拳斗士", "探秘艾伯伦",
    "斯坦哈德的诡怖猎杀指南", "歪曲之月", "火炬光下的克苏鲁",
    "狮鹫的鞍中珍宝Ⅱ", "瓦尔达的秘密尖塔", "胧忆岛", "血猎手",
    "谦卑林", "谦卑林故事集", "邪狱使", "鬼魅幽谷", "黯潮之书",
)

# 特殊父目录：DNDBeyond 与 其他 是混合目录，按子目录单独处理。
# 这些子目录一律用中文书名作 source（新增，不覆盖），除非在此显式映射。
DNDBEYOND_MAP: dict[str, tuple[str | None, str | None]] = {
    # 怪物纲要 / 邪物卷册 / 错位怪物 / 侠盗荣耀 等，逐步核实
    "怪物纲要1": ("MCV1SC", "2014"),  # 需验证（法术船生物）
    "怪物纲要2": ("MCV2DC", "2014"),  # 需验证
    "怪物纲要3": ("MCV3MC", "2014"),  # 需验证
    "怪物纲要4": ("MCV4EC", "2014"),  # 需验证
    "邪物卷册1": ("MFF", "2014"),     # Mordenkainen's Fiendish Folio，需验证
    "错位怪物1": ("MisMV1", "2014"),  # Misplaced Monsters Vol 1，需验证
    "侠盗荣耀": ("HAT-TG", "2014"),   # Honor Among Thieves，需验证
}


def resolve_book(name: str) -> tuple[str | None, str | None]:
    """顶层书 → (source, edition)。未知书返回 (None, None) 表示跳过。"""
    return BOOK_MAP.get(name, (None, None))


def resolve_module(name: str) -> tuple[str | None, str | None]:
    return MODULE_MAP.get(name, (None, None))


def is_third_party(name: str) -> bool:
    return name in THIRD_PARTY_BOOKS


def resolve_dndbeyond(name: str) -> tuple[str | None, str | None]:
    return DNDBEYOND_MAP.get(name, (None, None))

"""inventory.py — 扫描 5e_chm/md 全库职业/子职文件，生成解析计划。

只处理「正式书」目录（官方 + 第三方）；跳过 其他/（UA/Plane Shift 历史内容，
5etools-cn 已有）、模组/、速查/、DNDBeyond/ 与纯怪物书。

判定 kind：
  class          职业主体（含本职特性）
  class_options  可选职业特性（TCE「职业（TCE）.md」）
  subclass       子职（目录/文件名/特例表推断）
  multi          单文件含多个子职（拉尼卡/鸦阁 子职选项）
  skip           风味介绍/指引/非特性（SCAG 职业主体、拉尼卡公会职业）
  unknown        无法判定 → LLM 兜底
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .source_map import BOOK_SOURCES, source_for

CLASS_NAMES = {
    "野蛮人", "吟游诗人", "牧师", "德鲁伊", "战士", "武僧", "圣武士",
    "游侠", "游荡者", "术士", "邪术师", "魔契师", "法师", "奇械师",
    "秘术师", "血族", "铳士", "拳斗士", "血猎手", "邪狱使",
}

# 文件名 → 所属职业特例（剑湾 剑咏.md 等文件名不含职业名）
FILENAME_CLASS_OVERRIDE = {
    "剑咏": "法师",
    "紫龙骑士": "战士",
    "日魂宗": "武僧",
    "永亡宗": "武僧",
    "风暴术法": "术士",
    "策士": "游荡者",
    "风流剑客": "游荡者",
    "王冠之誓": "圣武士",
    "不朽者": "邪术师",
    "战狂": "野蛮人",
    "图腾武者_剑湾追加": "野蛮人",
    "奥秘领域": "牧师",
}

# 选项列表类文件名（非独立子职/职业，是 entries 选项或 optionalfeature）
OPTION_LIST_STEMS = {
    "战技选项", "战斗大师构筑", "驯兽师伙伴", "魔能祈唤", "魔能祈唤选项",
    "超魔法选项", "学习野兽形态", "战技项", "血咒",
}

# 跳过整个目录：非职业数据书
SKIP_DIRS = {
    "DNDBeyond", "template", "template2", "空白页模板",
    "其他", "模组", "速查", "被遗忘的国度", "写在前面", "分隔符",
    "怪物图鉴", "怪物图鉴2025", "多元宇宙的怪物", "瓦罗怪物指南",
    "魔邓肯的众敌卷册", "布布的星界怪兽展", "旧版说明", "更新日志",
    "贤者谏言2025",  # 官方答疑书，非职业数据
}

# 明确职业数据书目录（官方 + 第三方），其余目录不扫
CLASS_BOOK_DIRS = set(BOOK_SOURCES.keys())


@dataclass
class PlanItem:
    path: Path
    book: str
    source: str
    kind: str = "unknown"
    class_name: str = ""
    subclass_name: str = ""


def _rel_book(path: Path, md_root: Path) -> str:
    rel = path.relative_to(md_root)
    parts = rel.parts
    if not parts:
        return ""
    return parts[1] if parts[0] == "第三方" else parts[0]


def _infer_from_name(stem: str) -> tuple[str, str] | None:
    """文件名推断 (class, subclass)；失败返回 None。"""
    if "-" in stem:
        cls, sub = stem.split("-", 1)
        if cls in CLASS_NAMES:
            return cls, sub.strip()
    if stem in CLASS_NAMES:
        return stem, ""
    if stem in FILENAME_CLASS_OVERRIDE:
        return FILENAME_CLASS_OVERRIDE[stem], stem
    return None


def scan(md_root: Path) -> list[PlanItem]:
    plan: list[PlanItem] = []
    for path in sorted(md_root.rglob("*.md")):
        rel = path.relative_to(md_root)
        parts = rel.parts
        stem = path.stem
        if not parts:
            continue
        book = parts[1] if parts[0] == "第三方" else parts[0]
        if book in SKIP_DIRS:
            continue
        if book not in CLASS_BOOK_DIRS:
            continue
        src = source_for(str(rel))
        # 职业法术列表路径跳过（非特性）
        if "法术列表" in str(rel):
            continue
        # 关键词过滤：只处理职业数据相关路径（统一 / 分隔符）
        path_str = str(rel).replace("\\", "/")
        kw_hit = any(k in path_str for k in (
            "职业", "子职", "血族", "铳士", "拳斗士", "血猎手", "邪狱使",
            "角色选项", "角色职业",
        ))
        # 顶层职业主体文件（书根目录下 战士.md / 战士（TCE）.md 等）也命中
        stem_in_class = stem in CLASS_NAMES or any(
            s in stem for s in CLASS_NAMES if len(s) >= 2
        )
        if not kw_hit and not stem_in_class:
            continue
        # 非职业路径跳过：种族/背景/专长/赠礼/戏法/装备/生物
        if any(k in path_str for k in (
            "角色选项/种族", "角色选项/背景", "角色选项/专长",
            "角色选项/超自然赠礼", "角色选项/赠礼", "角色选项/戏法",
            "角色选项/装备", "角色选项/魔法物品",
        )):
            continue

        # 选项列表类文件（战技选项/魔能祈唤等）→ 跳过（非独立子职）
        if stem in OPTION_LIST_STEMS:
            continue
        # ERLW 第一章/奇械师.md = 纯设定文本（TCE 已重置奇械师）→ skip
        if book == "艾伯伦：从终末战争中崛起" and len(parts) >= 2 \
                and parts[-2] == "第一章" and stem == "奇械师":
            plan.append(PlanItem(path, book, src, "skip", "奇械师", ""))
            continue
        # 铳士目录：铳士.md=简介(skip)；铳士职业.md=class；白名单子职；其余跳过
        GUNSLINGER_SUBS = {"密间客", "技枪客", "死眼客", "白帽客", "豪赌客", "魔弹客"}
        if len(parts) >= 2 and parts[-2] == "铳士" and book == "瓦尔达的秘密尖塔":
            if stem == "铳士":
                plan.append(PlanItem(path, book, src, "skip", "铳士", ""))
            elif stem == "铳士职业":
                plan.append(PlanItem(path, book, src, "class", "铳士", ""))
            elif stem in GUNSLINGER_SUBS:
                plan.append(PlanItem(path, book, src, "subclass", "铳士", stem))
            # 专长/枪械/法术/词条等附属内容 → 跳过
            continue
        # ---- 目录判定：血族职业/ → 血族子职（血族.md 是职业主体）----
        if len(parts) >= 2 and parts[-2] == "血族职业":
            if stem == "血族":
                plan.append(PlanItem(path, book, src, "class", "血族", ""))
            else:
                plan.append(PlanItem(path, book, src, "subclass", "血族", stem))
            continue

        # ---- 目录判定：职业目录下的 职业名/子职.md（官方 PHB/XGE/SCAG；角色职业 走下方 2024 分支）----
        if len(parts) >= 3 and parts[-3] in ("职业", "角色选项", "玩家选项"):
            cls = parts[-2]
            if "（" in cls and "）" in cls:
                cls = cls.split("（")[0]  # TCE「战士（TCE）」
            if cls in CLASS_NAMES:
                plan.append(PlanItem(path, book, src, "subclass", cls, stem))
                continue

        # ---- 目录判定：2024 角色职业/职业/子职.md（职业.md 是职业主体）----
        if len(parts) >= 3 and parts[-2] in CLASS_NAMES and parts[-3] == "角色职业":
            if stem == parts[-2]:
                plan.append(PlanItem(path, book, src, "class", parts[-2], ""))
            else:
                plan.append(PlanItem(path, book, src, "subclass", parts[-2], stem))
            continue

        # ---- SCAG 职业主体 = 风味介绍（skip），其子职走文件名 ----
        if book == "剑湾冒险者指南" and len(parts) >= 2 and parts[-2] == "职业" \
                and "-" not in stem and stem not in FILENAME_CLASS_OVERRIDE:
            plan.append(PlanItem(path, book, src, "skip", stem, ""))
            continue

        # ---- TCE 职业（TCE）.md = 可选职业特性 ----
        if book == "塔莎的万事坩埚" and "（TCE）" in stem \
                and stem not in CLASS_NAMES and "（TCE）" not in stem[:0]:
            cls = stem.split("（")[0]
            if cls in CLASS_NAMES:
                plan.append(PlanItem(path, book, src, "class_options", cls, ""))
                continue

        # ---- 拉尼卡 ----
        if book == "拉尼卡公会长指南":
            if stem == "子职选项":
                plan.append(PlanItem(path, book, src, "multi", "", ""))
            elif stem == "公会职业":
                plan.append(PlanItem(path, book, src, "skip", "", ""))
            else:
                infer = _infer_from_name(stem)
                if infer:
                    plan.append(PlanItem(
                        path, book, src, "subclass", infer[0], infer[1]))
            continue

        # ---- 文件名推断（职业名.md / 职业-子职.md / 职业：子职.md）----
        infer = _infer_from_name(stem)
        if infer:
            cls, sub = infer
            kind = "class" if not sub else "subclass"
            # XGE/SCAG 角色选项/职业名.md 是「新增子职简介」：
            # 同目录存在 职业名/ 子目录 → 该文件是索引，跳过（子职在子目录）
            if not sub and len(parts) >= 2 and parts[-2] in ("角色选项",):
                sibling_dir = path.parent / cls
                if sibling_dir.is_dir():
                    plan.append(PlanItem(path, book, src, "skip", cls, ""))
                    continue
            plan.append(PlanItem(path, book, src, kind, cls, sub))
            continue
        # 「职业：子职」格式（塞洛斯等）：吟游诗人：雄辩学院.md
        if "：" in stem and len(parts) > 1:
            cls, _, sub = stem.partition("：")
            if cls in CLASS_NAMES and sub:
                plan.append(PlanItem(path, book, src, "subclass", cls, sub))
                continue

        # ---- 多子职合体文件 ----
        if stem in ("子职选项", "职业与子职", "第三章：子职"):
            plan.append(PlanItem(path, book, src, "multi", "", ""))
            continue

        plan.append(PlanItem(path, book, src, "unknown", "", ""))
    return plan

# -*- coding: utf-8 -*-
"""gen_enrich.py — 为 5e_chm 法术生成富化字段（summary + spell_keyword）初版。

背景：kb_patches/spell_enrich.json 现有 554 条（AI 生成，保留不动）。本次 md 重建
新增 ~701 条法术无富化。本脚本基于详述正文做规则/启发式生成，保证 100% 覆盖，
质量靠规则抽取准确性，明显差的条目由人工/AI 精修。

用法：
    python gen_enrich.py --spells scripts/_md_cache/spells_chm.json \
        --enrich kb_patches/spell_enrich.json \
        --out kb_patches/spell_enrich.json   # 原地合并写回

输出：合并后的完整 spell_enrich.json（现有 554 条 + 缺口生成）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 伤害类型（canonical，正文「X伤害」匹配）
DAMAGE_WORDS = [
    "强酸", "寒冷", "火焰", "闪电", "雷鸣", "力场", "暗蚀", "心灵",
    "毒素", "光耀", "死灵", "钝击", "穿刺", "挥砍",
]
RE_DAMAGE = re.compile("(" + "|".join(DAMAGE_WORDS) + ")伤害")

# 状态（正文「陷入X」「X状态」匹配）
CONDITIONS = [
    "目盲", "耳聋", "魅惑", "恐慌", "中毒", "束缚", "麻痹", "石化",
    "震慑", "失能", "昏迷", "恐惧", "疲乏", "力竭", "受缚",
]
RE_COND = re.compile("陷入(" + "|".join(CONDITIONS) + ")")

# 形状
SHAPES = ["锥形", "球状", "柱形", "线形", "立方", "圆形", "弧形"]
RE_SHAPE = re.compile("(" + "|".join(SHAPES) + ")")

# 语义大类关键词（词表内为主，自由词补充）。注意「法术/施法/攻击」等词
# 在正文普遍出现，不作为大类信号（否则 施法辅助/战斗辅助 泛滥无区分度）。
SEMANTIC_RULES: list[tuple[str, list[str]]] = [
    ("治疗", ["治疗", "恢复生命", "回复生命", "生命值恢复", "治愈", "愈合", "疗伤"]),
    ("召唤", ["召唤", "咒唤", "唤起", "召唤出"]),
    ("位移", ["传送", "瞬移", "闪现", "传送门", "消失"]),
    ("潜行", ["隐形", "潜行", "隐匿"]),
    ("防护", ["护盾", "护甲", "防御", "减伤", "吸收伤害", "护罩", "屏障", "门", "锁"]),
    ("侦查", ["侦测", "探知", "探查", "感知", "识破", "感测", "占卜", "预知", "预言"]),
    ("控场", ["魅惑", "束缚", "麻痹", "震慑", "昏迷", "目盲", "恐慌", "定身", "控制"]),
    ("减益", ["削弱", "诅咒", "降咒", "力竭", "中毒", "弱化", "厄运"]),
    ("增益", ["增益", "强化", "加值", "祝福", "免疫", "抗性", "优势"]),
    ("伤害", ["造成", "额外伤害", "受到", "点伤害"]),
    ("即死", ["即死", "瞬间死亡", "直接死亡", "死亡豁免"]),
    ("造物", ["创造", "制造", "生成", "造出", "变出", "物质化"]),
    ("探索", ["探索", "地图", "搜寻", "寻路", "方向感", "黑暗"]),
    ("社交", ["说服", "唬骗", "威吓", "魅力检定", "魅力"]),
    ("幻术", ["幻影", "幻象", "幻觉", "虚假"]),
    ("火焰", ["火焰", "烈焰", "燃烧", "火球", "火墙"]),
    ("冰霜", ["冰霜", "寒冷", "冻伤", "冻结", "冰"]),
    ("闪电", ["闪电", "雷电", "电弧", "雷暴"]),
    ("暗蚀", ["暗蚀", "死亡能量", "凋零"]),
    ("心灵", ["心灵", "精神", "心智"]),
    ("亡灵", ["亡灵", "不死", "僵尸", "骷髅", "幽灵", "尸"]),
    ("变身", ["变身", "变形", "变作", "变化为", "化为"]),
    ("飞行", ["飞行", "翱翔"]),
    ("元素", ["元素", "土元素", "火元素", "水元素", "气元素"]),
    ("野兽", ["野兽", "动物", "兽"]),
]

# 无关键词时的学派兜底（保证 spell_keyword 100% 覆盖）
SCHOOL_FALLBACK_KEYWORD = {
    "防护": "防护", "咒法": "召唤", "预言": "侦查", "惑控": "控场",
    "塑能": "伤害", "幻术": "幻术", "死灵": "亡灵", "变化": "变身",
}


def _first_sentence(text: str, limit: int = 90) -> str:
    """取正文第一句（含伤害/状态信息优先），截断到 limit。"""
    t = re.sub(r"\s+", "", text).strip()
    if not t:
        return ""
    for sep in ("。", "；", "。"):
        idx = t.find(sep)
        if 0 < idx <= limit:
            t = t[: idx + 1]
            break
    if len(t) > limit:
        t = t[:limit] + "…"
    return t


def gen_summary(rec: dict) -> str:
    """概要：首句（引导句时合并下一句），截断 90 字；无详述用元数据兜底。"""
    detail = re.sub(r"\s+", "", rec.get("detail") or "").strip()
    if not detail:
        lvl = "戏法" if rec.get("level") == 0 else f"{rec.get('level')}环"
        return f"{lvl}{rec.get('school') or ''}法术。"
    sentences = [s for s in re.split(r"(?<=[。！？])", detail) if s.strip()]
    s = sentences[0]
    # 引导句（选择/指定/当…时/以…）信息不完整 → 合并下一句
    if (len(s) < 12 or re.search(r"^(选择|指定|当|以|用|你(?:能|可以|们)?(?:选择|指定|可以))", s)) \
            and len(sentences) > 1:
        s += sentences[1]
    if len(s) > 90:
        s = s[:90] + "…"
    return s


def gen_keywords(rec: dict) -> list[str]:
    """关键字：伤害/状态/形状 + 语义大类 + 主题词，去重保序。"""
    detail = rec.get("detail") or ""
    kws: list[str] = []
    for d in re.findall(RE_DAMAGE, detail):
        if d not in kws:
            kws.append(d)
    for c in re.findall(RE_COND, detail):
        c = c[0] if isinstance(c, tuple) else c
        if c not in kws:
            kws.append(c)
    for sh in re.findall(RE_SHAPE, detail):
        if sh not in kws:
            kws.append(sh)
    for kw, pats in SEMANTIC_RULES:
        if any(p in detail for p in pats):
            if kw not in kws:
                kws.append(kw)
    # 兜底：规则未命中时给学派大类词，保证 100% 覆盖
    if not kws:
        fb = SCHOOL_FALLBACK_KEYWORD.get(rec.get("school") or "")
        if fb:
            kws.append(fb)
    return kws


def main() -> int:
    ap = argparse.ArgumentParser(description="为 5e_chm 法术生成富化初版")
    ap.add_argument("--spells", required=True, help="spells_chm.json")
    ap.add_argument("--enrich", required=True, help="现有 spell_enrich.json")
    ap.add_argument("--out", required=True, help="输出（合并后）")
    args = ap.parse_args()

    spells = json.loads(Path(args.spells).read_text(encoding="utf-8"))
    enrich = json.loads(Path(args.enrich).read_text(encoding="utf-8"))
    existing = {(e.get("name"), e.get("source"), e.get("edition")) for e in enrich}

    added = 0
    empty_summary = 0
    for s in spells:
        key = (s["name"], s["source_5e"], s["edition"])
        if key in existing:
            continue
        summary = gen_summary(s)
        kws = gen_keywords(s)
        if not summary.strip():
            empty_summary += 1
        enrich.append({
            "name": s["name"],
            "source": s["source_5e"],
            "edition": s["edition"],
            "summary": summary,
            "keywords": kws,
        })
        added += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(enrich, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[gen_enrich] 新增 {added} 条 → {out} (总计 {len(enrich)}) | 空概要 {empty_summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

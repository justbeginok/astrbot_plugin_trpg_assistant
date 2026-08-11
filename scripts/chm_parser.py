# -*- coding: utf-8 -*-
"""chm_parser.py — 解析 5e_chm Markdown 法术数据源（纯函数，无副作用）。

数据源：C:\\Users\\75957\\WorkBuddy\\可爱骰娘\\5e_chm\\md（htm_to_md.py 转换产物，人工校对中文）
- 速查表：速查/法术速查/5E万法大全.md（官方 936 条）+ 合作方万法大全.md（第三方 284 条）
- 详述页：按环阶打包（玩家手册2024/法术详述/{0..9环}.md 等）+ 散落单文件 + 第三方目录

用法：
    python chm_parser.py --chm-md <md根目录> --out <输出json>

产出（spells_chm.json，list[dict]）每条：
    name/eng_name/source(表格原始码)/source_5e(5etools码)/edition/level/school/
    classes(中文职业名列表)/time(施法时间)/components{}/ritual/concentration/
    aliases(双拼名别名)/detail(详述正文)/detail_meta/detail_time/detail_range/
    detail_components/detail_duration/detail_higher/detail_source/has_detail/body
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量表
# ---------------------------------------------------------------------------

# 万法大全职业简写 → 中文职业全名
CLASS_SHORT_CN = {
    "械": "奇械师",
    "诗": "吟游诗人",
    "牧": "牧师",
    "德": "德鲁伊",
    "帕": "圣武士",
    "软": "游侠",
    "术": "术士",
    "法": "法师",
    "锁": "魔契师",
    "无": "",  # 无职
}

# 详述页职业全名（2014 详述元数据里的全名，去重后写入 classes）
CLASS_FULL_CN = {
    "奇械师", "吟游诗人", "牧师", "德鲁伊", "圣武士", "游侠",
    "术士", "法师", "魔契师",
}

# 学派中文全集
SCHOOLS = {"防护", "咒法", "预言", "惑控", "塑能", "幻术", "死灵", "变化"}

# 环阶中文 → 数字（0 = 戏法）
LEVEL_CN = {
    "戏法": 0, "零环": 0, "0环": 0,
    "一环": 1, "1环": 1, "二环": 2, "2环": 2, "三环": 3, "3环": 3,
    "四环": 4, "4环": 4, "五环": 5, "5环": 5, "六环": 6, "6环": 6,
    "七环": 7, "7环": 7, "八环": 8, "8环": 8, "九环": 9, "9环": 9,
}

# chm 表格来源码 → (5etools 来源码, edition)
CHM_SOURCE_MAP = {
    "PHB24": ("XPHB", "2024"),
    "PHB14": ("PHB", "2014"),
    "XGE": ("XGE", "2014"),
    "TCE": ("TCE", "2014"),
    "FTD": ("FTD", "2014"),
    "BMT": ("BMT", "2014"),
    "GGR": ("GGR", "2014"),
    "AI": ("AI", "2014"),
    "EGW": ("EGW", "2014"),
    "SCC": ("SCC", "2014"),
    "AAG": ("AAG", "2014"),
    "SO": ("SatO", "2014"),
    "FR": ("FRHoF", "2014"),
    "EFA": ("EFA", "2014"),
    "夸力许": ("LLK", "2014"),
    "冰风谷": ("IDRotF", "2014"),
}

# 第三方来源（合作方万法大全表格值）→ 来源码。表格值为准，audit 会列出未映射项。
THIRD_PARTY_SOURCE_MAP = {
    "黯潮": "ACT",          # 黯潮之书
    "胧忆岛": "LOO",
    "歪月": "WYM",          # 歪曲之月
    "克苏鲁": "CTH",        # 火炬光下的克苏鲁
    "谦卑林战役": "HML",
    "谦卑林故事": "HMT",
    "塔尔多雷": "TAL",
    "德城": "DEC",          # 德城怪物
    "邪狱使": "DHB",
    "尖塔1": "VAT1",        # 瓦尔达的秘密尖塔
    "尖塔2": "VAT2",
    "铳士": "GNR",          # 铳士（瓦尔达扩展）
    "探秘艾伯伦": "EXE",
    "斯坦哈德": "SGH",      # 斯坦哈德的诡怖猎杀指南
    "鬼谷14": "GHV",        # 鬼魅幽谷
    "艾巢": "AIC",          # 艾伯伦：终末战争 巢穴类（待审计）
    "德拉肯海姆": "DRA",
}

# 官方详述文件（相对 md 根）→ 来源码。环阶文件用 {lvl} 占位（戏法=0）。
DETAIL_SOURCE_FILES: list[tuple[str, str]] = [
    ("玩家手册2024/法术详述", "PHB24"),
    ("玩家手册/魔法/法术详述", "PHB14"),
    ("珊娜萨的万事指南/法术/法术详述", "XGE"),
    ("塔莎的万事坩埚/法术/法术详述", "TCE"),
    ("被遗忘的国度/费伦英雄/第五章/新法术.md", "FR"),
    ("荒洲探险家指南/角色选项/秘迹学法术/秘迹学法术详述.md", "EGW"),
    ("费资本的巨龙宝库/玩家选项/巨龙法术详述.md", "FTD"),
    ("艾奎兹玄有限责任公司/玩家选项/新法术详述.md", "AI"),
    ("斯翠海文：混沌研习/玩家选项/法术详述.md", "SCC"),
    ("万象无常书/贤者/卡牌法术详述.md", "BMT"),
    ("模组/夸力许/新法术.md", "夸力许"),
    ("模组/冰风谷/新法术.md", "冰风谷"),
    ("印记城与外域/第一章/新法术.md", "SO"),
    ("星界冒险者指南/新法术.md", "AAG"),
    ("拉尼卡公会长指南/思想编码.md", "GGR"),
    ("艾伯伦：奇械锻炉/第一章/法术.md", "EFA"),
]

# 环阶文件名：2024 用「0环」、2014 用「戏法」，两种都要试
LEVEL_FILE_NAMES = ["0环", "戏法", "1环", "2环", "3环", "4环", "5环", "6环", "7环", "8环", "9环"]

# 详述元数据行：*三环塑能（术士、法师）*施法时间：1 动作
#               *塑能戏法（吟游诗人、术士、魔契师、法师）*施法时间：动作
#               *三环死灵（仪式；吟游诗人、牧师、德鲁伊、法师）*施法时间：1 动作（2014 含仪式标记）
#               *三环死灵（吟游诗人、法师）\n*施法时间：10分钟（部分文件斜体标记不成对）
_META_TIME_RE = re.compile(
    r"^\*(?P<meta>[^*\n]+?)\*?(?:\s*\n\s*)?\*?\s*施法时间：?(?P<time>.+?)\s*$", re.M
)
# 元数据内部：环阶+学派+（职业）或（仪式；职业）
_META_BODY_RE = re.compile(
    r"^(?P<lvl>(?:[零一二三四五六七八九0-9]环|戏法))?"
    r"(?P<school>防护|咒法|预言|惑控|塑能|幻术|死灵|变化)?"
    r"（(?P<cls>[^）]*)）"
)
# 标题：#### 火球术｜Fireball / #### 火球术｜Fireball（带空格）
_DETAIL_TITLE_RE = re.compile(r"^#+\s*(?P<zh>.+?)\s*[｜|]\s*(?P<en>.+?)\s*$")
# 速查表名字列：中文名English（无空格）或 中文名 English。
# en 允许拉丁重音字符（Séance）、/（双拼名 Antipathy/Sympathy）、空格。
_QUICK_NAME_RE = re.compile(r"^(?P<zh>.+?)(?P<en>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9 '’'\-\./]*)$")
# 元数据属性行（re.M：跨行定位并删除）
_RANGE_RE = re.compile(r"^施法距离：(.+)$", re.M)
_COMPONENTS_RE = re.compile(r"^法术成分：(.+)$", re.M)
_DURATION_RE = re.compile(r"^持续时间：(.+)$", re.M)


def _split_tds(tr_line: str) -> list[str]:
    """从 <tr>...</tr> 行提取所有 <td> 内容（含 <th>）。"""
    return re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_line, flags=re.S)


def _decode_classes_short(raw: str) -> list[str]:
    """解码速查表职业简写（如 德软术法械 → [德鲁伊,游侠,术士,法师,奇械师]）。"""
    out: list[str] = []
    for ch in raw:
        cn = CLASS_SHORT_CN.get(ch)
        if cn:
            out.append(cn)
    return out


def _decode_classes_full(raw: str) -> list[str]:
    """解析详述页职业（顿号/逗号分隔，可能混入 仪式/TCE：/子职 等标记段）。

    只保留已知标准职业名（CLASS_FULL_CN）；含冒号的附加来源注记
    （TCE：吟游诗人）、仪式/子职标记段一律丢弃。
    """
    out: list[str] = []
    for tok in re.split(r"[；;、，,]", raw):
        tok = tok.strip()
        if not tok or "：" in tok or ":" in tok:
            continue
        if tok in CLASS_FULL_CN and tok not in out:
            out.append(tok)
    return out


def _eng_key(name: str) -> str:
    """英文名归一化 join 键：小写 + 去非字母数字（容忍源数据漏空格等瑕疵）。"""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _parse_quick_name(raw: str) -> tuple[str, str]:
    """拆分速查表名字列「中文名English」→ (中文名, 英文名)。"""
    m = _QUICK_NAME_RE.match(raw)
    if not m:
        return raw, ""
    return m.group("zh").strip(), m.group("en").strip()


def parse_quick_table(text: str) -> list[dict]:
    """解析速查表（万法大全/合作方万法大全）→ 速查记录列表。

    每行 23 个 td，数据列索引 1,3,5,...,21（奇数位）。
    """
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("<tr>"):
            continue
        tds = _split_tds(line)
        if len(tds) != 23:
            continue  # 跳过表头（「法术名」行也 23 列，靠名字校验过滤）
        name_raw = tds[1].strip()
        if not name_raw or name_raw == "法术名":
            continue
        zh, en = _parse_quick_name(name_raw)
        if not zh:
            continue
        lvl_raw = tds[3].strip()
        school = tds[5].strip()
        cls_raw = tds[7].strip()
        time_cn = tds[9].strip()
        vocal = tds[11].strip()
        somatic = tds[13].strip()
        material = tds[15].strip()
        ritual_raw = tds[17].strip()
        conc_raw = tds[19].strip()
        source_raw = tds[21].strip()
        # 成分列：言语 V、姿势 S、材料 M/M*
        comps = {
            "v": vocal == "V",
            "s": somatic == "S",
            "m": material in ("M", "M*"),
            "costly": material == "M*",
        }
        aliases: list[str] = []
        main_zh = zh
        if "/" in zh:
            parts = [p.strip() for p in zh.split("/") if p.strip()]
            main_zh = parts[0]
            aliases = parts[1:]
        rows.append({
            "name": main_zh,
            "aliases": aliases,
            "eng_name": en,
            "source": source_raw,
            "level": LEVEL_CN.get(lvl_raw, -1),
            "school": school if school in SCHOOLS else "",
            "classes": _decode_classes_short(cls_raw),
            "time": time_cn,
            "components": comps,
            "ritual": ritual_raw == "√",
            "concentration": conc_raw == "√",
        })
    return rows


def _split_detail_blocks(text: str) -> list[tuple[str, str]]:
    """把详述文件按 `#### 中文名｜English` 标题切块 → [(标题行, 块体)]。"""
    blocks: list[tuple[str, str]] = []
    cur_title: str | None = None
    cur_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#### "):
            if cur_title is not None:
                blocks.append((cur_title, "\n".join(cur_lines)))
            cur_title = line
            cur_lines = []
        elif cur_title is not None:
            # 遇到更高层级标题（### 等）或文件尾部，当前块结束
            if line.startswith("###"):
                blocks.append((cur_title, "\n".join(cur_lines)))
                cur_title = None
                cur_lines = []
            else:
                cur_lines.append(line)
    if cur_title is not None:
        blocks.append((cur_title, "\n".join(cur_lines)))
    return blocks


def _strip_html_table(body: str) -> str:
    """把残留 <table> 转成纯文本行（td 内容以 | 连接），保证正文可读。"""
    lines: list[str] = []

    def _repl_table(m: re.Match) -> str:
        rows_txt: list[str] = []
        for tr in re.findall(r"<tr>(.*?)</tr>", m.group(0), flags=re.S):
            cells = _split_tds(tr)
            cells = [c.strip() for c in cells if c.strip()]
            if cells:
                rows_txt.append(" | ".join(cells))
        return "\n" + "\n".join(rows_txt) + "\n" if rows_txt else ""

    # 逐 table 替换
    out = re.sub(r"<table>.*?</table>", _repl_table, body, flags=re.S)
    # 清理残留的孤立标签（防漏网）
    out = re.sub(r"</?(?:tr|td|th|table)[^>]*>", "", out)
    return out


def _clean_links(body: str) -> str:
    """站内链接降级：*寻获魔宠find familiar* → 寻获魔宠（find familiar）。"""
    out = re.sub(r"\*([^*]+?)([A-Za-z][A-Za-z0-9 ']*) \*\*", r"\1（\2）", body)
    out = re.sub(r"\*([^*]+?)([A-Za-z][A-Za-z0-9 ']*)\*", r"\1（\2）", out)
    return out


def parse_detail_file(text: str, source_chm: str) -> list[dict]:
    """解析单个详述文件 → 详述记录列表。"""
    out: list[dict] = []
    for title_line, body in _split_detail_blocks(text):
        tm = _DETAIL_TITLE_RE.match(title_line)
        if not tm:
            continue
        zh = tm.group("zh").strip()
        en = tm.group("en").strip()
        # 1) 提取元数据块（容忍 *meta* 与 施法时间 之间换行），并从正文删除
        meta_raw = ""
        time_raw = ""
        mm = _META_TIME_RE.search(body)
        if mm:
            meta_raw = mm.group("meta").strip()
            time_raw = mm.group("time").strip()
            body = body[: mm.start()] + body[mm.end() :]
        # 2) 提取距离/成分/持续时间属性行
        range_raw = comp_raw = dur_raw = ""
        for key, pat in (("range_raw", _RANGE_RE), ("comp_raw", _COMPONENTS_RE), ("dur_raw", _DURATION_RE)):
            m = pat.search(body)
            if m:
                if key == "range_raw":
                    range_raw = m.group(1).strip()
                elif key == "comp_raw":
                    comp_raw = m.group(1).strip()
                else:
                    dur_raw = m.group(1).strip()
                body = body[: m.start()] + body[m.end() :]
        # 3) 剩余文本按段处理：升环施法段 + 正文
        rest: list[str] = []
        for ln in body.splitlines():
            stripped = ln.strip()
            if stripped:
                rest.append(stripped)
        higher = ""
        body_parts: list[str] = []
        for ln in rest:
            if ln.startswith("升环施法"):
                higher = ln
            elif higher:
                higher += "\n" + ln
            else:
                body_parts.append(ln)
        detail_text = "\n\n".join(body_parts).strip()
        # 4) 元数据块解析：环阶/学派/职业（兼容 2014 仪式标记「（仪式；职业）」与
        #    「（职业；TCE：职业）」附加来源注记）
        level = -1
        school = ""
        classes: list[str] = []
        if meta_raw:
            mm = _META_BODY_RE.match(meta_raw)
            if mm:
                lvl_raw = mm.group("lvl") or ""
                level = LEVEL_CN.get(lvl_raw, -1)
                school = mm.group("school") or ""
                cls_raw = mm.group("cls") or ""
                classes = _decode_classes_full(cls_raw)
        out.append({
            "name": zh,
            "detail_full_zh": zh,
            "eng_name": en,
            "source": source_chm,
            "level": level,
            "school": school,
            "classes": classes,
            "detail_meta": meta_raw,
            "detail_time": time_raw,
            "detail_range": range_raw,
            "detail_components": comp_raw,
            "detail_duration": dur_raw,
            "detail": _clean_links(_strip_html_table(detail_text)),
            "detail_higher": _clean_links(_strip_html_table(higher)),
            "has_detail": True,
        })
    return out


def find_detail_files(md_root: Path) -> list[tuple[Path, str]]:
    """定位官方详述文件 → [(绝对路径, 来源码)]。

    环阶打包目录展开为具体文件；散落单文件直接使用。
    """
    found: list[tuple[Path, str]] = []
    for rel, source in DETAIL_SOURCE_FILES:
        p = md_root / rel
        if p.is_dir():
            for lvl_name in LEVEL_FILE_NAMES:
                f = p / f"{lvl_name}.md"
                if f.exists():
                    found.append((f, source))
        elif p.is_file():
            found.append((p, source))
        else:
            print(f"  [warn] 详述文件缺失: {rel}", file=sys.stderr)
    return found


def find_third_party_detail_files(md_root: Path) -> list[tuple[Path, str]]:
    """扫描 md_root/第三方 下所有 .md，返回含法术详述块的文件。

    来源码不做目录映射（第三方文件与表格来源值对不上），统一用 'T' 占位，
    join 时按英文名匹配。返回 [(路径, 'T')]。
    """
    found: list[tuple[Path, str]] = []
    tp_root = md_root / "第三方"
    if not tp_root.is_dir():
        return found
    for f in sorted(tp_root.rglob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"^#### .+[｜|].+$", text, flags=re.M):
            found.append((f, "T"))
    return found


def _build_body(rec: dict) -> str:
    """拼装法术正文（与 kb_build_lib._spell_body 同构的「【法术信息】…」格式）。

    md 主源：元数据全部来自速查表+详述页，正文为详述文本+升环段。
    """
    parts = []
    meta = []
    lvl_cn = "戏法" if rec.get("level") == 0 else f"{rec.get('level')}环"
    meta.append(lvl_cn)
    if rec.get("school"):
        meta.append(f"学派{rec['school']}")
    t = rec.get("detail_time") or rec.get("time") or ""
    if t:
        meta.append(f"施法时间{t}")
    if rec.get("detail_range"):
        meta.append(f"距离{rec['detail_range']}")
    c = rec.get("components") or {}
    cstr = "成分" + "".join(
        p for p, flag in (("言语", c.get("v")), ("姿势", c.get("s")), ("材料", c.get("m"))) if flag
    )
    meta.append(cstr)
    if rec.get("detail_duration"):
        meta.append(f"持续时间：{rec['detail_duration']}")
    if rec.get("ritual"):
        meta.append("仪式")
    if rec.get("concentration"):
        meta.append("专注")
    parts.append("【法术信息】" + "｜".join(meta))
    if rec.get("detail"):
        parts.append(rec["detail"])
    if rec.get("detail_higher"):
        parts.append(rec["detail_higher"])
    return "\n\n".join(parts)


def build_spells(md_root: Path) -> list[dict]:
    """主入口：速查表 × 详述页 join → spells_chm.json 记录列表。"""
    # 1. 速查表
    quick_records: list[dict] = []
    for tbl_rel in ("速查/法术速查/5E万法大全.md", "速查/法术速查/合作方万法大全.md"):
        p = md_root / tbl_rel
        if not p.exists():
            print(f"  [warn] 速查表缺失: {tbl_rel}", file=sys.stderr)
            continue
        quick_records.extend(parse_quick_table(p.read_text(encoding="utf-8", errors="ignore")))

    # 2. 详述记录（官方 + 第三方）
    detail_records: list[dict] = []
    detail_by_file: dict[str, list[dict]] = {}
    for f, source in find_detail_files(md_root):
        text = f.read_text(encoding="utf-8", errors="ignore")
        recs = parse_detail_file(text, source)
        detail_records.extend(recs)
        detail_by_file.setdefault(str(f.relative_to(md_root)), recs)
    third_party_records: list[dict] = []
    for f, source in find_third_party_detail_files(md_root):
        text = f.read_text(encoding="utf-8", errors="ignore")
        recs = parse_detail_file(text, source)
        third_party_records.extend(recs)
        detail_by_file.setdefault(str(f.relative_to(md_root)), recs)

    # 3. join：速查表为锚。官方详述按 (英文名归一化, 来源码)，第三方详述按英文名归一化。
    by_eng_src: dict[tuple[str, str], dict] = {}
    for d in detail_records:
        by_eng_src.setdefault((_eng_key(d["eng_name"]), d["source"]), d)
    tp_by_eng: dict[str, list[dict]] = {}
    for d in third_party_records:
        tp_by_eng.setdefault(_eng_key(d["eng_name"]), []).append(d)

    # 详述记录 → 来源文件路径（用于 detail_source）
    rec_file: dict[int, str] = {}
    for rel, recs in detail_by_file.items():
        for r in recs:
            rec_file[id(r)] = rel

    out: list[dict] = []
    missing_detail: list[tuple[str, str]] = []
    for q in quick_records:
        src = q["source"]
        src5e, edition = CHM_SOURCE_MAP.get(src, (THIRD_PARTY_SOURCE_MAP.get(src, f"T:{src}"), "第三方"))
        rec = {
            "name": q["name"],
            "aliases": q.get("aliases", []),
            "eng_name": q["eng_name"],
            "source": src,
            "source_5e": src5e,
            "edition": edition,
            "level": q["level"],
            "school": q["school"],
            "classes": q["classes"],
            "time": q["time"],
            "components": q["components"],
            "ritual": q["ritual"],
            "concentration": q["concentration"],
            "has_detail": False,
            "detail": "",
            "detail_meta": "",
            "detail_time": "",
            "detail_range": "",
            "detail_components": "",
            "detail_duration": "",
            "detail_higher": "",
            "detail_source": "",
        }
        d: dict | None = None
        if src in CHM_SOURCE_MAP:
            d = by_eng_src.get((_eng_key(q["eng_name"]), src))
        else:
            # 第三方：英文名匹配全部第三方详述；同名冲突取第一个并告警
            cands = tp_by_eng.get(_eng_key(q["eng_name"]), [])
            if cands:
                d = cands[0]
                if len(cands) > 1:
                    print(f"  [warn] 第三方同名详述 {len(cands)} 份: {q['name']} "
                          f"{[rec_file.get(id(c), '?') for c in cands]}", file=sys.stderr)
        if d:
            rec.update({
                "has_detail": True,
                "detail": d["detail"],
                "detail_meta": d["detail_meta"],
                "detail_time": d["detail_time"],
                "detail_range": d["detail_range"],
                "detail_components": d["detail_components"],
                "detail_duration": d["detail_duration"],
                "detail_higher": d["detail_higher"],
            })
            # 详述页的职业（2014 全名）优先，速查表简写解码兜底
            if d["classes"]:
                rec["classes"] = d["classes"]
            if d["level"] != -1 and rec["level"] == -1:
                rec["level"] = d["level"]
            if d["school"] and not rec["school"]:
                rec["school"] = d["school"]
            # 详述标题的完整中文名（含 /）与表格主名不同时，补充为别名
            full_zh = d.get("detail_full_zh", "")
            if full_zh and full_zh != rec["name"]:
                for part in full_zh.split("/"):
                    part = part.strip()
                    if part and part not in rec["aliases"] and part != rec["name"]:
                        rec["aliases"].append(part)
            rec["detail_source"] = rec_file.get(id(d), "")
        else:
            missing_detail.append((q["name"], src))
        rec["body"] = _build_body(rec)
        out.append(rec)

    if missing_detail:
        print(f"  [audit] 无详述法术 {len(missing_detail)} 条: "
              f"{missing_detail[:10]}{'…' if len(missing_detail) > 10 else ''}", file=sys.stderr)

    # 4. 同名法术中文名归一（2024 优先，如 造水术/造水 两版并存时统一为「造水术」，
    #    原版本名（造水）并入 aliases，保证按任一中文名搜索都能命中两版）。
    name_by_eng: dict[str, str] = {}
    for s in out:
        if s["edition"] == "第三方":
            continue
        ek = _eng_key(s["eng_name"])
        if ek not in name_by_eng or s["edition"] == "2024":
            name_by_eng[ek] = s["name"]
    for s in out:
        if s["edition"] == "第三方":
            continue
        ek = _eng_key(s["eng_name"])
        canon = name_by_eng.get(ek)
        if canon and canon != s["name"]:
            if s["name"] not in s["aliases"]:
                s["aliases"].append(s["name"])
            s["name"] = canon
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="解析 5e_chm 法术数据源")
    ap.add_argument("--chm-md", required=True, help="5e_chm/md 根目录")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    args = ap.parse_args()
    md_root = Path(args.chm_md)
    spells = build_spells(md_root)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spells, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[chm_parser] 法术 {len(spells)} 条 → {out}")
    # 统计
    from collections import Counter
    by_src = Counter(s["source"] for s in spells)
    by_edition = Counter(s["edition"] for s in spells)
    no_detail = sum(1 for s in spells if not s["has_detail"])
    print(f"[chm_parser] 来源分布: {dict(by_src)}")
    print(f"[chm_parser] 版本分布: {dict(by_edition)} | 无详述: {no_detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

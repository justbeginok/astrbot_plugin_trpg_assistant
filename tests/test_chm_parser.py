"""scripts/chm_parser.py 单元测试：速查表/详述页解析、join、归一、来源映射。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.chm_parser import (
    CHM_SOURCE_MAP,
    _eng_key,
    build_spells,
    parse_detail_file,
    parse_quick_table,
)

CACHE = Path(__file__).resolve().parent.parent / "scripts" / "_md_cache" / "spells_chm.json"

# 速查表样本（23 td 行，取自真实万法大全）
QUICK_SAMPLE = """<table>
<tr><td></td><td>法术名</td><td></td><td>环阶</td><td></td><td>学派</td><td></td><td>职业</td><td></td><td>时间</td><td></td><td>言语</td><td></td><td>姿势</td><td></td><td>材料</td><td></td><td>仪式</td><td></td><td>专注</td><td></td><td>来源</td><td></td></tr>
<tr><td></td><td>火球术Fireball</td><td></td><td>三环</td><td></td><td>塑能</td><td></td><td>术法</td><td></td><td>动作</td><td></td><td>V</td><td></td><td>S</td><td></td><td>M</td><td></td><td>×</td><td></td><td>×</td><td></td><td>PHB24</td><td></td></tr>
<tr><td></td><td>嫌恶术/关怀术Antipathy/Sympathy</td><td></td><td>八环</td><td></td><td>惑控</td><td></td><td>诗德法</td><td></td><td>其他</td><td></td><td>V</td><td></td><td>S</td><td></td><td>M</td><td></td><td>×</td><td></td><td>×</td><td></td><td>PHB24</td><td></td></tr>
<tr><td></td><td>通灵仪式Séance</td><td></td><td>三环</td><td></td><td>死灵</td><td></td><td>诗法</td><td></td><td>其他</td><td></td><td>V</td><td></td><td>S</td><td></td><td>M*</td><td></td><td>×</td><td></td><td>×</td><td></td><td>尖塔2</td><td></td></tr>
<tr><td></td><td>警报术Alarm</td><td></td><td>一环</td><td></td><td>防护</td><td></td><td>软法械</td><td></td><td>其他</td><td></td><td>V</td><td></td><td>S</td><td></td><td>M</td><td></td><td>√</td><td></td><td>×</td><td></td><td>PHB24</td><td></td></tr>
</table>"""


def test_quick_table_basic() -> None:
    rows = parse_quick_table(QUICK_SAMPLE)
    assert len(rows) == 4  # 表头行被过滤
    fb = rows[0]
    assert fb["name"] == "火球术"
    assert fb["eng_name"] == "Fireball"
    assert fb["level"] == 3
    assert fb["school"] == "塑能"
    assert fb["classes"] == ["术士", "法师"]
    assert fb["time"] == "动作"
    assert fb["components"] == {"v": True, "s": True, "m": True, "costly": False}
    assert fb["ritual"] is False and fb["concentration"] is False


def test_quick_double_name() -> None:
    """双拼名：主名 + 别名 + 英文名含 / 完整保留。"""
    rows = parse_quick_table(QUICK_SAMPLE)
    ax = rows[1]
    assert ax["name"] == "嫌恶术"
    assert ax["aliases"] == ["关怀术"]
    assert ax["eng_name"] == "Antipathy/Sympathy"
    assert ax["level"] == 8


def test_quick_latin_accent() -> None:
    """拉丁重音字符（Séance）与 M*（有价值材料）解析。"""
    rows = parse_quick_table(QUICK_SAMPLE)
    tl = rows[2]
    assert tl["name"] == "通灵仪式"
    assert tl["eng_name"] == "Séance"
    assert tl["components"]["m"] is True
    assert tl["components"]["costly"] is True


def test_quick_ritual_mark() -> None:
    rows = parse_quick_table(QUICK_SAMPLE)
    assert rows[3]["ritual"] is True  # 警报术是仪式


# 详述页样本：2024 版 / 2014 版（空格+仪式标记）/ 斜体不成对（跨行）
DETAIL_SAMPLE_2024 = """#### 火球术｜Fireball

*三环塑能（术士、法师）*施法时间：动作
施法距离：150尺
法术成分：V、S、M（一颗蝙蝠粪和硫磺搓成的小球）
持续时间：立即
明亮的闪光从你的指间飞驰向施法距离内你指定的一点。豁免失败者将受到8d6点火焰伤害。
升环施法。使用的法术位每比三环高一环，此伤害就增加1d6。
"""

DETAIL_SAMPLE_2014 = """#### 假死术｜Feign Death

*三环死灵（仪式；吟游诗人、牧师、德鲁伊、法师）*施法时间：1 动作
施法距离：触碰
法术成分：V、S、M（一把坟土）
持续时间：1 小时
你触碰一自愿生物并将其变得如死去一样的僵直状态。
"""

DETAIL_SAMPLE_CROSSLINE = """#### 通灵仪式｜Séance

*三环死灵（吟游诗人、法师）
*施法时间：10分钟
施法距离：自身
法术成分：V、S、M（一颗水晶球）
持续时间：1分钟
你与三个或更多自愿的生物双手相握，从冥界召唤一个精魂来回答你们的提问。
"""

DETAIL_SAMPLE_HTML = """#### 混乱箭｜Chaos Bolt

*一环塑能（术士）*施法时间：1 动作
施法距离：120 尺
法术成分：V、S
持续时间：立即
你向目标掷出一团混乱能量。选择一颗 d8 投出的数字来决定它的伤害类型。

<table>
<tr><td></td><th>D8</th><th></th><th>伤害类别</th><th></th></tr>
<tr><td></td><td>1</td><td></td><td>强酸</td><td></td></tr>
</table>

若你的两颗 d8 投出了相同的数字，混乱能量将跳跃。
升环施法。当你使用二环或更高的法术位施展此法术时，伤害增加1d6。
"""


def test_detail_2024() -> None:
    recs = parse_detail_file(DETAIL_SAMPLE_2024, "PHB24")
    assert len(recs) == 1
    r = recs[0]
    assert r["name"] == "火球术" and r["eng_name"] == "Fireball"
    assert r["level"] == 3 and r["school"] == "塑能"
    assert r["classes"] == ["术士", "法师"]
    assert r["detail_time"] == "动作"
    assert r["detail_range"] == "150尺"
    assert r["detail_duration"] == "立即"
    assert r["detail_higher"].startswith("升环施法")
    # 元数据行与属性行应已从正文剔除（正文里「施法距离内」是正常行文，需排除）
    assert "*三环塑能" not in r["detail"]
    assert "施法距离：150尺" not in r["detail"]
    assert "法术成分：" not in r["detail"]
    assert "8d6点火焰伤害" in r["detail"]


def test_detail_2014_ritual_fullname() -> None:
    """2014：空格数字 + 仪式标记 + 职业全名（逗号/顿号混用）。"""
    recs = parse_detail_file(DETAIL_SAMPLE_2014, "PHB14")
    r = recs[0]
    assert r["level"] == 3 and r["school"] == "死灵"
    assert r["detail_time"] == "1 动作"
    # 仪式标记段丢弃，只保留职业
    assert r["classes"] == ["吟游诗人", "牧师", "德鲁伊", "法师"]


def test_detail_cross_line_meta() -> None:
    """斜体标记不成对（*meta* 与 施法时间 跨行）。"""
    recs = parse_detail_file(DETAIL_SAMPLE_CROSSLINE, "尖塔2")
    r = recs[0]
    assert r["level"] == 3 and r["school"] == "死灵"
    assert r["detail_time"] == "10分钟"
    assert "*三环死灵" not in r["detail"]
    assert "召唤一个精魂" in r["detail"]


def test_detail_html_table_strip() -> None:
    """残留 <table> 降级为纯文本行，不污染正文。"""
    recs = parse_detail_file(DETAIL_SAMPLE_HTML, "XGE")
    r = recs[0]
    assert "<table>" not in r["detail"]
    assert "D8" in r["detail"]  # 表头文本保留
    assert "升环施法" in r["detail_higher"]


def test_eng_key_normalize() -> None:
    """英文名归一化：大小写/空格/特殊字符容忍（Crownof Radiance vs Crown of Radiance）。"""
    assert _eng_key("Crownof Radiance") == _eng_key("Crown of Radiance")
    assert _eng_key("Antipathy/Sympathy") == _eng_key("antipathysympathy")


def test_source_map() -> None:
    assert CHM_SOURCE_MAP["PHB24"] == ("XPHB", "2024")
    assert CHM_SOURCE_MAP["PHB14"] == ("PHB", "2014")
    assert CHM_SOURCE_MAP["XGE"] == ("XGE", "2014")
    assert CHM_SOURCE_MAP["FR"] == ("FRHoF", "2014")
    assert CHM_SOURCE_MAP["夸力许"] == ("LLK", "2014")
    assert CHM_SOURCE_MAP["冰风谷"] == ("IDRotF", "2014")


@pytest.mark.skipif(not CACHE.exists(), reason="未生成 spells_chm.json（先跑 chm_parser）")
def test_smoke_real_data() -> None:
    """真实产物 smoke：1220 条、无详述 0、同英文名中文名归一、字段完整性。"""
    spells = json.loads(CACHE.read_text(encoding="utf-8"))
    assert len(spells) == 1220
    assert all(s["has_detail"] for s in spells), "存在无详述法术"
    assert all(s["level"] != -1 for s in spells), "存在环阶未知法术"
    assert all(s["eng_name"] for s in spells), "存在英文名缺失"
    # 同英文名（官方）中文名应一致（归一）
    by_eng: dict[str, set[str]] = {}
    for s in spells:
        if s["edition"] != "第三方":
            by_eng.setdefault(_eng_key(s["eng_name"]), set()).add(s["name"])
    multi = {k: v for k, v in by_eng.items() if len(v) > 1}
    assert not multi, f"同英文名多中文名未归一: {multi}"
    # 版本分布
    from collections import Counter
    ed = Counter(s["edition"] for s in spells)
    assert ed["2024"] == 391 and ed["2014"] == 545 and ed["第三方"] == 284

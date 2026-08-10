"""kb_tags 标签清洗单元测试。"""

from __future__ import annotations

import pytest

from astrbot_plugin_trpg_assistant.kb_tags import clean_5etools_tags


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 通用：取首段
        ("造成 {@damage 8d6} 伤害", "造成 8d6 伤害"),
        ("施法 {@spell 火球术|PHB}", "施法 火球术"),
        ("{@spell 火球术} 是经典法术", "火球术 是经典法术"),
        ("获得 {@item 治疗药水|DMG}", "获得 治疗药水"),
        ("目标陷入 {@condition 魅惑|PHB}", "目标陷入 魅惑"),
        ("使用 {@skill 察觉}", "使用 察觉"),
        ("{@class 法师|PHB} 施法者", "法师 施法者"),
        ("{@link 查看手册|https://example.com}", "查看手册"),
        # 特例：数字类
        ("豁免 {@dc 15}", "豁免 DC 15"),
        ("攻击骰 {@hit +5}", "攻击骰 +5"),
        ("攻击骰 {@hit 5}", "攻击骰 +5"),
        ("{@chance 50} 概率", "50% 概率"),
        ("{@recharge 5} 龙息", "（充能 5–6） 龙息"),
        ("{@recharge 6} 龙息", "（充能 6–6） 龙息"),
        # 特例：命中前缀（无内容）
        ("{@h} 挥砍伤害", "命中： 挥砍伤害"),
        # 特例：攻击类型
        ("{@atk mw,rw}", "近战武器攻击或远程武器攻击"),
        ("{@atk mw}", "近战武器攻击"),
        # 2024 版攻击距离/豁免动作
        ("{@atkr m}: {@hit +5}", "近战: +5"),
        ("{@atkr r|60}", "远程（触及60尺）"),
        ("{@actSave dex}：{@dc 12}", "敏捷豁免：DC 12"),
        ("{@actSaveFail}：21（{@damage 6d6}）", "豁免失败：21（6d6）"),
        ("{@actSaveSuccess}：半伤", "豁免成功：半伤"),
        # 双冒号折叠（{@h} + 源数据自带冒号）
        ("{@h}：8（{@damage 1d6}）", "命中：8（1d6）"),
        # 表格展开
        ("{@table 冲撞表|1、2|3、4}", "冲撞表：\n1、2\n3、4"),
        # 列表展开
        ("{@list 甲、乙、丙}", "甲\n乙\n丙"),
        # 未知标签兜底取首段
        ("{@quickref 护甲等级|PHB|2}", "护甲等级"),
        ("{@homebrew 自定内容|xx}", "自定内容"),
        # 嵌套标签迭代
        ("掷 {@dice 1d4 + {@damage 2d6}}", "掷 1d4 + 2d6"),
        # 名称类标签：第三段是显示覆盖
        ("{@variantrule 球状 [效应区域]|XPHB|球状}区域", "球状区域"),
        ("{@spell 火球术|PHB|火球术！} 来了", "火球术！ 来了"),
        # 名称类标签剥离子标签括号限定
        ("陷入 {@condition 力竭 [力竭]|PHB}", "陷入 力竭"),
        # scaledamage 取第三段（每环增量），不是第一段（基础伤害）
        ("伤害增加{@scaledamage 8d6|3-9|1d6}", "伤害增加1d6"),
        ("每高一环初始伤害增加{@scaledamage 10d4|4-9|2d4}", "每高一环初始伤害增加2d4"),
        ("伤害增加{@scaledice 1d6|5|1d6}", "伤害增加1d6"),
        # filter 类标签取首段（后续段是过滤条件）
        ("{@filter 战斗风格专长|feats|category=战斗风格|source=PHB}", "战斗风格专长"),
        # 空文本
        ("", ""),
        ("   ", ""),
        # 非标签花括号原样保留
        ("普通 {文本} 不处理", "普通 {文本} 不处理"),
    ],
)
def test_clean(raw: str, expected: str) -> None:
    assert clean_5etools_tags(raw) == expected


def test_empty_content_tag() -> None:
    # 无内容的未知标签应清空而非报错
    assert clean_5etools_tags("前缀{@foo}后缀") == "前缀后缀"


def test_consecutive_blank_lines_collapsed() -> None:
    raw = "第一段\n\n\n\n第二段"
    assert clean_5etools_tags(raw) == "第一段\n\n第二段"


def test_nested_deep_tags() -> None:
    raw = "{@b {@i {@spell 火球术|PHB}}}"
    assert clean_5etools_tags(raw) == "火球术"

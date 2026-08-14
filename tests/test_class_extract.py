"""class_extract 管道回归测试（v0.50.1：chm 独有职业 class 条目补全）。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.class_extract.emit import _class_eng_name, emit_one
from scripts.class_extract.finalize import merge_one


def _plan_item(kind: str, class_name: str, subclass_name: str = "") -> object:
    from scripts.class_extract.inventory import PlanItem

    return PlanItem(
        path=Path("fake.md"), book="第三方", source="VTM",
        kind=kind, class_name=class_name, subclass_name=subclass_name,
    )


def test_emit_class_branch_writes_class_array(tmp_path: Path) -> None:
    """v0.50.1：class 主体必须生成 class 数组条目（entries kind='class' 用）。"""
    text = (
        "---\ntitle: 拳斗士 Pugilist\n---\n\n# 拳斗士 Pugilist\n\n"
        "## 成为拳斗士…\n\n3级：拳击\n你的拳头就是武器。\n"
    )
    key, out = emit_one(_plan_item("class", "拳斗士", ""), text)
    assert key == "brawler"
    # class 条目：name + ENG_name + source
    assert len(out["class"]) == 1
    c = out["class"][0]
    assert c["name"] == "拳斗士"
    assert c["ENG_name"] == "Pugilist"
    assert c["source"] == "VTM"
    # classFeature 不受影响
    assert len(out["classFeature"]) >= 1


def test_class_eng_name_extraction() -> None:
    """英文名提取：H1 标题与 frontmatter title 均可。"""
    assert _class_eng_name("# 血族 Kindred\n\n正文") == "Kindred"
    assert _class_eng_name("---\ntitle: 拳斗士 Pugilist\n---\n") == "Pugilist"
    assert _class_eng_name("# 战士\n\n无英文标题") == ""


def test_finalize_merges_chm_only_class_entries(tmp_path: Path) -> None:
    """v0.50.1：chm 独有职业（cn 无 class 条目）的 class 数组合并补入。"""
    cn_dir = tmp_path / "cn"
    chm_dir = tmp_path / "chm"
    cn_dir.mkdir()
    chm_dir.mkdir()
    # cn：只有 fighter（含 class 条目）
    (cn_dir / "class-fighter.json").write_text(json.dumps({
        "class": [{"name": "战士", "ENG_name": "Fighter", "source": "PHB"}],
        "subclass": [], "classFeature": [], "subclassFeature": [],
    }, ensure_ascii=False), encoding="utf-8")
    (cn_dir / "class-vampire.json").write_text(json.dumps({
        "class": [], "subclass": [], "classFeature": [], "subclassFeature": [],
    }, ensure_ascii=False), encoding="utf-8")
    # chm：fighter（同名家）+ vampire（cn 无 class 条目）
    (chm_dir / "class-fighter.json").write_text(json.dumps({
        "class": [{"name": "战士", "ENG_name": "Fighter", "source": "PHB"}],
        "subclass": [], "classFeature": [], "subclassFeature": [],
    }, ensure_ascii=False), encoding="utf-8")
    (chm_dir / "class-vampire.json").write_text(json.dumps({
        "class": [{"name": "血族", "ENG_name": "Kindred", "source": "VTM"}],
        "subclass": [], "classFeature": [], "subclassFeature": [],
    }, ensure_ascii=False), encoding="utf-8")

    merged, _rep = merge_one(chm_dir / "class-vampire.json", cn_dir / "class-vampire.json")
    assert merged["class"] == [{"name": "血族", "ENG_name": "Kindred", "source": "VTM"}]

    # fighter：chm 同名 class 条目不应重复追加
    merged, _rep = merge_one(chm_dir / "class-fighter.json", cn_dir / "class-fighter.json")
    assert len(merged["class"]) == 1


def test_inventory_third_party_class_books(tmp_path: Path) -> None:
    """v0.50.2：第三方职业书根目录（拳斗士/血猎手/邪狱使）子职判定。

    主体.md=class、附属跳过、其余=subclass；斗殴者/家臣子目录跳过。
    """
    from scripts.class_extract.inventory import scan

    md = tmp_path / "md" / "第三方"
    (md / "拳斗士").mkdir(parents=True)
    (md / "血猎手").mkdir()
    (md / "邪狱使").mkdir()
    (md / "拳斗士" / "斗殴者").mkdir()
    (md / "邪狱使" / "家臣").mkdir()
    for rel in (
        "拳斗士/拳斗士.md", "拳斗士/怒雷恶氓.md", "拳斗士/封面.md",
        "拳斗士/斗殴者/好战莽夫.md", "拳斗士/斗殴者/斗殴者.md",
        "血猎手/血猎手.md", "血猎手/化狼结社.md", "血猎手/血咒.md",
        "邪狱使/邪狱使.md", "邪狱使/影宗.md", "邪狱使/家臣/家臣.md",
    ):
        p = md / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\ntitle: x\n---\n", encoding="utf-8")

    got: dict[str, tuple[str, str, str]] = {}
    for item in scan(tmp_path / "md"):
        rel = str(item.path.relative_to(tmp_path / "md")).replace("\\", "/")
        got[rel] = (item.kind, item.class_name, item.subclass_name)

    assert got["第三方/拳斗士/拳斗士.md"][:2] == ("class", "拳斗士")
    assert got["第三方/拳斗士/怒雷恶氓.md"] == ("subclass", "拳斗士", "怒雷恶氓")
    assert "第三方/拳斗士/封面.md" not in got          # 附属跳过
    assert "第三方/拳斗士/斗殴者/好战莽夫.md" not in got  # 怪物子目录跳过
    assert got["第三方/血猎手/血猎手.md"][:2] == ("class", "血猎手")
    assert got["第三方/血猎手/化狼结社.md"] == ("subclass", "血猎手", "化狼结社")
    assert "第三方/血猎手/血咒.md" not in got            # 选项列表跳过
    assert got["第三方/邪狱使/影宗.md"] == ("subclass", "邪狱使", "影宗")
    assert "第三方/邪狱使/家臣/家臣.md" not in got       # 助战者规则跳过

"""v0.37.0 私设助手（manage_homebrew）测试。

覆盖：homebrew_writer 纯函数（校验/文件名/合并/原子写）与工具级三动作
（convert 双程校验回执、write 配置/权限/冲突协议/落盘重载、review 锚点）。
惯例照搬 test_homebrew.py：_MemoryPlugin 直接注入 KnowledgeBaseManager，
无需 star_tools。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from astrbot_plugin_trpg_assistant.homebrew import HomebrewManager
from astrbot_plugin_trpg_assistant.homebrew_writer import (
    atomic_write_text,
    derive_filename,
    flatten_raw_entries,
    merge_homebrew_texts,
    sanitize_filename,
    validate_homebrew_text,
)
from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin
from scripts.build_kb import build

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"
NO_PATCH_DIR = Path(__file__).resolve().parent / "fixtures" / "no_patches"


def build_db(tmp_path: Path) -> Path:
    out = tmp_path / "kb" / "dnd_kb.db"
    build(FIXTURE_DIR, out, commit="fixture-abc123", patch_root=NO_PATCH_DIR)
    return out


def write_homebrew(dir_path: Path, name: str, data) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / name).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class FakeEvent:
    def __init__(
        self,
        message_str: str = "",
        private: bool = False,
        admin: bool = True,
        sender_id: str = "u1",
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = "group:1" if not private else "private:1"
        self._private = private
        self._admin = admin
        self._sender_id = sender_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return "Alice"

    def is_private_chat(self) -> bool:
        return self._private

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        pass


class _MemoryPlugin(TrpgAssistantPlugin):
    def __init__(self, db_path: Path, homebrew_dir: Path | None = None) -> None:
        super().__init__(context=None, config=None)
        self._kv: dict[str, object] = {}
        self._kb_manager = KnowledgeBaseManager(db_path, homebrew_dir=homebrew_dir)

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


def run(coro):
    return asyncio.run(coro)


def _spell_json(name: str = "私设火球", source: str = "DM", body: str = "8d6 火焰。") -> str:
    return json.dumps(
        [{"kind": "spell", "name": name, "source": source,
          "level": 3, "school": "E", "body": body}],
        ensure_ascii=False,
    )


def _write_plugin(tmp_path: Path, with_dir: bool = True) -> tuple[_MemoryPlugin, Path]:
    db = build_db(tmp_path)
    hb = tmp_path / "hb"
    hb.mkdir(parents=True, exist_ok=True)
    plugin = _MemoryPlugin(db, homebrew_dir=hb if with_dir else None)
    plugin.homebrew_write_enabled = True
    return plugin, hb


# ---------------------------------------------------------------------------
# convert：双程校验第二程
# ---------------------------------------------------------------------------


def test_convert_ok(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    text = _spell_json()
    out = run(plugin.manage_homebrew_tool(FakeEvent(), action="convert", json_text=text))
    assert "校验通过" in out and "1 条私设条目" in out
    assert "私设火球" in out  # 全文贴回


def test_convert_bad_json(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="convert", json_text="这不是 JSON {"))
    assert "校验失败" in out and "JSON 语法错误" in out
    assert "--- JSON 全文 ---" not in out  # 失败不贴全文


def test_convert_missing_name(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    text = json.dumps([{"kind": "item", "body": "没有名字。"}], ensure_ascii=False)
    out = run(plugin.manage_homebrew_tool(FakeEvent(), action="convert", json_text=text))
    assert "校验失败" in out
    assert "缺少 name" in out  # HomebrewManager 条目级告警透传
    assert "无可加载条目" in out


def test_convert_invalid_kind(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    text = json.dumps(
        [{"kind": "道具x", "name": "怪东西", "body": "x"}], ensure_ascii=False)
    out = run(plugin.manage_homebrew_tool(FakeEvent(), action="convert", json_text=text))
    assert "校验失败" in out and "缺少可识别的 kind" in out


def test_convert_override_warning(tmp_path: Path) -> None:
    """source 撞官方键（fixture 有 PHB 火球术）→ 醒目提示将房规覆盖。"""
    plugin, _ = _write_plugin(tmp_path)
    text = _spell_json(name="火球术", source="PHB")
    out = run(plugin.manage_homebrew_tool(FakeEvent(), action="convert", json_text=text))
    assert "校验通过" in out
    assert "撞键" in out and "房规覆盖" in out


def test_convert_long_text_hint(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    long_body = "长。" * 700  # 全文 >1200 字符
    text = _spell_json(body=long_body)
    assert len(text) > 1200
    out = run(plugin.manage_homebrew_tool(FakeEvent(), action="convert", json_text=text))
    assert "校验通过" in out and "开启私设写入" in out


# ---------------------------------------------------------------------------
# write：配置闸 / 权限闸 / 冲突协议 / 落盘重载
# ---------------------------------------------------------------------------


def test_write_ok_and_reload(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json()))
    assert "已写入 DM.json" in out and "新建" in out
    assert "私设已重载" in out and "共 1 条" in out
    payload = json.loads((hb / "DM.json").read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload[0]["name"] == "私设火球"
    hits = plugin.kb_manager.search("私设火球", kind="spell")
    assert hits and hits[0].is_homebrew


def test_write_disabled_reject(tmp_path: Path) -> None:
    db = build_db(tmp_path)
    hb = tmp_path / "hb"
    hb.mkdir(parents=True, exist_ok=True)
    plugin = _MemoryPlugin(db, homebrew_dir=hb)  # 默认配置：写入关闭
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json()))
    assert "私设写入未开启" in out and "WebUI" in out
    assert list(hb.glob("*.json")) == []


def test_write_permission_reject_group(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(admin=False), action="write", json_text=_spell_json()))
    assert "权限不足" in out


def test_write_permission_reject_private(tmp_path: Path) -> None:
    """私设是全局数据：私聊非管理员同样拒绝（决策 5，不放行私聊）。"""
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(private=True, admin=False), action="write",
        json_text=_spell_json()))
    assert "权限不足" in out


def test_write_whitelist_allow(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    plugin.enable_whitelist = True
    plugin.whitelist_users = ["u1"]
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(admin=False, sender_id="u1"), action="write",
        json_text=_spell_json()))
    assert "已写入" in out
    assert (hb / "DM.json").exists()


def test_write_conflict_reject(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    write_homebrew(hb, "party.json", [
        {"kind": "item", "name": "旧匕首", "source": "DM", "body": "旧。"}])
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json(),
        filename="party.json"))
    assert "冲突" in out and "旧匕首" in out and "overwrite=true" in out
    # 文件内容不变
    payload = json.loads((hb / "party.json").read_text(encoding="utf-8"))
    assert len(payload) == 1 and payload[0]["name"] == "旧匕首"


def test_write_overwrite(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    write_homebrew(hb, "party.json", [
        {"kind": "item", "name": "旧匕首", "source": "DM", "body": "旧。"}])
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json(),
        filename="party.json", overwrite=True))
    assert "已写入" in out and "整文件替换" in out
    payload = json.loads((hb / "party.json").read_text(encoding="utf-8"))
    assert len(payload) == 1 and payload[0]["name"] == "私设火球"


def test_write_merge(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    write_homebrew(hb, "party.json", [
        {"kind": "spell", "name": "火球改", "source": "DM", "body": "旧版。"},
        {"kind": "item", "name": "房规巨剑", "source": "DM", "body": "保留。"},
    ])
    new_text = json.dumps([
        {"kind": "spell", "name": "火球改", "source": "DM", "body": "新版。"},
        {"kind": "item", "name": "房规匕首", "source": "DM", "body": "追加。"},
    ], ensure_ascii=False)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=new_text,
        filename="party.json", merge=True))
    assert "已写入" in out and "合并" in out and "共 3 条" in out
    payload = json.loads((hb / "party.json").read_text(encoding="utf-8"))
    by_name = {e["name"]: e for e in payload}
    assert by_name["火球改"]["body"] == "新版。"  # 同键新盖旧
    assert by_name["房规巨剑"]["body"] == "保留。"  # 不同键保留
    assert "房规匕首" in by_name  # 新键追加


def test_write_merge_5etools_existing(tmp_path: Path) -> None:
    """旧文件是 5etools 顶层键格式：merge 输出简化数组且可重新加载。"""
    plugin, hb = _write_plugin(tmp_path)
    write_homebrew(hb, "party.json", {"spell": [
        {"name": "飘浮羽毛", "source": "DM", "level": 1,
         "entries": ["让羽毛飘浮。"]}]})
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json(),
        filename="party.json", merge=True))
    assert "已写入" in out and "共 2 条" in out
    payload = json.loads((hb / "party.json").read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    # 可重新加载（5etools 字段原样保留，显式三字段已注入）
    mgr = HomebrewManager(hb)
    result = mgr.load()
    assert result.entries == 2 and not result.errors
    names = {e.name for e in mgr.entries()}
    assert names == {"飘浮羽毛", "私设火球"}


def test_write_filename_injection(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    for bad in ("../../evil.json", "a/b.json", "..\\x.json"):
        out = run(plugin.manage_homebrew_tool(
            FakeEvent(), action="write", json_text=_spell_json(), filename=bad))
        assert "文件名非法" in out, bad
    assert not (tmp_path / "evil.json").exists()
    assert list(hb.glob("*.json")) == []


def test_write_filename_derive(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    text = _spell_json(source="DM团")
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=text))
    assert "已写入 DM团.json" in out
    assert (hb / "DM团.json").exists()


def test_write_overwrite_and_merge_mutex(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json(),
        overwrite=True, merge=True))
    assert "参数错误" in out and "互斥" in out


def test_write_validation_fail_no_disk(tmp_path: Path) -> None:
    plugin, hb = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text="坏 {{{"))
    assert "校验失败" in out
    assert list(hb.glob("*.json")) == []


def test_write_no_homebrew_dir(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path, with_dir=False)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="write", json_text=_spell_json()))
    assert "无私设目录" in out


# ---------------------------------------------------------------------------
# review：锚点 + 同名命中 + 强制查库句
# ---------------------------------------------------------------------------


def test_review_anchors(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="review", json_text=_spell_json()))
    assert "锚点" in out and "法术" in out and "私设火球" in out
    assert "level=3" in out  # 侧表字段摘录
    assert "query_dnd_knowledge" in out  # 尾部强制查库句


def test_review_unparseable_draft(tmp_path: Path) -> None:
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="review", json_text="一个三环能烧穿铁门的私设法术。"))
    assert "纯文本点评模式" in out
    assert "query_dnd_knowledge" in out


def test_review_same_name_hit(tmp_path: Path) -> None:
    """草稿名称与 fixture 官方条目同名 → 命中区列出官方条目。"""
    plugin, _ = _write_plugin(tmp_path)
    out = run(plugin.manage_homebrew_tool(
        FakeEvent(), action="review",
        json_text=_spell_json(name="火球术", source="DM")))
    assert "同名/近似命中" in out and "火球术" in out


# ---------------------------------------------------------------------------
# homebrew_writer 纯函数
# ---------------------------------------------------------------------------


def test_sanitize_filename_cases() -> None:
    assert sanitize_filename("ok.json") == "ok.json"
    assert sanitize_filename("我的房规") == "我的房规.json"
    assert sanitize_filename("a/b.json") is None
    assert sanitize_filename("..\\x.json") is None
    assert sanitize_filename("../../evil.json") is None
    assert sanitize_filename("") is None
    assert sanitize_filename("///") is None
    long_name = "长" * 100
    safe = sanitize_filename(long_name)
    assert safe is not None and safe.endswith(".json")
    assert len(safe) <= 65  # stem 截断 60 + ".json"
    assert derive_filename(["DM", "DM", "X"]) == "DM.json"
    assert derive_filename(["///"]) == "homebrew.json"


def test_merge_texts_unit() -> None:
    old = json.dumps([
        {"kind": "spell", "name": "A", "source": "DM", "body": "旧A"},
        {"kind": "item", "name": "B", "source": "DM", "body": "旧B"},
    ], ensure_ascii=False)
    new = json.dumps([
        {"kind": "spell", "name": "A", "source": "DM", "body": "新A"},
        {"kind": "item", "name": "C", "source": "DM", "body": "新C"},
        {"kind": "item", "name": "C", "source": "DM", "body": "新C2"},
    ], ensure_ascii=False)
    merged = json.loads(merge_homebrew_texts(old, new))
    assert [e["name"] for e in merged] == ["A", "B", "C"]
    by_name = {e["name"]: e for e in merged}
    assert by_name["A"]["body"] == "新A"   # 同键原位替换
    assert by_name["B"]["body"] == "旧B"   # 旧条目保留
    assert by_name["C"]["body"] == "新C2"  # 新文本内部同键后盖先


def test_atomic_write_text(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "dir" / "x.json"
    atomic_write_text(target, "你好")
    assert target.read_text(encoding="utf-8") == "你好"  # 自动建目录
    atomic_write_text(target, "覆盖")
    assert target.read_text(encoding="utf-8") == "覆盖"
    assert list(target.parent.glob("*.tmp")) == []  # 无临时文件残留


def test_validate_and_flatten_units(tmp_path: Path) -> None:
    """validate 权威性与 flatten 键定位的分工：flatten 容忍缺正文，validate 不容忍。"""
    text = json.dumps({"物品": [
        {"name": "无正文", "source": "DM"},
        {"name": "有正文", "source": "DM", "body": "有。"},
    ]}, ensure_ascii=False)
    v = validate_homebrew_text(text)
    assert v.ok  # 条目级问题进 warnings 不算 errors
    assert len(v.entries) == 1 and v.warnings
    assert "正文为空" in v.warnings[0]
    raws = flatten_raw_entries(json.loads(text))
    assert [r.name for r in raws] == ["无正文", "有正文"]  # 键定位不做合法性判断
    assert all(r.kind == "item" for r in raws)

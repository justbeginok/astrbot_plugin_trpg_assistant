"""LLM 工具 docstring schema 回归测试。

AstrBot 的 register_llm_tool 从函数的 Google 风格 docstring（Args: 段）解析
工具参数 schema，而不是从函数签名生成：docstring 缺 Args 段 → 参数列表为空 →
LLM 拿到的工具没有任何参数定义（表现为「LLM 没法正确拼写查询语句」）。
本测试忠实复刻 AstrBot 的解析逻辑，确保每个 @filter.llm_tool 函数的 docstring
参数与函数签名一致、类型合法、描述非空。
"""

from __future__ import annotations

import inspect

import pytest

docstring_parser = pytest.importorskip("docstring_parser")

from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin  # noqa: E402

# 与 AstrBot func_tool_manager 的 SUPPORTED_TYPES / PY_TO_JSON_TYPE 保持一致。
SUPPORTED_TYPES = ["string", "number", "object", "array", "boolean"]


def _llm_tool_methods():
    """yield (方法名, 函数对象)：所有带 _llm_tool_name 标记的装饰器方法。"""
    for name in dir(TrpgAssistantPlugin):
        obj = getattr(TrpgAssistantPlugin, name)
        if callable(obj) and getattr(obj, "_llm_tool_name", None):
            yield name, obj


def test_at_least_four_llm_tools() -> None:
    tools = list(_llm_tool_methods())
    names = {getattr(fn, "_llm_tool_name") for _, fn in tools}
    assert {
        "roll_dice", "manage_initiative", "manage_inventory", "manage_shop",
        "query_dnd_knowledge", "manage_character", "guide_chargen",
        "advise_build", "manage_homebrew", "summarize_session",
    } <= names


def test_every_llm_tool_docstring_matches_signature() -> None:
    """docstring Args 段参数必须与函数签名一一对应（排除 self/event）。"""
    for name, fn in _llm_tool_methods():
        sig = inspect.signature(fn)
        expected = [
            p for p in sig.parameters if p not in ("self", "event")
        ]
        parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
        actual = [p.arg_name for p in parsed.params]
        assert actual == expected, (
            f"{name}: docstring 参数 {actual} 与函数签名 {expected} 不一致，"
            "AstrBot 将无法生成正确的工具 schema。"
        )


def test_every_llm_tool_param_has_valid_type_and_description() -> None:
    """每个参数必须带 AstrBot 支持的类型注释与描述，否则注册时抛 ValueError。"""
    for name, fn in _llm_tool_methods():
        parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
        for p in parsed.params:
            assert p.type_name in SUPPORTED_TYPES, (
                f"{name}: 参数 {p.arg_name} 类型 {p.type_name!r} 不被 AstrBot 支持"
            )
            assert p.description and p.description.strip(), (
                f"{name}: 参数 {p.arg_name} 缺少描述"
            )


def test_manage_shop_schema_params() -> None:
    """manage_shop 的 5 个参数进入 schema（v0.39.0 新增 items 批量参数）。"""
    fn = getattr(TrpgAssistantPlugin, "manage_shop_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    names = [p.arg_name for p in parsed.params]
    assert names == ["action", "item", "qty", "page", "items"]
    items_param = parsed.params[-1]
    assert items_param.type_name == "array"
    assert "item" in (items_param.description or "") and "qty" in (
        items_param.description or ""
    )


def test_manage_inventory_schema_params() -> None:
    """manage_inventory 的 8 个参数进入 schema（v0.42.0 新增 items 批量参数）。"""
    fn = getattr(TrpgAssistantPlugin, "manage_inventory_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    names = [p.arg_name for p in parsed.params]
    assert names == ["action", "item", "qty", "weight", "value", "note", "to_party", "items"]
    items_param = parsed.params[-1]
    assert items_param.type_name == "array"
    assert "item" in (items_param.description or "") and "qty" in (
        items_param.description or ""
    )


def test_query_dnd_knowledge_schema_has_expected_params() -> None:
    """query_dnd_knowledge 的 42 个参数必须全部进入 schema（本次 bug 的直接回归）。"""
    fn = getattr(TrpgAssistantPlugin, "query_dnd_knowledge_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    assert len(parsed.params) == 42
    names = [p.arg_name for p in parsed.params]
    assert names == [
        "action", "kind", "name", "level", "school",
        "cr_min", "cr_max", "monster_type", "rarity", "subclass",
        "feature",
        "damage_type", "condition", "environment", "weapon_property",
        "components", "concentration", "shape", "target",
        "range_type", "range_max", "base_item", "item_type",
        "speed_type", "speed_min", "speed_max", "size",
        "creature_type", "darkvision_min", "innate_spell",
        "feat_type", "feat_keywords", "spell_keywords",
        "class_role", "class_keywords", "subclass_keywords",
        "race_keywords", "background_keywords",
        "spell_class", "class_level",
        "opt_type", "opt_prereq",
    ]


def test_advise_build_schema_params() -> None:
    """advise_build 的 4 个参数进入 schema，且带防幻觉守则描述。"""
    fn = getattr(TrpgAssistantPlugin, "advise_build_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    assert [p.arg_name for p in parsed.params] == [
        "action", "goal", "keywords", "level",
    ]
    doc = inspect.getdoc(fn) or ""
    assert "禁止凭记忆" in doc or "禁止" in doc


def test_guide_chargen_schema_has_prefill_params() -> None:
    """guide_chargen 新增 race/class_name/background 预填参数（v0.35.0）。"""
    fn = getattr(TrpgAssistantPlugin, "guide_chargen_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    names = [p.arg_name for p in parsed.params]
    assert names == ["action", "answer", "assign", "race", "class_name", "background"]


def test_manage_homebrew_schema_params() -> None:
    """manage_homebrew 的 5 个参数进入 schema（v0.37.0），且带写入安全约定。"""
    fn = getattr(TrpgAssistantPlugin, "manage_homebrew_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    names = [p.arg_name for p in parsed.params]
    assert names == ["action", "json_text", "filename", "overwrite", "merge"]
    doc = inspect.getdoc(fn) or ""
    assert "禁止仅凭记忆点评" in doc and "白名单" in doc


def test_summarize_session_schema_params() -> None:
    """summarize_session 的 4 个参数进入 schema（v0.54.0），且带权限/缓存约定。"""
    fn = getattr(TrpgAssistantPlugin, "summarize_session_tool")
    parsed = docstring_parser.parse(inspect.getdoc(fn) or "")
    names = [p.arg_name for p in parsed.params]
    assert names == ["action", "campaign", "session_seq", "force"]
    assert parsed.params[0].type_name == "string"
    assert parsed.params[2].type_name == "number"
    assert parsed.params[3].type_name == "boolean"
    doc = inspect.getdoc(fn) or ""
    assert "玩家明确要求" in doc  # 写操作守则
    assert "缓存" in doc  # 摘要缓存约定

"""LLM 请求前钩子（on_llm_request）工具守则测试。

背景：AstrBot v4.5+ 插件工具需在 WebUI 工具面板启用后才注入 LLM，且工具
描述只在模型决定调用时才可见。若模型在检定、车卡这类开放式任务上倾向于
直接用自然语言扮演/编造，docstring 里的守则永远不会被模型看到，表现为
「助手很乐意扮演，但不调用工具」。

本插件通过 @filter.on_llm_request 钩子在每个 LLM 请求的 system_prompt 末尾
注入压缩版「跑团助手·工具守则」（9 个工具按场景一句一条 + 破坏性操作约束），
让模型在生成回复前就知道 TRPG 数据操作必须走工具——这是 AstrBot 生态
「工具触发引导」的标准做法（live_dashboard 等插件同款）。

覆盖点：
  - 钩子已注册（_on_llm_request 标记）且守则文本含全部 9 个工具。
  - 注入后 system_prompt 含守则、原内容保留；已含守则时不重复追加。
  - system_prompt 为 None / 缺失该字段时静默容错，绝不抛异常阻断 LLM 流程。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from astrbot_plugin_trpg_assistant.kb import KnowledgeBaseManager
from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "kb_sample"


class _GuardPlugin(TrpgAssistantPlugin):
    """最小编译插件实例：仅需 __init__ 可跑 + _kb_manager 占位。"""

    def __init__(self) -> None:
        super().__init__(context=None)
        self._kb_manager = KnowledgeBaseManager(FIXTURE_DIR)


def _run(coro) -> object:
    return asyncio.run(coro)


def _make_plugin() -> _GuardPlugin:
    return _GuardPlugin()


def test_on_llm_request_hook_registered() -> None:
    """钩子方法必须带 on_llm_request 装饰器标记（测试替身可识别）。"""
    fn = getattr(TrpgAssistantPlugin, "_on_llm_request_guard")
    assert callable(fn)
    assert getattr(fn, "_on_llm_request", False)


def test_guard_text_mentions_required_tools() -> None:
    """守则文本必须点名全部 9 个工具 + 破坏性操作约束。"""
    text = TrpgAssistantPlugin._llm_request_guard()
    for tool in (
        "roll_dice",
        "query_dnd_knowledge",
        "manage_initiative",
        "manage_inventory",
        "manage_character",
        "guide_chargen",
        "manage_shop",
        "advise_build",
        "manage_homebrew",
    ):
        assert tool in text, f"守则缺少工具 {tool}"
    assert "/车卡 导入" in text
    assert "破坏性操作" in text
    # 可检索标记（防重复追加判断依赖它）
    assert "【跑团助手·工具守则】" in text


def test_guard_injected_into_system_prompt() -> None:
    """注入后 system_prompt 保留原文并追加守则。"""
    plugin = _make_plugin()
    req = SimpleNamespace(system_prompt="你是 DM。")
    _run(plugin._on_llm_request_guard(None, req))
    assert req.system_prompt.startswith("你是 DM。")
    assert "【跑团助手·工具守则】" in req.system_prompt


def test_guard_not_duplicated() -> None:
    """system_prompt 已含守则时再次调用不重复追加。"""
    plugin = _make_plugin()
    guard = TrpgAssistantPlugin._llm_request_guard()
    req = SimpleNamespace(system_prompt="你是 DM。" + guard)
    before = req.system_prompt
    _run(plugin._on_llm_request_guard(None, req))
    assert req.system_prompt == before
    assert req.system_prompt.count("【跑团助手·工具守则】") == 1


def test_guard_none_system_prompt() -> None:
    """system_prompt 为 None 时正常注入守则（不炸 None 拼接）。"""
    plugin = _make_plugin()
    req = SimpleNamespace(system_prompt=None)
    _run(plugin._on_llm_request_guard(None, req))
    assert "【跑团助手·工具守则】" in req.system_prompt


def test_guard_missing_system_prompt_silent() -> None:
    """req 缺少 system_prompt 字段时静默兜底（新老版本 ProviderRequest 差异），
    不抛异常、不影响 LLM 流程。"""
    plugin = _make_plugin()
    req = SimpleNamespace(prompt="hi")  # 无 system_prompt 属性
    _run(plugin._on_llm_request_guard(None, req))
    assert "【跑团助手·工具守则】" in req.system_prompt

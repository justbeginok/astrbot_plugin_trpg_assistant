"""astrbot.api.event 测试替身。

仅提供装饰器与类型占位，使 main.py 可被导入；
真实消息处理逻辑不在单元测试覆盖范围内（测试使用 FakeEvent）。
"""

from __future__ import annotations

from enum import Enum


class AstrMessageEvent:
    """占位类型，真实实现由 AstrBot 提供。"""


class EventMessageType(Enum):
    """占位枚举，真实实现包含更多成员。"""

    ALL = "all"


class _Filter:
    """装饰器集合：原样返回函数，不拦截测试导入。"""

    EventMessageType = EventMessageType

    @staticmethod
    def command(name: str, alias: set[str] | None = None):
        def decorator(fn):
            fn._cmd_name = name
            fn._cmd_aliases = alias or set()
            return fn

        return decorator

    @staticmethod
    def llm_tool(name: str | None = None):
        def decorator(fn):
            fn._llm_tool_name = name
            return fn

        return decorator

    @staticmethod
    def on_llm_request(*args, **kwargs):
        def decorator(fn):
            fn._on_llm_request = True
            return fn

        return decorator

    @staticmethod
    def event_message_type(*args, **kwargs):
        def decorator(fn):
            return fn

        return decorator

    @staticmethod
    def on_decorating_result(*args, **kwargs):
        def decorator(fn):
            fn._on_decorating_result = True
            return fn

        return decorator


filter = _Filter()

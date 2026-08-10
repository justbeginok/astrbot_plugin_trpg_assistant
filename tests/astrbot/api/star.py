"""astrbot.api.star 测试替身。"""

from __future__ import annotations


class Context:
    """占位类型。"""


class Star:
    """占位类型。

    真实 AstrBot 中 Star 混入 KV 存储（get_kv_data/put_kv_data/delete_kv_data），
    测试时由子类（内存实现）提供这些能力。
    """

    def __init__(self, context: object | None = None) -> None:
        self.context = context

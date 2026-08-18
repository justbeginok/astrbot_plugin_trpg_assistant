"""astrbot.api.message_components 测试替身：仅提供 Plain 文本段。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Plain:
    text: str = ""

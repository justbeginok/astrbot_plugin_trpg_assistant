# -*- coding: utf-8 -*-
"""端到端测试：连接本机 AstrBot（127.0.0.1:6185）真实验证插件行为。

默认跳过（不影响 1026 个替身单元测试）；显式启用：
    E2E_TESTS=1 python -m pytest tests/test_e2e_api.py -v

前置：
    - AstrBot 桌面版正在运行（后端监听 6185）
    - workspace 根 .env 配置了 ASTROBOT_API_KEY（chat 权限）
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import e2e_test  # noqa: E402


def _env_ready() -> bool:
    if not os.environ.get("E2E_TESTS"):
        return False
    try:
        e2e_test.api_key()
        return True
    except RuntimeError:
        return False


pytestmark = pytest.mark.skipif(
    not _env_ready(),
    reason="E2E 测试默认关闭（设置 E2E_TESTS=1 且 .env 含 ASTROBOT_API_KEY 才运行）",
)


@pytest.fixture(scope="session", autouse=True)
def _cleanup_after_session():
    """测试结束后清理 KV 与消息历史残留，避免污染真实会话。"""
    yield
    result = e2e_test.cleanup()
    if any(result.values()):
        print(f"\n[e2e] 已清理测试数据：{result}")


class TestDiceRoll:
    def test_single_d20(self):
        text, _ = e2e_test.send_chat("/r 1d20")
        assert re.search(r"d20: \[\d+\] = \d+", text), f"响应异常: {text}"

    def test_pool_with_modifier(self):
        text, _ = e2e_test.send_chat("/r 2d6+3")
        assert "=" in text, f"响应异常: {text}"
        assert text.strip(), "空响应"

    def test_named_alias_roll(self):
        text, _ = e2e_test.send_chat("/roll 1d20")
        assert "=" in text, f"响应异常: {text}"


class TestKnowledgeBase:
    def test_spell_query(self):
        text, _ = e2e_test.send_chat("/查法术 火球术")
        assert "火球术" in text, f"未查到火球术: {text[:200]}"


class TestSessionCommands:
    def test_shop_view_responds(self):
        text, _ = e2e_test.send_chat("/商店 查看")
        assert text.strip(), f"空响应: {text}"

    def test_history_responds(self):
        text, _ = e2e_test.send_chat("/rh")
        assert text.strip(), f"空响应: {text}"

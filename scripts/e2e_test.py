# -*- coding: utf-8 -*-
"""AstrBot 端到端测试 harness（连接本机部署实例 127.0.0.1:6185）。

用法：
    python scripts/e2e_test.py "/r 1d20"           # 发一条消息，打印插件回复
    E2E_TESTS=1 python -m pytest tests/test_e2e_api.py -v   # 跑端到端用例

配置：
    - 环境变量 ASTROBOT_API_KEY，或 workspace 根 .env 中的 ASTROBOT_API_KEY
    - 可选：ASTROBOT_BASE_URL（默认 http://127.0.0.1:6185/api/v1）
    - 可选：ASTROBOT_DB（SQLite 直连清理用，默认 ~/.astrbot/data/data_v4.db）

说明：
    - POST /api/v1/chat 走 webchat 平台适配器 -> 完整消息管线 -> 插件命令入口，
      与真实用户在 WebUI 发消息等价。响应为 SSE 流，type=plain 为文本回复。
    - 测试用独立 username/session_id 隔离数据；cleanup() 清理 KV 残留。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_URL = os.environ.get("ASTROBOT_BASE_URL", "http://127.0.0.1:6185/api/v1")
PLUGIN_SCOPE_ID = "justbeginok/astrbot_plugin_trpg_assistant"
DB_PATH = Path(
    os.environ.get(
        "ASTROBOT_DB",
        r"C:/Users/75957/.astrbot/data/data_v4.db",
    )
)


def load_key() -> str | None:
    """从环境变量或 workspace 根 .env 读取 API key。"""
    if key := os.environ.get("ASTROBOT_API_KEY"):
        return key.strip()
    # 脚本位于 <workspace>/astrbot_plugin_trpg_assistant/scripts/，向上两级找 workspace 根
    cand = Path(__file__).resolve().parent.parent.parent / ".env"
    if cand.exists():
        for line in cand.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ASTROBOT_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def api_key() -> str:
    key = load_key()
    if not key:
        raise RuntimeError(
            "未找到 ASTROBOT_API_KEY：请设置环境变量，或在 workspace 根 .env 中配置"
        )
    return key


def parse_sse(raw: str) -> list[dict]:
    """解析 SSE 流（data: 行），返回事件列表。"""
    events: list[dict] = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        events.append(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
    return events


def extract_plain(events: list[dict]) -> str:
    """拼接所有 type=plain 事件的文本（即 bot 的最终回复）。"""
    return "".join(
        str(e.get("data", "")) for e in events if e.get("type") == "plain"
    )


def send_chat(
    message: str,
    username: str = "e2e-tester",
    session_id: str | None = None,
    timeout: int = 60,
) -> tuple[str, list[dict]]:
    """发送一条消息给插件，返回 (纯文本回复, 原始事件列表)。"""
    if session_id is None:
        session_id = f"e2e-{uuid.uuid4().hex[:8]}"
    body = json.dumps(
        {"message": message, "username": username, "session_id": session_id}
    ).encode("utf-8")
    req = urllib.request.Request(BASE_URL + "/chat", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", api_key())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    events = parse_sse(raw)
    return extract_plain(events), events


def cleanup(username: str = "e2e-tester", session_prefix: str = "e2e-") -> dict[str, int]:
    """清理测试会话残留：插件 KV（preferences 表）+ 消息历史（platform_message_history 表）。

    消息历史的 user_id 是 session_id（send_chat 生成 `e2e-<hex8>`），
    因此按 session_prefix 匹配；KV 的 origin 含 username，按 username 匹配。

    返回 {"kv": 删除条数, "messages": 删除条数}。
    """
    result = {"kv": 0, "messages": 0}
    if not DB_PATH.exists():
        return result
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        cur = db.cursor()
        cur.execute(
            "DELETE FROM preferences WHERE scope='plugin' AND scope_id=? AND key LIKE ?",
            (PLUGIN_SCOPE_ID, f"%webchat!{username}%"),
        )
        result["kv"] = cur.rowcount
        pat = f"{session_prefix}%"
        cur.execute(
            "DELETE FROM platform_message_history "
            "WHERE user_id LIKE ? OR sender_id LIKE ? OR sender_name LIKE ?",
            (pat, pat, pat),
        )
        result["messages"] = cur.rowcount
        db.commit()
        return result
    finally:
        db.close()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    text, events = send_chat(" ".join(sys.argv[1:]))
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

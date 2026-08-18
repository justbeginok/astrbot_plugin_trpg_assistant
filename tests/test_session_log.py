"""跑团记录（session_log）测试：Manager 单测 + 命令级集成 + 捕获钩子。

沿用 test_integration_commands.py 的模式：内存 KV 假 Star + 假消息事件，
跑团日志管理器注入临时 SQLite（tmp_path），异步测试用 asyncio.run 包装
（与仓库现有风格一致，不依赖 pytest-asyncio）。覆盖：
团/场次生命周期、权限门、玩家/机器人消息捕获与结算预标、摘要生成/缓存/重算、
LLM 工具 summarize_session、功能关闭时的降级。
"""

from __future__ import annotations

import asyncio
import functools

import pytest

from astrbot_plugin_trpg_assistant.main import TrpgAssistantPlugin, _looks_like_roll_command
from astrbot_plugin_trpg_assistant.session_log import (
    LogEntry,
    SessionLogManager,
    parse_transcript,
)


def sync_test(fn):
    """把 async 测试函数转成同步（asyncio.run 包装）。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


class _MemPlugin(TrpgAssistantPlugin):
    """子类化插件：真实 __init__ + 内存 KV（跑团记录走注入的临时 SQLite）。"""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(context=None, config=config)
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


class _Result:
    def __init__(self, chain) -> None:
        self.chain = chain


class _Ev:
    """假消息事件：含 get_result/get_messages（捕获与导入钩子需要）。"""

    def __init__(
        self,
        message_str: str,
        origin: str = "group:1",
        sender_id: str = "u1",
        sender_name: str = "Alice",
        private: bool = False,
        admin: bool = False,
    ) -> None:
        self.message_str = message_str
        self.unified_msg_origin = origin
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._private = private
        self._admin = admin
        self._result: _Result | None = None
        self._messages: list = []
        self.stopped = False

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def is_private_chat(self) -> bool:
        return self._private

    def is_admin(self) -> bool:
        return self._admin

    def plain_result(self, text: str) -> str:
        return text

    def stop_event(self) -> None:
        self.stopped = True

    def get_result(self) -> _Result | None:
        return self._result

    def set_result_text(self, text: str) -> None:
        from astrbot.api.message_components import Plain

        self._result = _Result([Plain(text=text)])

    def get_messages(self) -> list:
        return self._messages

    def set_messages(self, segs: list) -> None:
        self._messages = list(segs)


class _FakeFileSeg:
    """假 File 消息段：async get_file 返回本地路径。"""

    def __init__(self, path: str) -> None:
        self._path = path

    async def get_file(self) -> str:
        return self._path


class FakeLLMResponse:
    def __init__(self, text: str) -> None:
        self.completion_text = text


class FakeContext:
    """假 context：记录 llm 调用次数，返回固定摘要文本。"""

    def __init__(self, text: str = "团第 1 场摘要：冒险者进入洞窟……") -> None:
        self._text = text
        self.calls: list[tuple] = []

    async def get_current_chat_provider_id(self, umo: str) -> str:
        self.calls.append(("provider", umo))
        return "fake-provider"

    async def llm_generate(self, chat_provider_id: str, prompt: str, system_prompt: str):
        self.calls.append(("llm", chat_provider_id, prompt, system_prompt))
        return FakeLLMResponse(self._text)


@pytest.fixture
def plugin(tmp_path) -> _MemPlugin:
    p = _MemPlugin()
    p._session_log_manager = SessionLogManager(tmp_path / "trpg_log.db")
    return p


def ev(
    message_str: str,
    origin: str = "group:1",
    sender_id: str = "u1",
    sender_name: str = "Alice",
    private: bool = False,
    admin: bool = False,
) -> _Ev:
    return _Ev(message_str, origin, sender_id, sender_name, private, admin)


# ---------------------------------------------------------------------------
# 结算预标正则
# ---------------------------------------------------------------------------


def test_looks_like_roll_command() -> None:
    assert _looks_like_roll_command("/r 1d20+5")
    assert _looks_like_roll_command(".r 力量")
    assert _looks_like_roll_command("/roll 2d6")
    assert _looks_like_roll_command("/dnd")
    assert _looks_like_roll_command("roll 1d20")
    assert not _looks_like_roll_command("/ri 先攻")
    assert not _looks_like_roll_command("/记录 看")
    assert not _looks_like_roll_command("我拔出长剑冲向哥布林")
    assert not _looks_like_roll_command("1d20 裸骰不算指令")


# ---------------------------------------------------------------------------
# Manager：团/场次生命周期
# ---------------------------------------------------------------------------


@sync_test
async def test_manager_lifecycle(tmp_path) -> None:
    mgr = SessionLogManager(tmp_path / "log.db")
    # 新建团 → 第 1 场
    ok, msg = await mgr.start("group:1", "红龙之影")
    assert ok and "第 1 场" in msg
    # 重复 start → 已在记录
    ok2, msg2 = await mgr.start("group:1", "红龙之影")
    assert not ok2 and "已在记录" in msg2
    # 同会话只能记录一个团
    ok3, msg3 = await mgr.start("group:1", "暗月")
    assert not ok3 and "只能记录一个团" in msg3
    # 暂停后消息不入日志
    ok4, msg4 = await mgr.pause("group:1")
    assert ok4 and "已暂停" in msg4
    assert not await mgr.add_entry("group:1", "player", "暂停中的消息")
    # 继续 → 同场次
    ok5, msg5 = await mgr.start("group:1", "红龙之影")
    assert ok5 and "继续记录" in msg5 and "第 1 场" in msg5
    # 正常追加
    assert await mgr.add_entry("group:1", "player", "我拔剑", sender_name="Alice")
    assert await mgr.add_entry("group:1", "bot", "掷出了 d20 = 15")
    entries = await mgr.get_entries("group:1", "红龙之影")
    assert len(entries) == 2 and entries[0].role == "player"
    # 结束 → 数据保留，不再追加
    ok6, msg6 = await mgr.stop("group:1")
    assert ok6 and "已结束" in msg6 and "2 条" in msg6
    assert not await mgr.add_entry("group:1", "player", "跑完了")
    # 再次开始 → 第 2 场
    ok7, msg7 = await mgr.start("group:1", "红龙之影")
    assert ok7 and "第 2 场" in msg7
    await mgr.add_entry("group:1", "player", "第二场开头")
    assert len(await mgr.get_entries("group:1", "红龙之影")) == 1  # 最近一场
    assert len(await mgr.get_entries("group:1", "红龙之影", session_seq=1)) == 2
    # 团概要
    campaigns = await mgr.list_campaigns("group:1")
    assert len(campaigns) == 1
    assert campaigns[0].campaign == "红龙之影"
    assert campaigns[0].total_sessions == 2
    assert campaigns[0].total_count == 3
    # 删除
    deleted = await mgr.delete_campaign("group:1", "红龙之影")
    assert deleted == 3
    assert await mgr.list_campaigns("group:1") == []


@sync_test
async def test_manager_other_origin_isolated(tmp_path) -> None:
    mgr = SessionLogManager(tmp_path / "log.db")
    await mgr.start("group:1", "团A")
    await mgr.add_entry("group:1", "player", "群1消息")
    await mgr.start("group:2", "团B")
    await mgr.add_entry("group:2", "player", "群2消息")
    assert len(await mgr.get_entries("group:1", "团A")) == 1
    assert len(await mgr.get_entries("group:2", "团B")) == 1


# ---------------------------------------------------------------------------
# Manager：摘要存取
# ---------------------------------------------------------------------------


@sync_test
async def test_manager_summary(tmp_path) -> None:
    mgr = SessionLogManager(tmp_path / "log.db")
    await mgr.start("group:1", "团A")
    await mgr.add_entry("group:1", "player", "行动")
    await mgr.save_summary("group:1", "团A", 1, "摘要内容")
    row = await mgr.get_summary("group:1", "团A", 1)
    assert row is not None and row.summary_text == "摘要内容"
    rows = await mgr.list_summaries("group:1", "团A")
    assert len(rows) == 1 and rows[0].session_seq == 1
    # 覆盖写入
    await mgr.save_summary("group:1", "团A", 1, "新摘要")
    assert (await mgr.get_summary("group:1", "团A", 1)).summary_text == "新摘要"


@sync_test
async def test_build_summary_input_truncation(tmp_path) -> None:
    mgr = SessionLogManager(tmp_path / "log.db", max_summary_entries=2, max_summary_chars=50)
    entries = [
        LogEntry(i, "ts", "player", "u", "A", f"消息{i}", False) for i in range(5)
    ]
    text, truncated = mgr.build_summary_input("团", 1, entries)
    assert truncated
    assert "消息4" in text  # 取最近窗口，保留尾段


# ---------------------------------------------------------------------------
# 命令级：权限
# ---------------------------------------------------------------------------


@sync_test
async def test_write_commands_require_permission(plugin) -> None:
    ev_group = ev("/开始记录 红龙", admin=False)  # 群聊非管理员
    ok, msg = await plugin._start_log_core(ev_group, "红龙")
    assert not ok and "权限" in msg
    ok, msg = await plugin._stop_log_core(ev_group, "红龙")
    assert not ok and "权限" in msg
    ok, msg = await plugin._delete_log_core(ev_group, "红龙")
    assert not ok and "权限" in msg


@sync_test
async def test_write_commands_private_or_admin_allowed(plugin) -> None:
    ok, _ = await plugin._start_log_core(ev("/开始记录 红龙", private=True), "红龙")
    assert ok
    ok, _ = await plugin._start_log_core(
        ev("/开始记录 蓝龙", origin="group:2", admin=True), "蓝龙"
    )
    assert ok


# ---------------------------------------------------------------------------
# 命令级：生命周期 + 查看
# ---------------------------------------------------------------------------


@sync_test
async def test_start_stop_view_flow(plugin) -> None:
    out: list[str] = []
    e1 = ev("/开始记录 红龙之影", admin=True)
    async for m in plugin.start_log_cmd(e1):
        out.append(m)
    assert any("红龙之影" in m and "第 1 场" in m for m in out)
    # 消息入日志（直接调捕获钩子）
    await plugin.session_log_route(ev("我推开酒馆大门", sender_name="Bob"))
    await plugin.session_log_route(ev("/r 察觉 12", sender_name="Bob"))
    # 结束
    e2 = ev("/结束记录", admin=True)
    async for m in plugin.stop_log_cmd(e2):
        out.append(m)
    # 查看：/记录 看 红龙之影
    e3 = ev("/记录 看 红龙之影")
    async for m in plugin.log_cmd(e3):
        out.append(m)
    joined = "\n".join(out)
    assert "我推开酒馆大门" in joined
    assert "[结算]" in joined  # /r 察觉 被预标
    # 状态（无参）
    e4 = ev("/记录")
    async for m in plugin.log_cmd(e4):
        out.append(m)
    assert any("红龙之影" in m for m in out[3:])


@sync_test
async def test_pause_resume_flow(plugin) -> None:
    await plugin._start_log_core(ev("/开始记录 团A", admin=True), "团A")
    ok, msg = await plugin._pause_log_core(ev("/暂停记录", admin=True), "")
    assert ok and "已暂停" in msg
    # 暂停后玩家消息不入日志
    await plugin.session_log_route(ev("暂停期间的闲聊"))
    entries = await plugin._session_log_manager.get_entries("group:1", "团A")
    assert len(entries) == 0
    ok2, _ = await plugin._resume_log_core(ev("/继续记录", admin=True), "")
    assert ok2
    await plugin.session_log_route(ev("恢复后的行动"))
    entries = await plugin._session_log_manager.get_entries("group:1", "团A")
    assert len(entries) == 1 and entries[0].text == "恢复后的行动"


# ---------------------------------------------------------------------------
# 捕获钩子：玩家消息 + 机器人回复
# ---------------------------------------------------------------------------


@sync_test
async def test_session_log_route_records_with_pre_tag(plugin) -> None:
    await plugin._start_log_core(ev("/开始记录 团A", admin=True), "团A")
    await plugin.session_log_route(ev("我拔出长剑冲向哥布林", sender_name="Alice"))
    await plugin.session_log_route(ev("/r 1d20+5 攻击", sender_name="Bob"))
    entries = await plugin._session_log_manager.get_entries("group:1", "团A")
    assert len(entries) == 2
    assert entries[0].is_roll is False and entries[0].sender_name == "Alice"
    assert entries[1].is_roll is True and entries[1].text == "/r 1d20+5 攻击"


@sync_test
async def test_session_log_route_ignores_when_not_recording(plugin) -> None:
    await plugin.session_log_route(ev("没有开团时的消息"))
    campaigns = await plugin._session_log_manager.list_campaigns("group:1")
    assert campaigns == []


@sync_test
async def test_session_log_bot_hook_records(plugin) -> None:
    await plugin._start_log_core(ev("/开始记录 团A", admin=True), "团A")
    e = ev("")
    e.set_result_text("掷出了 d20+5 = [15]+5 = 20，命中！")
    await plugin.session_log_bot_hook(e)
    entries = await plugin._session_log_manager.get_entries("group:1", "团A")
    assert len(entries) == 1
    assert entries[0].role == "bot" and entries[0].is_roll is True


# ---------------------------------------------------------------------------
# 摘要：LLM 生成 + 缓存 + 重算
# ---------------------------------------------------------------------------


@sync_test
async def test_summarize_core_with_llm_and_cache(plugin) -> None:
    fake = FakeContext(text="剧情回顾：冒险者进入洞窟……\n结算统计：攻击命中。")
    plugin.context = fake

    def llm_calls() -> int:
        return len([c for c in fake.calls if c[0] == "llm"])

    await plugin._start_log_core(ev("/开始记录 团A", admin=True), "团A")
    await plugin.session_log_route(ev("我拔出长剑"))
    await plugin.session_log_route(ev("/r 1d20+5 攻击"))
    ok, msg = await plugin._summarize_core(ev("/总结 团A"), "团A")
    assert ok and "剧情回顾" in msg
    assert llm_calls() == 1
    # 缓存命中：不再调 LLM
    ok2, msg2 = await plugin._summarize_core(ev("/总结 团A"), "团A")
    assert ok2 and "重算" in msg2
    assert llm_calls() == 1
    # 强制重算 → 再调一次
    ok3, msg3 = await plugin._summarize_core(ev("/总结 团A 重算"), "团A 重算")
    assert ok3 and llm_calls() == 2


@sync_test
async def test_summarize_without_llm_env(plugin) -> None:
    """context 为 None（测试替身默认）→ 友好失败，不崩。"""
    assert plugin.context is None
    await plugin._start_log_core(ev("/开始记录 团A", admin=True), "团A")
    await plugin.session_log_route(ev("行动"))
    ok, msg = await plugin._summarize_core(ev("/总结 团A"), "团A")
    assert not ok and "无法调用 LLM" in msg


@sync_test
async def test_summarize_cached_summary_via_记录_摘要(plugin) -> None:
    fake = FakeContext(text="第一场摘要")
    plugin.context = fake
    await plugin._start_log_core(ev("/开始记录 团A", admin=True), "团A")
    await plugin.session_log_route(ev("行动"))
    await plugin._summarize_core(ev("/总结 团A"), "团A")
    # 摘要列表
    ok, msg = await plugin._log_view_core(ev("/记录 摘要 团A"), "摘要 团A")
    assert ok and "第一场摘要" in msg


# ---------------------------------------------------------------------------
# LLM 工具
# ---------------------------------------------------------------------------


@sync_test
async def test_tool_summarize_session(plugin) -> None:
    fake = FakeContext(text="工具生成的摘要")
    plugin.context = fake
    e = ev("/开始记录 团A", admin=True)
    # start（写操作，管理员放行）
    r = await plugin.summarize_session_tool(e, action="start", campaign="团A")
    assert "第 1 场" in r
    # 无权限写操作被拒
    r_denied = await plugin.summarize_session_tool(
        ev("/开始记录 团B", admin=False), action="start", campaign="团B"
    )
    assert "权限" in r_denied
    # 有日志后 summarize
    await plugin.session_log_route(ev("行动开始"))
    r2 = await plugin.summarize_session_tool(e, action="summarize", campaign="团A")
    assert "工具生成的摘要" in r2
    # status
    r3 = await plugin.summarize_session_tool(e, action="status")
    assert "团A" in r3
    # 未知 action
    r4 = await plugin.summarize_session_tool(e, action="nonsense")
    assert "未知的 action" in r4


# ---------------------------------------------------------------------------
# 功能关闭
# ---------------------------------------------------------------------------


@sync_test
async def test_disabled_when_config_off(tmp_path) -> None:
    p = _MemPlugin(config={"enable_session_log": False})
    assert p.session_log_manager is None
    ok, msg = await p._start_log_core(ev("/开始记录 X", admin=True), "X")
    assert not ok and "未启用" in msg


# ---------------------------------------------------------------------------
# 导入（Import）：旧记录倒灌
# ---------------------------------------------------------------------------


def test_parse_transcript_basic() -> None:
    text = (
        "[2026-08-01 20:00] 阿伟: 我推开酒馆大门\n"
        "阿花: 我跟在后面\n"
        "掷出了 d20 = 15\n"
        "\n"
        "阿伟: /r 1d20+5 攻击\n"
        "大家安静点\n"
    )
    entries = parse_transcript(text)
    assert len(entries) == 5  # 空行被剔除
    # [时间] 昵称: 内容 → 提取发送者
    assert entries[0]["sender_name"] == "阿伟"
    assert entries[0]["text"] == "我推开酒馆大门"
    assert entries[0]["is_roll"] is False and entries[0]["role"] == "player"
    # 昵称: 内容
    assert entries[1]["sender_name"] == "阿花"
    # 无发送者的骰式行 → 机器人结算
    assert entries[2]["sender_name"] == ""
    assert entries[2]["is_roll"] is True and entries[2]["role"] == "bot"
    # 有发送者的骰指令 → 玩家且标结算
    assert entries[3]["sender_name"] == "阿伟"
    assert entries[3]["is_roll"] is True and entries[3]["role"] == "player"
    # 无发送者普通行 → 玩家
    assert entries[4]["sender_name"] == ""
    assert entries[4]["is_roll"] is False and entries[4]["role"] == "player"


def test_parse_transcript_max_lines() -> None:
    entries = parse_transcript("\n".join(f"第{i}行内容" for i in range(10)), max_lines=3)
    assert len(entries) == 3


@sync_test
async def test_import_session_creates_campaign_and_appends(tmp_path) -> None:
    mgr = SessionLogManager(tmp_path / "log.db")
    entries = parse_transcript("阿伟: 我推开酒馆大门\n掷出了 d20 = 15\n")
    seq, count = await mgr.import_session("group:1", "旧团", entries)
    assert seq == 1 and count == 2
    campaigns = await mgr.list_campaigns("group:1")
    assert len(campaigns) == 1
    assert campaigns[0].status == "off" and campaigns[0].total_sessions == 1
    # 再导入 → 新场次 2
    seq2, count2 = await mgr.import_session("group:1", "旧团", entries)
    assert seq2 == 2 and count2 == 2
    assert len(await mgr.get_entries("group:1", "旧团", session_seq=2)) == 2
    assert (await mgr.list_campaigns("group:1"))[0].total_sessions == 2
    # 摘要可针对导入场次
    await mgr.save_summary("group:1", "旧团", 2, "旧场摘要")
    assert (await mgr.get_summary("group:1", "旧团", 2)).summary_text == "旧场摘要"


@sync_test
async def test_import_session_does_not_disturb_recording(tmp_path) -> None:
    mgr = SessionLogManager(tmp_path / "log.db")
    await mgr.start("group:1", "进行中")
    await mgr.add_entry("group:1", "player", "正在记录")
    # 导入同团 → 新场次 2，但 recording 指针/状态不动
    seq, count = await mgr.import_session("group:1", "进行中", parse_transcript("旧内容\n"))
    assert seq == 2 and count == 1
    active = await mgr.get_active("group:1")
    assert active is not None and active.status == "recording"
    assert active.session_seq == 1  # 指针仍是第 1 场
    # 继续记录仍写第 1 场
    await mgr.add_entry("group:1", "player", "继续")
    assert len(await mgr.get_entries("group:1", "进行中", session_seq=1)) == 2
    assert len(await mgr.get_entries("group:1", "进行中", session_seq=2)) == 1


@sync_test
async def test_import_command_paste(plugin) -> None:
    e = ev("/导入记录 旧团 阿伟: 我推开酒馆大门\n掷出了 d20 = 15", admin=True)
    out = []
    async for m in plugin.import_log_cmd(e):
        out.append(m)
    assert any("已导入 2 条到团「旧团」第 1 场" in m for m in out)
    entries = await plugin._session_log_manager.get_entries("group:1", "旧团")
    assert len(entries) == 2
    assert entries[1].role == "bot" and entries[1].is_roll is True


@sync_test
async def test_import_command_requires_permission(plugin) -> None:
    e = ev("/导入记录 旧团 阿伟: 内容", admin=False)
    ok, msg = await plugin._import_log_core(e, "旧团 阿伟: 内容")
    assert not ok and "权限" in msg


@sync_test
async def test_import_command_file(tmp_path, plugin) -> None:
    f = tmp_path / "history.txt"
    f.write_text("阿伟: 我推开酒馆大门\n掷出了 d20 = 15\n", encoding="utf-8")
    e = ev("/导入记录 旧团", admin=True)
    e.set_messages([_FakeFileSeg(str(f))])
    ok, msg = await plugin._import_log_core(e, "旧团")
    assert ok and "已导入 2 条" in msg
    entries = await plugin._session_log_manager.get_entries("group:1", "旧团")
    assert len(entries) == 2


@sync_test
async def test_import_then_summarize(plugin) -> None:
    fake = FakeContext(text="旧团导入场次摘要：冒险者进村……")
    plugin.context = fake
    e = ev("/导入记录 旧团 阿伟: 我推开酒馆大门\n阿花: 我拔剑\n掷出了 d20 = 15", admin=True)
    async for _ in plugin.import_log_cmd(e):
        pass
    ok, msg = await plugin._summarize_core(ev("/总结 旧团"), "旧团")
    assert ok and "旧团导入场次摘要" in msg


@sync_test
async def test_import_no_content(plugin) -> None:
    e = ev("/导入记录 旧团", admin=True)
    ok, msg = await plugin._import_log_core(e, "旧团")
    assert not ok and "没有识别到要导入的内容" in msg

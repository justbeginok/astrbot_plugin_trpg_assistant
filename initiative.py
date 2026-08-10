"""
initiative.py — DnD 先攻（Initiative）追踪模块。

提供会话级先攻列表，通过 AstrBot 的 KV 存储持久化。
按 unified_msg_origin（群聊或私聊会话）分别存储。

功能：
  - 掷骰入列 / 固定值录入（数据模型统一为 InitiativeEntry）
  - 降序排列，同值按入列先后（seq）打破平手
  - 回合推进（/init end）：指针轮转 + 轮数计数
  - 单个移除（/init del）：移除当前行动者时指针自动校正
  - 整场清空（/init clr）

为未来角色卡（/char）预留：InitiativeEntry.modifier 字段与 user_id 字段，
角色卡落地后 /ri 无参数时可自动从角色卡取先攻调整值。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent
    from astrbot.api.star import Star

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# KV 存储 key 前缀（PluginKVStoreMixin 已按 plugin_id 隔离命名空间，
# 此前缀仅用于本插件内部区分不同功能，与 history:、session_sides: 等区分）。
_KV_PREFIX = "initiative:"

# 单位名称最大长度，超出截断并追加省略号。
_NAME_MAX = 30

# 需要从名称中剔除的控制字符正则（换行、回车、制表符等）。
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_name(text: str) -> str:
    """剔除名称中的控制字符并截断至 _NAME_MAX 字符，防止伪造多行输出。"""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text).strip()
    if len(cleaned) > _NAME_MAX:
        cleaned = cleaned[:_NAME_MAX] + "…"
    return cleaned


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class InitiativeEntry:
    """先攻列表中的单个单位。"""

    name: str  # 单位名称（玩家昵称或 DM 指定的怪物名）
    value: int  # 先攻总值
    modifier: int = 0  # 先攻调整值（为角色卡联动预留）
    user_id: str = ""  # 发送者 ID（为角色卡联动预留；怪物为空）
    is_fixed: bool = False  # True=固定值录入，False=掷骰入列
    seq: int = 0  # 入列序号，同先攻值时先报先动（升序）

    # ------------------------------------------------------------------
    # 序列化辅助方法
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转换为可写入 KV 存储的字典。"""
        return {
            "name": self.name,
            "value": self.value,
            "modifier": self.modifier,
            "user_id": self.user_id,
            "is_fixed": self.is_fixed,
            "seq": self.seq,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InitiativeEntry:
        """从 KV 存储读取的字典中还原记录，容忍缺失字段与脏数据类型。"""
        try:
            value = int(data.get("value", 0))
        except (TypeError, ValueError):
            value = 0
        try:
            modifier = int(data.get("modifier", 0))
        except (TypeError, ValueError):
            modifier = 0
        try:
            seq = int(data.get("seq", 0))
        except (TypeError, ValueError):
            seq = 0
        return cls(
            name=str(data.get("name", "")),
            value=value,
            modifier=modifier,
            user_id=str(data.get("user_id", "")),
            is_fixed=bool(data.get("is_fixed", False)),
            seq=seq,
        )


@dataclass
class InitiativeState:
    """单个会话的先攻状态。"""

    entries: list[InitiativeEntry] = field(default_factory=list)
    current_seq: int | None = None  # 当前行动单位的 seq；None=战斗尚未开始
    round: int = 0  # 当前轮数；0=尚未开始

    # ------------------------------------------------------------------
    # 排序与查询
    # ------------------------------------------------------------------

    def sorted_entries(self) -> list[InitiativeEntry]:
        """按先攻值降序排列，同值按 seq 升序（先报先动）。"""
        return sorted(self.entries, key=lambda e: (-e.value, e.seq))

    def get_current(self) -> InitiativeEntry | None:
        """返回当前行动单位；战斗未开始或条目缺失时返回 None。"""
        if self.current_seq is None:
            return None
        for entry in self.entries:
            if entry.seq == self.current_seq:
                return entry
        return None

    # ------------------------------------------------------------------
    # 序列化辅助方法
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """转换为可写入 KV 存储的字典。"""
        return {
            "entries": [e.to_dict() for e in self.entries],
            "current_seq": self.current_seq,
            "round": self.round,
        }

    @classmethod
    def from_dict(cls, data: dict) -> InitiativeState:
        """从 KV 存储读取的字典中还原状态，容忍缺失字段与脏数据。"""
        raw_entries = data.get("entries", [])
        entries = (
            [InitiativeEntry.from_dict(d) for d in raw_entries if isinstance(d, dict)]
            if isinstance(raw_entries, list)
            else []
        )
        try:
            current_seq = int(data["current_seq"]) if data.get("current_seq") is not None else None
        except (TypeError, ValueError):
            current_seq = None
        try:
            round_num = int(data.get("round", 0))
        except (TypeError, ValueError):
            round_num = 0
        return cls(entries=entries, current_seq=current_seq, round=round_num)


@dataclass
class AdvanceResult:
    """回合推进的结果，供调用方拼装播报消息。"""

    state: InitiativeState  # 推进后的完整状态
    current: InitiativeEntry | None  # 推进后当前行动单位；列表为空时 None
    previous: InitiativeEntry | None  # 推进前行动单位（战斗刚开始时 None）
    wrapped: bool = False  # 是否绕回列表顶部（轮数 +1）
    started: bool = False  # 是否从"未开始"首次进入战斗


@dataclass
class RemoveResult:
    """移除单位的结果。"""

    state: InitiativeState  # 移除后的完整状态
    removed: InitiativeEntry | None  # 被移除的单位；未找到时 None
    next_current: InitiativeEntry | None  # 移除的是当前行动者时的接力者；非 None 表示指针已移动


# ---------------------------------------------------------------------------
# 先攻管理器
# ---------------------------------------------------------------------------


class InitiativeManager:
    """基于 AstrBot KV 存储的会话级先攻管理器。"""

    def __init__(self, star: Star) -> None:
        self._star = star
        # 单把管理器级锁，保证 add/advance/remove/clear 的读-改-写互斥
        # （与 RollHistoryManager 的锁策略一致，操作只是几次 KV 调用）。
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 内部 KV 读写
    # ------------------------------------------------------------------

    async def _load(self, origin: str) -> InitiativeState:
        try:
            raw = await self._star.get_kv_data(_KV_PREFIX + origin, None)
            if isinstance(raw, dict):
                return InitiativeState.from_dict(raw)
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 读取先攻状态失败: {e}")
        except Exception as e:
            logger.warning(f"[trpg_assistant] 读取先攻状态时发生未预期异常: {e}")
        return InitiativeState()

    async def _save(self, origin: str, state: InitiativeState) -> None:
        try:
            await self._star.put_kv_data(_KV_PREFIX + origin, state.to_dict())
        except (OSError, ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"[trpg_assistant] 写入先攻状态失败: {e}")
        except Exception as e:
            logger.warning(f"[trpg_assistant] 写入先攻状态时发生未预期异常: {e}")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def get_state(self, event: AstrMessageEvent) -> InitiativeState:
        """返回当前会话的先攻状态（只读）。"""
        return await self._load(event.unified_msg_origin)

    async def add(
        self,
        event: AstrMessageEvent,
        name: str,
        value: int,
        modifier: int = 0,
        user_id: str = "",
        is_fixed: bool = False,
    ) -> tuple[InitiativeState, InitiativeEntry]:
        """将一个单位加入当前会话的先攻列表，返回 (新状态, 新增条目)。"""
        origin = event.unified_msg_origin
        entry = InitiativeEntry(
            name=_sanitize_name(name),
            value=value,
            modifier=modifier,
            user_id=_sanitize_name(user_id),
            is_fixed=is_fixed,
            seq=0,  # 加锁内赋值
        )
        async with self._lock:
            state = await self._load(origin)
            entry.seq = max((e.seq for e in state.entries), default=0) + 1
            state.entries.append(entry)
            await self._save(origin, state)
        return state, entry

    async def advance(self, event: AstrMessageEvent) -> AdvanceResult:
        """推进到下一个单位的回合。

        未开始时：从先攻最高者开始，轮数置 1。
        已开始：移动到下一位；若当前已是最后一位则绕回顶部，轮数 +1。
        列表为空：返回 current=None，状态不变。
        """
        origin = event.unified_msg_origin
        async with self._lock:
            state = await self._load(origin)
            entries = state.sorted_entries()
            if not entries:
                return AdvanceResult(state=state, current=None, previous=None)

            previous = state.get_current()
            if previous is None:
                # 战斗尚未开始：从先攻最高者开始。
                state.current_seq = entries[0].seq
                state.round = 1
                result = AdvanceResult(
                    state=state,
                    current=entries[0],
                    previous=None,
                    started=True,
                )
                await self._save(origin, state)
                return result

            current_pos = next(
                (i for i, e in enumerate(entries) if e.seq == previous.seq), None
            )
            if current_pos is None:
                # 状态损坏（当前行动者不在列表中）：按重新开始处理。
                state.current_seq = entries[0].seq
                state.round = 1
                result = AdvanceResult(
                    state=state,
                    current=entries[0],
                    previous=None,
                    started=True,
                )
                await self._save(origin, state)
                return result

            if current_pos < len(entries) - 1:
                nxt = entries[current_pos + 1]
                state.current_seq = nxt.seq
                result = AdvanceResult(
                    state=state, current=nxt, previous=previous, wrapped=False
                )
            else:
                nxt = entries[0]
                state.current_seq = nxt.seq
                state.round += 1
                result = AdvanceResult(
                    state=state, current=nxt, previous=previous, wrapped=True
                )
            await self._save(origin, state)
            return result

    async def remove(
        self, event: AstrMessageEvent, name: str
    ) -> RemoveResult:
        """按名称移除先攻列表中的单位（同名时移除最早入列的一个）。

        若移除的是当前行动者，指针自动移至「原本排在它之后」的第一位
        （先攻值更低，或同值但更晚入列）；若被移除者是列表末位则绕回
        顶部并轮数 +1。列表清空后 current_seq/round 复位。
        """
        origin = event.unified_msg_origin
        target = _sanitize_name(name)
        async with self._lock:
            state = await self._load(origin)
            if not state.entries:
                return RemoveResult(state=state, removed=None, next_current=None)

            rm_idx = next(
                (i for i, e in enumerate(state.entries) if e.name == target), None
            )
            if rm_idx is None:
                return RemoveResult(state=state, removed=None, next_current=None)

            removed = state.entries.pop(rm_idx)
            was_current = state.current_seq == removed.seq

            if not state.entries:
                state.current_seq = None
                state.round = 0
                await self._save(origin, state)
                return RemoveResult(state=state, removed=removed, next_current=None)

            next_current: InitiativeEntry | None = None
            if was_current:
                entries = state.sorted_entries()
                # 后继者 = 排序后紧随被移除者之后的第一位：
                # 先攻值更小，或先攻值相同但更晚入列（seq 更大）。
                # 排序键为 (-value, seq)，故按 (-e.value, e.seq) 字典序比较。
                successor = next(
                    (
                        e
                        for e in entries
                        if (-e.value, e.seq) > (-removed.value, removed.seq)
                    ),
                    None,
                )
                if successor is not None:
                    state.current_seq = successor.seq
                    next_current = successor
                else:
                    # 被移除者是当前列表末位：绕回顶部，轮数 +1。
                    state.current_seq = entries[0].seq
                    state.round += 1
                    next_current = entries[0]

            await self._save(origin, state)
            return RemoveResult(
                state=state, removed=removed, next_current=next_current
            )

    async def clear(self, event: AstrMessageEvent) -> int:
        """清空当前会话的先攻列表，返回被清除的单位数。"""
        origin = event.unified_msg_origin
        async with self._lock:
            state = await self._load(origin)
            count = len(state.entries)
            await self._star.delete_kv_data(_KV_PREFIX + origin)
            return count

    # ------------------------------------------------------------------
    # 格式化辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def format_list(state: InitiativeState) -> str:
        """将先攻状态渲染为先攻列表文本。"""
        entries = state.sorted_entries()
        if not entries:
            return "先攻列表为空。使用 /ri 掷先攻入列，/init 查看列表。"

        lines: list[str] = []
        header = f"⚔️ 先攻列表（{len(entries)} 个单位"
        if state.round > 0:
            header += f"，第 {state.round} 轮"
        header += "）："
        lines.append(header)
        for rank, entry in enumerate(entries, 1):
            marker = "▶" if state.current_seq == entry.seq else "　"
            lines.append(f"{marker} {rank}. {_sanitize_name(entry.name)}　先攻 {entry.value}")
        if state.current_seq is None:
            lines.append("战斗尚未开始，发送 /init end 开始回合。")
        return "\n".join(lines)

    @staticmethod
    def format_advance(result: AdvanceResult) -> str:
        """将推进结果渲染为播报文本。"""
        if result.current is None:
            return "先攻列表为空，无法推进回合。先使用 /ri 掷先攻入列。"
        parts: list[str] = []
        if result.started:
            parts.append("⚔️ 战斗开始！")
        if result.wrapped:
            parts.append(f"—— 第 {result.state.round} 轮开始 ——")
        if result.previous is not None:
            parts.append(f"✅ {_sanitize_name(result.previous.name)} 的回合结束。")
        parts.append(
            f"现在轮到 **{_sanitize_name(result.current.name)}**（先攻 {result.current.value}）行动。"
        )
        return "\n".join(parts)

    @staticmethod
    def format_entry_confirmation(entry: InitiativeEntry) -> str:
        """将单个条目渲染为入列确认文本（掷骰入列时展示明细）。"""
        if entry.is_fixed:
            return f"已录入先攻：**{_sanitize_name(entry.name)}** → 先攻 {entry.value}"
        if entry.modifier:
            return (
                f"🎲 {_sanitize_name(entry.name)} 的先攻掷骰："
                f"d20{entry.modifier:+d} → **{entry.value}**"
            )
        return f"🎲 {_sanitize_name(entry.name)} 的先攻掷骰：d20 → **{entry.value}**"

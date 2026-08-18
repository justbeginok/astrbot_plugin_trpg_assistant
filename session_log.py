"""
session_log.py — 跑团记录（团 / 场次 / 摘要）管理模块。

记录「开始记录 → 结束记录」之间的完整对话流（玩家消息 + 机器人回复），
按「团名 + 会话来源（origin）」隔离；团内每次「开始 → 结束」为一个场次
（session_seq），同一团可跨多次开团累积。

持久化：独立 SQLite 库 `data_dir/trpg_log.db`（ADR-0029，可变数据第二存储，
不走 AstrBot KV）。追加式、按 (origin, campaign, session_seq) 查询，
摘要前取「最近 N 条」走 SQL LIMIT。

权限模型：开始/暂停/继续/结束/删除为写操作（群聊需白名单/管理员）；
查看与摘要全员可用。本模块不关心权限，由命令层（main.py）鉴权。
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 库文件名（置于 StarTools.get_data_dir() 下，与 trpg_homebrew/ 并排）
_DB_FILENAME = "trpg_log.db"

_SCHEMA_VERSION = 1

# 查看日志单次最多显示的条数
_MAX_VIEW = 20

# 单条消息入库的最大字符数（超出截断，防脏数据撑爆行）
_MAX_ENTRY_CHARS = 2000

# 摘要喂给 LLM 的最大字符数（超出取「最近窗口」，前端注明截断）
_MAX_SUMMARY_CHARS = 8000

# 摘要生成时一次喂入的最大日志条数（与字符上限双保险）
_MAX_SUMMARY_ENTRIES = 400

# 需要从入库文本中剔除的控制字符
_CONTROL_CHARS_RE = re.compile(r"[\r\n\t\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 团/场次状态
_STATUS_RECORDING = "recording"  # 正在记录（消息入日志）
_STATUS_PAUSED = "paused"        # 已暂停（消息不入日志，场次未结束）
_STATUS_OFF = "off"              # 已结束（场次关闭，数据保留）


def _now() -> str:
    """日志时间戳（完整日期，团可跨天）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005


def _sanitize(text: str, max_chars: int = _MAX_ENTRY_CHARS) -> str:
    """剔除控制字符并截断，防止换行伪造日志行。"""
    cleaned = _CONTROL_CHARS_RE.sub(" ", text).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + "…"
    return cleaned


# ---------------------------------------------------------------------------
# 结算启发式（写入时预标，捕获钩子与导入共用；在 main.py 中经
# `from .session_log import _looks_like_roll_command` 复用，避免循环导入）
# ---------------------------------------------------------------------------

# 玩家消息「结算」形态：骰指令（/r、.r、/roll、/dnd，前缀符号任意）。
# 前缀符号取 0~2 个非单词非空白字符，然后必须是 r/roll/dnd 整词后接空白或结尾
# （避免误伤 /ri、/rh、/记录 等）。
_ROLL_CMD_RE = re.compile(r"^[^\w\s]{0,2}(?:r|roll|dnd)(?=\s|$)", re.IGNORECASE)
# 机器人消息「结算」形态：含骰式（1d20/d20/3d6）或「掷出」字样。
_ROLL_TEXT_RE = re.compile(r"\b\d*d\d+\b")

# 导入切分：可选 [时间] 前缀 + 短昵称 + 冒号
_IMPORT_SENDER_RE = re.compile(r"^(?:\[[^\]]*\]\s*)?([^：:\n]{1,16})[：:]\s*(.+)$")
# 导入最大行数（防超大粘贴/刷库）
_MAX_IMPORT_LINES = 2000


def _looks_like_roll_command(text: str) -> bool:
    """文本是否像掷骰指令（用于结算预标）。"""
    if not text:
        return False
    return bool(_ROLL_CMD_RE.match(text))


def _looks_like_roll_result(text: str) -> bool:
    """文本是否像掷骰结果（用于结算预标）。"""
    return bool(_ROLL_TEXT_RE.search(text)) or "掷出" in text


def parse_transcript(text: str, max_lines: int = _MAX_IMPORT_LINES) -> list[dict]:
    """把既有纯文本聊天记录按行切分为日志条目（导入用，规则切分）。

    每行一条：
    - 命中「[时间] 昵称: 内容」/「昵称: 内容」→ 提取发送者；
    - 否则整行作为内容（发送者为空）；
    - 骰式/「掷出」行标 is_roll=True；无发送者的骰式行视为机器人结算（role=bot）；
    - 超 max_lines 截断（防超大粘贴/刷库）。

    Returns:
        [{"role", "sender_id", "sender_name", "text", "is_roll"}, ...]
    """
    entries: list[dict] = []
    for line in text.splitlines():
        line = _sanitize(line)
        if not line:
            continue
        if len(entries) >= max_lines:
            break
        m = _IMPORT_SENDER_RE.match(line)
        if m:
            sender = _sanitize(m.group(1), 16)
            content = _sanitize(m.group(2))
        else:
            sender = ""
            content = line
        if not content:
            continue
        is_roll = _looks_like_roll_command(content) or _looks_like_roll_result(content)
        role = "bot" if (is_roll and not sender) else "player"
        entries.append(
            {
                "role": role,
                "sender_id": "",
                "sender_name": sender,
                "text": content,
                "is_roll": is_roll,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class LogEntry:
    """跑团日志中的一条消息。"""

    seq: int
    ts: str
    role: str  # 'player' | 'bot'
    sender_id: str
    sender_name: str
    text: str
    is_roll: bool  # 结算预标（玩家骰指令 / 机器人骰结果形态）


@dataclass
class CampaignInfo:
    """一个团（记录容器）的概要信息。"""

    campaign: str
    session_seq: int  # 当前/最近场次
    status: str       # recording | paused | off
    started_at: str
    session_count: int   # 当前场次的消息条数
    total_count: int     # 团内全部消息条数
    total_sessions: int  # 团内场次数


@dataclass
class SummaryRow:
    """一场已生成的摘要。"""

    campaign: str
    session_seq: int
    summary_text: str
    created_at: str


# ---------------------------------------------------------------------------
# 管理器
# ---------------------------------------------------------------------------


class SessionLogManager:
    """基于独立 SQLite 的跑团日志管理器。

    所有读-改-写在同一把 asyncio.Lock 内完成（与 history/inventory 等
    Manager 同构）；连接懒加载、常驻复用，WAL 模式。
    """

    def __init__(
        self,
        db_path: Path,
        max_summary_chars: int = _MAX_SUMMARY_CHARS,
        max_summary_entries: int = _MAX_SUMMARY_ENTRIES,
    ) -> None:
        self._db_path = Path(db_path)
        self._max_summary_chars = max_summary_chars
        self._max_summary_entries = max_summary_entries
        self._lock: asyncio.Lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:  # pragma: no cover - 数据目录不可写
            logger.warning(f"[trpg_assistant] 创建日志数据目录失败: {e}")

    # ------------------------------------------------------------------
    # 连接与 schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._ensure_schema(conn)
            self._conn = conn
        return self._conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS campaigns(
              origin TEXT NOT NULL,
              campaign TEXT NOT NULL,
              session_seq INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'off',
              started_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(origin, campaign)
            );
            CREATE TABLE IF NOT EXISTS log_entries(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              origin TEXT NOT NULL,
              campaign TEXT NOT NULL,
              session_seq INTEGER NOT NULL,
              seq INTEGER NOT NULL,
              ts TEXT NOT NULL,
              role TEXT NOT NULL,
              sender_id TEXT NOT NULL DEFAULT '',
              sender_name TEXT NOT NULL DEFAULT '',
              text TEXT NOT NULL,
              is_roll INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_log_lookup
              ON log_entries(origin, campaign, session_seq, seq);
            CREATE TABLE IF NOT EXISTS summaries(
              origin TEXT NOT NULL,
              campaign TEXT NOT NULL,
              session_seq INTEGER NOT NULL,
              summary_text TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(origin, campaign, session_seq)
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )
        conn.commit()

    def close(self) -> None:
        """关闭底层连接（插件卸载时调用；未打开时无操作）。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # 团 / 场次生命周期
    # ------------------------------------------------------------------

    async def start(
        self,
        origin: str,
        campaign: str,
    ) -> tuple[bool, str]:
        """开始（或继续）记录一个团。

        - 该团 paused → 恢复，续写当前场次；
        - 该团 off / 不存在 → 开新场次；
        - 该团 recording → 返回已在记录；
        - 同 origin 已有其他团在 recording/paused → 拒绝（同一会话同一时间
          只允许一条进行中的记录，保证捕获路由无歧义）。
        """
        campaign = _sanitize(campaign, max_chars=40)
        if not campaign:
            return False, "团名不能为空。"
        async with self._lock:
            conn = self._connect()
            now = _now()
            active = conn.execute(
                "SELECT campaign, status FROM campaigns "
                "WHERE origin=? AND status IN ('recording','paused')",
                (origin,),
            ).fetchone()
            if active is not None and active["campaign"] != campaign:
                return False, (
                    f"当前已在记录团「{active['campaign']}」，同一会话同一时间只能"
                    f"记录一个团，请先 /结束记录 或 /暂停记录。"
                )
            row = conn.execute(
                "SELECT session_seq, status FROM campaigns "
                "WHERE origin=? AND campaign=?",
                (origin, campaign),
            ).fetchone()
            if row is not None:
                if row["status"] == _STATUS_RECORDING:
                    return False, f"团「{campaign}」已在记录中（第 {row['session_seq']} 场）。"
                if row["status"] == _STATUS_PAUSED:
                    conn.execute(
                        "UPDATE campaigns SET status=?, updated_at=? "
                        "WHERE origin=? AND campaign=?",
                        (_STATUS_RECORDING, now, origin, campaign),
                    )
                    conn.commit()
                    return True, f"已继续记录团「{campaign}」（第 {row['session_seq']} 场）。"
                session_seq = int(row["session_seq"]) + 1
                conn.execute(
                    "UPDATE campaigns SET status=?, session_seq=?, started_at=?, "
                    "updated_at=? WHERE origin=? AND campaign=?",
                    (_STATUS_RECORDING, session_seq, now, now, origin, campaign),
                )
                conn.commit()
                return True, f"已开始记录团「{campaign}」第 {session_seq} 场。"
            conn.execute(
                "INSERT INTO campaigns(origin, campaign, session_seq, status, "
                "started_at, updated_at) VALUES(?,?,?,?,?,?)",
                (origin, campaign, 1, _STATUS_RECORDING, now, now),
            )
            conn.commit()
            return True, f"已新建团「{campaign}」并开始记录第 1 场。"

    async def pause(self, origin: str, campaign: str | None = None) -> tuple[bool, str]:
        """暂停记录（消息不再入日志，场次保留）。campaign 缺省取当前进行中的团。"""
        async with self._lock:
            conn = self._connect()
            target = await self._resolve_active_locked(conn, origin, campaign)
            if target is None:
                return False, "当前没有进行中的记录。"
            name, session_seq = target
            if name != campaign and campaign is not None:
                return False, f"团「{campaign}」当前没有进行中的记录。"
            conn.execute(
                "UPDATE campaigns SET status=? WHERE origin=? AND campaign=?",
                (_STATUS_PAUSED, origin, name),
            )
            conn.commit()
            return True, f"已暂停记录团「{name}」（第 {session_seq} 场），之后的消息不再入日志。"

    async def stop(self, origin: str, campaign: str | None = None) -> tuple[bool, str]:
        """结束记录（关闭当前场次，数据保留）。"""
        async with self._lock:
            conn = self._connect()
            target = await self._resolve_active_locked(conn, origin, campaign)
            if target is None:
                return False, "当前没有进行中的记录。"
            name, session_seq = target
            if name != campaign and campaign is not None:
                return False, f"团「{campaign}」当前没有进行中的记录。"
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM log_entries "
                "WHERE origin=? AND campaign=? AND session_seq=?",
                (origin, name, session_seq),
            ).fetchone()["c"]
            conn.execute(
                "UPDATE campaigns SET status=? WHERE origin=? AND campaign=?",
                (_STATUS_OFF, origin, name),
            )
            conn.commit()
            return True, f"已结束记录团「{name}」第 {session_seq} 场（共 {count} 条）。数据已保留。"

    async def delete_campaign(self, origin: str, campaign: str) -> int:
        """删除一个团的全部数据（日志 + 摘要 + 团记录），返回删除的日志条数。"""
        async with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM log_entries WHERE origin=? AND campaign=?",
                (origin, campaign),
            ).fetchone()
            count = int(row["c"])
            conn.execute(
                "DELETE FROM log_entries WHERE origin=? AND campaign=?",
                (origin, campaign),
            )
            conn.execute(
                "DELETE FROM summaries WHERE origin=? AND campaign=?",
                (origin, campaign),
            )
            conn.execute(
                "DELETE FROM campaigns WHERE origin=? AND campaign=?",
                (origin, campaign),
            )
            conn.commit()
            return count

    async def _resolve_active_locked(
        self,
        conn: sqlite3.Connection,
        origin: str,
        campaign: str | None,
    ) -> tuple[str, int] | None:
        """锁内解析「进行中（recording/paused）的团」。campaign 给定则限定该团。"""
        if campaign:
            row = conn.execute(
                "SELECT campaign, session_seq, status FROM campaigns "
                "WHERE origin=? AND campaign=? AND status IN ('recording','paused')",
                (origin, campaign),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT campaign, session_seq, status FROM campaigns "
                "WHERE origin=? AND status IN ('recording','paused') "
                "ORDER BY updated_at DESC LIMIT 1",
                (origin,),
            ).fetchone()
        if row is None:
            return None
        return row["campaign"], int(row["session_seq"])

    # ------------------------------------------------------------------
    # 消息捕获
    # ------------------------------------------------------------------

    async def add_entry(
        self,
        origin: str,
        role: str,
        text: str,
        sender_id: str = "",
        sender_name: str = "",
        is_roll: bool = False,
    ) -> bool:
        """把一条消息追加到当前 recording 的团（场次）。未在记录则忽略。

        Returns:
            True 写入成功；False 当前无 recording 状态（静默丢弃）。
        """
        text = _sanitize(text)
        if not text:
            return False
        async with self._lock:
            conn = self._connect()
            active = conn.execute(
                "SELECT campaign, session_seq FROM campaigns "
                "WHERE origin=? AND status=?",
                (origin, _STATUS_RECORDING),
            ).fetchone()
            if active is None:
                return False
            campaign, session_seq = active["campaign"], int(active["session_seq"])
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM log_entries "
                "WHERE origin=? AND campaign=? AND session_seq=?",
                (origin, campaign, session_seq),
            ).fetchone()
            next_seq = int(row["m"]) + 1
            conn.execute(
                "INSERT INTO log_entries(origin, campaign, session_seq, seq, ts, "
                "role, sender_id, sender_name, text, is_roll) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    origin,
                    campaign,
                    session_seq,
                    next_seq,
                    _now(),
                    role,
                    _sanitize(sender_id, 64),
                    _sanitize(sender_name, 64),
                    text,
                    1 if is_roll else 0,
                ),
            )
            conn.commit()
            return True

    async def import_session(
        self,
        origin: str,
        campaign: str,
        entries: list[dict],
    ) -> tuple[int, int]:
        """把既有记录批量导入为团的一个新场次（不依赖记录状态）。

        规则：campaign 不存在则创建（status='off'）；新场次号 =
        max(log_entries.session_seq) + 1（无则 1）；仅当团处于 off（无进行中
        场次）时才同步 campaigns.session_seq 指针——**recording/paused 的团
        不动指针**，避免破坏正在记录的场次（add_entry 靠该字段定位）。

        Args:
            entries: parse_transcript 产物
                [{"role","sender_id","sender_name","text","is_roll"}, ...]。

        Returns:
            (new_session_seq, 导入条数)。
        """
        campaign = _sanitize(campaign, max_chars=40)
        if not campaign or not entries:
            return 0, 0
        async with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                row = conn.execute(
                    "SELECT session_seq, status FROM campaigns "
                    "WHERE origin=? AND campaign=?",
                    (origin, campaign),
                ).fetchone()
                max_row = conn.execute(
                    "SELECT COALESCE(MAX(session_seq), 0) AS m FROM log_entries "
                    "WHERE origin=? AND campaign=?",
                    (origin, campaign),
                ).fetchone()
                new_seq = int(max_row["m"]) + 1 if max_row else 1
                now = _now()
                if row is None:
                    conn.execute(
                        "INSERT INTO campaigns(origin, campaign, session_seq, status, "
                        "started_at, updated_at) VALUES(?,?,?,?,?,?)",
                        (origin, campaign, new_seq, _STATUS_OFF, now, now),
                    )
                elif row["status"] == _STATUS_OFF:
                    conn.execute(
                        "UPDATE campaigns SET session_seq=?, updated_at=? "
                        "WHERE origin=? AND campaign=?",
                        (new_seq, now, origin, campaign),
                    )
                seq = 0
                count = 0
                for e in entries:
                    text = _sanitize(e.get("text", ""))
                    if not text:
                        continue
                    seq += 1
                    conn.execute(
                        "INSERT INTO log_entries(origin, campaign, session_seq, seq, ts, "
                        "role, sender_id, sender_name, text, is_roll) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            origin,
                            campaign,
                            new_seq,
                            seq,
                            now,
                            e.get("role", "player"),
                            _sanitize(e.get("sender_id", ""), 64),
                            _sanitize(e.get("sender_name", ""), 64),
                            text,
                            1 if e.get("is_roll") else 0,
                        ),
                    )
                    count += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return new_seq, count

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_active(self, origin: str) -> CampaignInfo | None:
        """返回当前进行中（recording/paused）的团（至多一个）。"""
        async with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT c.campaign, c.session_seq, c.status, c.started_at, "
                "(SELECT COUNT(*) FROM log_entries e "
                "  WHERE e.origin=c.origin AND e.campaign=c.campaign "
                "  AND e.session_seq=c.session_seq) AS sc, "
                "(SELECT COUNT(*) FROM log_entries e "
                "  WHERE e.origin=c.origin AND e.campaign=c.campaign) AS tc, "
                "(SELECT COUNT(DISTINCT e.session_seq) FROM log_entries e "
                "  WHERE e.origin=c.origin AND e.campaign=c.campaign) AS ts "
                "FROM campaigns c WHERE c.origin=? "
                "AND c.status IN ('recording','paused') "
                "ORDER BY c.updated_at DESC LIMIT 1",
                (origin,),
            ).fetchone()
            return self._row_to_campaign(row) if row is not None else None

    async def list_campaigns(self, origin: str) -> list[CampaignInfo]:
        """列出该会话全部团及其概要。"""
        async with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT c.campaign, c.session_seq, c.status, c.started_at, "
                "(SELECT COUNT(*) FROM log_entries e "
                "  WHERE e.origin=c.origin AND e.campaign=c.campaign "
                "  AND e.session_seq=c.session_seq) AS sc, "
                "(SELECT COUNT(*) FROM log_entries e "
                "  WHERE e.origin=c.origin AND e.campaign=c.campaign) AS tc, "
                "(SELECT COUNT(DISTINCT e.session_seq) FROM log_entries e "
                "  WHERE e.origin=c.origin AND e.campaign=c.campaign) AS ts "
                "FROM campaigns c WHERE c.origin=? "
                "ORDER BY c.updated_at DESC",
                (origin,),
            ).fetchall()
            return [self._row_to_campaign(r) for r in rows]

    @staticmethod
    def _row_to_campaign(row: Any) -> CampaignInfo:
        return CampaignInfo(
            campaign=str(row["campaign"]),
            session_seq=int(row["session_seq"]),
            status=str(row["status"]),
            started_at=str(row["started_at"]),
            session_count=int(row["sc"]),
            total_count=int(row["tc"]),
            total_sessions=int(row["ts"]),
        )

    async def get_latest_session_seq(self, origin: str, campaign: str) -> int | None:
        """团内最近场次号（无数据返回 None）。"""
        async with self._lock:
            conn = self._connect()
            return self._latest_session_seq_locked(conn, origin, campaign)

    @staticmethod
    def _latest_session_seq_locked(
        conn: sqlite3.Connection,
        origin: str,
        campaign: str,
    ) -> int | None:
        """锁内查询最近场次号（供 get_entries 复用，避免重复取锁死锁）。"""
        row = conn.execute(
            "SELECT MAX(session_seq) AS m FROM log_entries "
            "WHERE origin=? AND campaign=?",
            (origin, campaign),
        ).fetchone()
        return int(row["m"]) if row is not None and row["m"] is not None else None

    async def get_entries(
        self,
        origin: str,
        campaign: str,
        session_seq: int | None = None,
        limit: int | None = None,
    ) -> list[LogEntry]:
        """取团内日志。session_seq 缺省取最近一场；limit 只取最近 N 条。"""
        async with self._lock:
            conn = self._connect()
            if session_seq is None:
                latest = self._latest_session_seq_locked(conn, origin, campaign)
                if latest is None:
                    return []
                session_seq = latest
            params: tuple[Any, ...] = (origin, campaign, session_seq)
            sql = (
                "SELECT seq, ts, role, sender_id, sender_name, text, is_roll "
                "FROM log_entries WHERE origin=? AND campaign=? AND session_seq=? "
                "ORDER BY seq ASC"
            )
            if limit is not None:
                sql = (
                    "SELECT * FROM ("
                    "SELECT seq, ts, role, sender_id, sender_name, text, is_roll "
                    "FROM log_entries WHERE origin=? AND campaign=? AND session_seq=? "
                    "ORDER BY seq DESC LIMIT ?) ORDER BY seq ASC"
                )
                params = (origin, campaign, session_seq, int(limit))
            rows = conn.execute(sql, params).fetchall()
            return [
                LogEntry(
                    seq=int(r["seq"]),
                    ts=str(r["ts"]),
                    role=str(r["role"]),
                    sender_id=str(r["sender_id"]),
                    sender_name=str(r["sender_name"]),
                    text=str(r["text"]),
                    is_roll=bool(r["is_roll"]),
                )
                for r in rows
            ]

    async def count_entries(
        self,
        origin: str,
        campaign: str,
        session_seq: int | None = None,
    ) -> int:
        """统计日志条数。session_seq 缺省统计全部场次。"""
        async with self._lock:
            conn = self._connect()
            if session_seq is None:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM log_entries "
                    "WHERE origin=? AND campaign=?",
                    (origin, campaign),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM log_entries "
                    "WHERE origin=? AND campaign=? AND session_seq=?",
                    (origin, campaign, session_seq),
                ).fetchone()
            return int(row["c"]) if row is not None else 0

    # ------------------------------------------------------------------
    # 摘要
    # ------------------------------------------------------------------

    async def save_summary(
        self,
        origin: str,
        campaign: str,
        session_seq: int,
        summary_text: str,
    ) -> None:
        async with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR REPLACE INTO summaries(origin, campaign, session_seq, "
                "summary_text, created_at) VALUES(?,?,?,?,?)",
                (origin, campaign, session_seq, summary_text, _now()),
            )
            conn.commit()

    async def get_summary(
        self,
        origin: str,
        campaign: str,
        session_seq: int,
    ) -> SummaryRow | None:
        async with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT campaign, session_seq, summary_text, created_at "
                "FROM summaries WHERE origin=? AND campaign=? AND session_seq=?",
                (origin, campaign, session_seq),
            ).fetchone()
            if row is None:
                return None
            return SummaryRow(
                campaign=str(row["campaign"]),
                session_seq=int(row["session_seq"]),
                summary_text=str(row["summary_text"]),
                created_at=str(row["created_at"]),
            )

    async def list_summaries(
        self,
        origin: str,
        campaign: str,
    ) -> list[SummaryRow]:
        async with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT campaign, session_seq, summary_text, created_at "
                "FROM summaries WHERE origin=? AND campaign=? "
                "ORDER BY session_seq DESC",
                (origin, campaign),
            ).fetchall()
            return [
                SummaryRow(
                    campaign=str(r["campaign"]),
                    session_seq=int(r["session_seq"]),
                    summary_text=str(r["summary_text"]),
                    created_at=str(r["created_at"]),
                )
                for r in rows
            ]

    # ------------------------------------------------------------------
    # 格式化与提示词
    # ------------------------------------------------------------------

    @staticmethod
    def _status_cn(status: str) -> str:
        return {
            _STATUS_RECORDING: "记录中",
            _STATUS_PAUSED: "已暂停",
            _STATUS_OFF: "已结束",
        }.get(status, status)

    @staticmethod
    def format_status(active: CampaignInfo | None, campaigns: list[CampaignInfo]) -> str:
        """「/记录」无参：当前状态 + 团列表。"""
        if active is None:
            head = "当前没有进行中的记录。"
        else:
            head = (
                f"正在记录：团「{active.campaign}」第 {active.session_seq} 场"
                f"（{SessionLogManager._status_cn(active.status)}，"
                f"本场 {active.session_count} 条 / 累计 {active.total_count} 条）。"
            )
        if not campaigns:
            return head + "\n还没有任何团。用 /开始记录 <团名> 开团。"
        lines = [f"已记录团（{len(campaigns)} 个）："]
        for c in campaigns:
            lines.append(
                f"- 「{c.campaign}」共 {c.total_sessions} 场 / {c.total_count} 条"
                f"（最近第 {c.session_seq} 场，{SessionLogManager._status_cn(c.status)}）"
            )
        return head + "\n" + "\n".join(lines)

    @staticmethod
    def format_entries(entries: list[LogEntry], title: str) -> str:
        """「/记录 看」：逐条展示日志原文（清洗后）。"""
        if not entries:
            return f"{title}：暂无日志。"
        lines = [f"{title}（{len(entries)} 条）："]
        for e in entries:
            name = _sanitize(e.sender_name)
            text = _sanitize(e.text)
            tag = "[结算]" if e.is_roll else ""
            if e.role == "bot":
                lines.append(f"- [{e.ts}] 机器人{tag}: {text}")
            else:
                lines.append(f"- [{e.ts}] {name}{tag}: {text}")
        return "\n".join(lines)

    @staticmethod
    def format_summary_list(rows: list[SummaryRow], campaign: str) -> str:
        """「/记录 摘要」：已生成的场次摘要列表。"""
        if not rows:
            return f"团「{campaign}」还没有生成过摘要，用 /总结 生成。"
        lines = [f"团「{campaign}」已生成摘要："]
        for r in rows:
            first_line = _sanitize(r.summary_text.splitlines()[0], 60) if r.summary_text else ""
            lines.append(f"- 第 {r.session_seq} 场（{r.created_at}）：{first_line}")
        return "\n".join(lines)

    def build_summary_input(
        self,
        campaign: str,
        session_seq: int,
        entries: list[LogEntry],
    ) -> tuple[str, bool]:
        """把日志条目组装成喂给 LLM 的输入文本。

        返回 (输入文本, 是否被截断)。截断时取「最近窗口」，保证尾段剧情完整。
        """
        lines: list[str] = []
        for e in entries:
            if e.role == "bot":
                prefix = "[结算]" if e.is_roll else "[机器人]"
                lines.append(f"{prefix} {e.text}")
            else:
                name = _sanitize(e.sender_name) or e.sender_id
                prefix = "[结算]" if e.is_roll else "[玩家]"
                lines.append(f"{prefix} {name}: {e.text}")
        truncated = False
        # 先按条数上限，再按字符上限（都取最近窗口，保证尾段剧情完整）
        if len(lines) > self._max_summary_entries:
            lines = lines[-self._max_summary_entries :]
            truncated = True
        full = "\n".join(lines)
        if len(full) > self._max_summary_chars:
            full = full[-self._max_summary_chars :]
            truncated = True
        return (
            f"团「{campaign}」第 {session_seq} 场跑团记录：\n{full}",
            truncated,
        )

    @staticmethod
    def summary_system_prompt() -> str:
        """摘要系统提示词：叙事式剧情回顾 + 末尾结算统计。"""
        return (
            "你是跑团战报整理员。把用户给出的跑团记录压缩成一份叙事式剧情回顾：\n"
            "1) 只保留角色真正做的事（玩家扮演与 DM/机器人的剧情旁白），"
            "结算类记录折成一句话结果（如「攻击命中，造成 12 点伤害」）；\n"
            "2) 完全剔除玩家场外吐槽（闲聊、玩梗、与剧情无关的话）；\n"
            "3) 按时间顺序写成 3~6 段连贯叙事，人物用其名字或角色名；\n"
            "4) 末尾另起一行「结算统计：」列出本场关键骰点结果，不超过 5 条；\n"
            "输出纯文本，不使用任何 Markdown 符号（不用 **、#、```），"
            "总长度控制在 300 字以内。"
        )

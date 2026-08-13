"""kb.py — DND 知识库查询层（只读 SQLite）。

知识库是「只读 + 万级条目 + 结构化过滤」的数据，不适合走 AstrBot KV：
- 无读-改-写，不需要 asyncio.Lock；
- 查询全部走 SQLite（sqlite3 标准库，无第三方依赖）；
- 数据库文件随插件打包（kb_data/dnd_kb.db），未来在线更新时优先使用
  data_dir 下的 kb_update.db（本模块已预留路径解析）。

查询策略（不用 FTS5，见 ADR-0002）：
  别名精确 → name LIKE → 逐字缩短 → 候选列表。

模块结构遵循项目惯例：Manager + format_* 静态方法；
dataclass 仅作内存结果模型（无需 to_dict/from_dict）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .kb_enums import edition_of_source, format_rarity
from .kb_tags import clean_5etools_tags
from .money import format_cp
from .homebrew import (
    HOMEBREW_FLAG,
    HomebrewEntry,
    HomebrewLoadResult,
    HomebrewManager,
    filter_overlay,
    search_overlay,
)

# 单条条目返回的最大字符数，超出截断并提示。
MAX_ENTRY_LEN = 4000


def _first_line(text: str) -> str:
    """概要回退：取正文首行（概要层某特性缺 summary 时用，v0.48.0）。"""
    text = (text or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    if len(line) > 80:
        line = line[:80] + "…"
    return line

# 知识库 schema 版本：与 scripts/build_kb.py 的 SCHEMA_VERSION 保持一致。
# resolve_db_path 用它在「旧版 kb_update.db」与「新版内置库」之间做回退选择。
KB_SCHEMA_VERSION = 7

# 零幻觉约束：LLM 工具返回末尾固定附加（命令直查不需要，因为不经 LLM）。
NO_HALLUCINATION_NOTE = (
    "（以上内容来自知识库原文，仅可依据其作答，未提及的信息不要编造。）"
)

_MACHINE_FLAG = "⚠️机翻"
# 公开别名：供命令层复用（机翻标记的展示样式保持一致）。
MACHINE_FLAG = _MACHINE_FLAG

# 跨库广搜结果的类别展示顺序与中文标签。
_KIND_ORDER = [
    "spell", "monster", "item", "feat", "background",
    "class", "subclass", "condition", "race", "optionalfeature",
]
_KIND_LABEL = {
    "spell": "法术",
    "monster": "怪物",
    "item": "物品",
    "feat": "专长",
    "background": "背景",
    "class": "职业",
    "subclass": "子职",
    "condition": "状态",
    "race": "种族",
    "optionalfeature": "选项",
}

# 种族速度类型（英文键）→ races 表数值列（filter 用）。
_SPEED_COL: dict[str, str] = {
    "walk": "speed_walk",
    "climb": "speed_climb",
    "swim": "speed_swim",
    "fly": "speed_fly",
    "burrow": "speed_burrow",
}


# ---------------------------------------------------------------------------
# 内存结果模型
# ---------------------------------------------------------------------------


@dataclass
class KbEntry:
    kind: str
    name: str
    eng_name: str
    source: str
    edition: str
    body: str
    is_machine: int = 0
    # 过滤侧表字段（非过滤查询时为 None）
    level: int | None = None
    school: str | None = None
    cr: float | None = None
    mtype: str | None = None
    size: str | None = None
    rarity: str | None = None
    attunement: int | None = None
    # 种族侧表数值字段（v0.16.0）
    speed_walk: int | None = None
    speed_climb: int | None = None
    speed_swim: int | None = None
    speed_fly: int | None = None
    speed_burrow: int | None = None
    darkvision: int | None = None
    # 专长一句话概要（v0.26.0，feats 侧表；非过滤查询时为 None）
    feat_summary: str | None = None
    # 专长类型 / 属性提升（v0.26.1，detail 查询带出 entry_tags 汇总；非 detail 为 None）
    feat_type_label: str | None = None
    feat_ability: str | None = None
    # 法术一句话概要（v0.27.0，spells 侧表；非过滤查询时为 None）
    spell_summary: str | None = None
    # 法术语义标签（v0.44.0，entry_tags spell_keyword 汇总「、」分隔；detail 带出）
    spell_keywords: str | None = None
    # 职业/子职一句话概要 + 职业定位（v0.33.0，classes 侧表；非过滤查询时为 None）
    class_summary: str | None = None
    class_role: str | None = None
    # 种族/背景一句话概要（v0.34.0，races.summary / backgrounds 侧表；
    # 非过滤查询时为 None）
    race_summary: str | None = None
    background_summary: str | None = None
    # 条目 id（v0.35.0，仅 filter() 带出；供构筑咨询前置标注按 id 取 facet）
    entry_id: int | None = None
    # 可定制职业选项类型标签（v0.50.0，entry_tags feature_type 汇总；detail 带出）
    opt_type_label: str | None = None
    # 是否为运行期私设（v0.36.0；覆盖官方或新增，展示加「房规」标注）
    is_homebrew: bool = False

    @property
    def edition_label(self) -> str:
        return f"{self.source}·{self.edition}"

    @property
    def machine_label(self) -> str:
        return _MACHINE_FLAG if self.is_machine else ""

    @property
    def homebrew_label(self) -> str:
        return HOMEBREW_FLAG if self.is_homebrew else ""


@dataclass
class SearchHit:
    kind: str
    name: str
    eng_name: str
    source: str
    edition: str
    summary: str
    is_machine: int = 0
    # v0.36.0：是否为运行期私设（展示加「房规」标注）
    is_homebrew: bool = False

    @property
    def edition_label(self) -> str:
        return f"{self.source}·{self.edition}"

    @property
    def homebrew_label(self) -> str:
        return HOMEBREW_FLAG if self.is_homebrew else ""


@dataclass
class ClassFeatureRow:
    class_name: str
    subclass_name: str
    source: str
    level: int | None
    name: str
    summary: str
    body: str

    @property
    def edition(self) -> str:
        """从 source 推断规则版本（v0.48.0，分层展示按版本过滤用）。"""
        return edition_of_source(self.source)


# 职业特性层级分段（v0.48.0，ADR-0023）：第1层 1-4级 / 第2层 5-10级 /
# 第3层 11-16级 / 第4层 17-20级。L1 概要总表、L2 层级钻取、L3 全文分条共用。
CLASS_TIERS: list[tuple[int, int, str]] = [
    (1, 4, "第1层 1-4级"),
    (5, 10, "第2层 5-10级"),
    (11, 16, "第3层 11-16级"),
    (17, 20, "第4层 17-20级"),
]


def tier_of(level: int | None) -> int | None:
    """level → 层级 1..4；None 或越界返回 None。"""
    if level is None:
        return None
    for idx, (lo, hi, _label) in enumerate(CLASS_TIERS, start=1):
        if lo <= level <= hi:
            return idx
    return None


@dataclass
class ClassFeatureResult:
    class_name: str
    eng_name: str
    editions: list[str] = field(default_factory=list)
    base_rows: list[ClassFeatureRow] = field(default_factory=list)
    subclass_rows: list[ClassFeatureRow] = field(default_factory=list)
    subclass_candidates: list[str] = field(default_factory=list)
    # v0.29.0 本职特性细化：""=不细化（名字总表）；"*"=输出全部本职特性全文；
    # 其他值=只输出名称匹配（跨版本）的特性全文。
    feature_query: str = ""
    # v0.33.0 职业/子职富化：AI 一句话概要 + 职业定位（classes 侧表带出）。
    class_summary: str = ""
    class_role: str = ""
    # v0.48.1 子职版本回退：默认版本过滤后子职为空 → 回退展示其他版本，
    # 值=回退到的版本（"2014"/"2024"），空=未回退。解决「候选可见但查不到」
    # （如魔射手仅 XGE 2014，2024 群默认版本下查询为空）。
    subclass_edition_fallback: str = ""


@dataclass
class ClassTierSegment:
    """职业特性按 (版本, 层级) 分组后的一个展示段（v0.48.0，ADR-0023）。"""

    tier: int
    label: str          # "第1层 1-4级"
    edition: str        # "2014" / "2024" / "other"
    rows: list[ClassFeatureRow]


@dataclass
class ClassDisplay:
    """职业特性分层展示的结构化结果（v0.48.0）。

    overview：L1 概要层（每行「N级 名称：一句话概要」）分段；
    full_segments：L3 全文层（每条特性完整正文）分段，按层级一段；
    subclass_part：子职全文单块（v0.11.0 一次给齐）；
    prompts：末尾提示行。
    """

    head: str
    overview: list[ClassTierSegment] = field(default_factory=list)
    full_segments: list[ClassTierSegment] = field(default_factory=list)
    subclass_part: str = ""
    prompts: list[str] = field(default_factory=list)
    has_data: bool = False


@dataclass
class FilterResult:
    """filter() 的返回值：命中条目 + 未限量总数（供「共 N 条」提示）。"""

    entries: list[KbEntry] = field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# v0.18.0 规则引擎侧表结果模型（schema v4）
# ---------------------------------------------------------------------------


@dataclass
class ClassCombatRow:
    """职业战斗数据（class_combat 表）。caster ∈ full/1/2/1/3/pact/artificer/''。"""

    class_name: str
    source: str
    edition: str
    hd_faces: int | None
    saves: list[str]
    caster: str
    spell_ability: str

    @property
    def is_caster(self) -> bool:
        return self.caster in ("full", "1/2", "1/3", "pact", "artificer")


@dataclass
class ChooseSpec:
    """一条 choose 加值规格：count 型（选 count 个各 +1）或 weighted 型（权重分配）。"""

    kind: str  # "count" | "weighted"
    from_set: list[str]
    count: int = 1
    weights: list[int] = field(default_factory=list)


@dataclass
class AbilityOffer:
    """种族/背景属性加值：flat 固定加值 + chooses 可选加值（任意组合）。"""

    flat: dict[str, int] = field(default_factory=dict)
    chooses: list[ChooseSpec] = field(default_factory=list)

    @property
    def has_choice(self) -> bool:
        return bool(self.chooses)

    @property
    def total_flat(self) -> int:
        return sum(self.flat.values())


@dataclass
class ItemCombatRow:
    """护甲/武器战斗字段（item_combat 表）。armor_type ∈ HA/MA/LA/S/M/R/''。"""

    name: str
    source: str
    edition: str
    ac: int | None
    armor_type: str
    strength: int | None
    stealth: bool
    dmg1: str
    properties: list[str]
    range_note: str

    @property
    def is_shield(self) -> bool:
        return self.armor_type == "S"

    @property
    def is_ranged(self) -> bool:
        return self.armor_type == "R"

    @property
    def is_finesse(self) -> bool:
        return "F" in self.properties

    @property
    def is_two_handed(self) -> bool:
        return "2H" in self.properties

    @property
    def is_thrown(self) -> bool:
        return "T" in self.properties


# ---------------------------------------------------------------------------
# 路径解析
# ---------------------------------------------------------------------------


def _read_schema_version(db_path: Path) -> int:
    """读取库的 schema_version；meta 缺失视为 v1（旧格式），不可读返回 0。"""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row[0]) if row else 1
        finally:
            conn.close()
    except (sqlite3.Error, ValueError, OSError):
        return 0


def resolve_db_path(
    builtin_db: Path,
    update_db: Path | None = None,
    prefer_update: bool = True,
    min_schema: int = KB_SCHEMA_VERSION,
) -> Path:
    """解析生效的知识库路径。

    - prefer_update 且 update_db 存在、schema 版本达标 → update_db（在线更新产物）；
    - 更新库缺失或 schema 过旧（会屏蔽新内置库）→ 回退到内置库；
    - 内置库也不达标（理论上随包不会发生）→ 用更新库兜底。
    """
    if prefer_update and update_db is not None and update_db.is_file():
        if _read_schema_version(update_db) >= min_schema:
            return update_db
        if (
            builtin_db.is_file()
            and _read_schema_version(builtin_db) >= min_schema
        ):
            return builtin_db
        return update_db
    return builtin_db


_ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")


def _parse_ability_payload(payload: str | None) -> AbilityOffer | None:
    """解析 build 期固化的 ability JSON → AbilityOffer。

    兼容已验证结构：
    - 2014 种族平铺/合并：[{"cha": 2, "choose": {"from": [...], "count": 2}}]
      —— TCE 定制血统用 amount 而非 count；
    - 2024 背景 weighted：[{"choose": {"weighted": {"from": [...], "weights": [2,1]}}}]
      —— 多个方案并存（玩家二选一），全部作为 chooses 返回。
    """
    if not payload:
        return None
    try:
        arr = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(arr, list):
        return None
    offer = AbilityOffer()
    for item in arr:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key == "choose":
                if not isinstance(value, dict):
                    continue
                if "weighted" in value:
                    w = value["weighted"]
                    fs = w.get("from")
                    if not isinstance(fs, list):
                        continue
                    offer.chooses.append(
                        ChooseSpec(
                            kind="weighted",
                            from_set=[str(x) for x in fs],
                            weights=[int(x) for x in (w.get("weights") or []) if isinstance(x, (int, float))],
                        )
                    )
                else:
                    fs = value.get("from")
                    if not isinstance(fs, list):
                        continue
                    cnt = value.get("count", value.get("amount", 1))
                    offer.chooses.append(
                        ChooseSpec(
                            kind="count",
                            from_set=[str(x) for x in fs],
                            count=int(cnt) if isinstance(cnt, (int, float)) else 1,
                        )
                    )
            elif key in _ABILITY_KEYS and isinstance(value, (int, float)):
                offer.flat[key] = offer.flat.get(key, 0) + int(value)
    return offer


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class KnowledgeBaseManager:
    def __init__(self, db_path: str | Path,
                 homebrew_dir: str | Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._ok: bool | None = None
        # v0.36.0 运行期私设 overlay：目录（data_dir/trpg_homebrew）为空即关闭。
        self._homebrew_dir = Path(homebrew_dir) if homebrew_dir else None
        self._homebrew_mgr: HomebrewManager | None = None

    # -- 运行期私设（homebrew）overlay --

    @property
    def homebrew_dir(self) -> Path | None:
        """私设目录（未配置为 None）。"""
        return self._homebrew_dir

    def official_key_set(self) -> set[tuple[str, str, str]]:
        """官方条目 (kind,name,source) 键集（私设撞键检测用）。"""
        try:
            return {tuple(r) for r in self._connect().execute(
                "SELECT kind, name, source FROM entries")}
        except sqlite3.Error:
            return set()

    def _homebrew(self) -> HomebrewManager | None:
        """懒加载私设管理器；目录未配置/加载异常时返回 None（空 overlay）。"""
        if self._homebrew_dir is None:
            return None
        if self._homebrew_mgr is None:
            mgr = HomebrewManager(self._homebrew_dir)
            try:
                mgr.load()
            except Exception:  # noqa: BLE001 — 私设异常不阻塞知识库
                return None
            self._homebrew_mgr = mgr
        return self._homebrew_mgr

    def reload_homebrew(self) -> HomebrewLoadResult:
        """重新加载私设目录；返回加载结果（/kb reload 与 /kb 私设 展示用）。"""
        hb = self._homebrew()
        if hb is None:
            return HomebrewLoadResult()
        return hb.load(self.official_key_set())

    def homebrew_stats(self) -> HomebrewLoadResult:
        """当前 overlay 统计（不重扫，仅供 /kb 私设 展示）。"""
        hb = self._homebrew()
        if hb is None:
            return HomebrewLoadResult()
        return HomebrewLoadResult(
            files=len(set(e.file for e in hb.entries())),
            entries=len(hb.entries()),
            overrides=sum(1 for e in hb.entries() if e.is_override),
        )

    def _hb_to_kb_entry(self, e: HomebrewEntry) -> KbEntry:
        """私设条目 → KbEntry（is_homebrew=True，侧表字段照搬）。"""
        kb = KbEntry(
            kind=e.kind, name=e.name, eng_name=e.eng_name, source=e.source,
            edition=e.edition, body=e.body, is_machine=e.is_machine,
            is_homebrew=True,
        )
        side = e.side
        if e.kind == "spell":
            kb.level = side.get("level")
            kb.school = side.get("school")
            if side.get("summary"):
                kb.spell_summary = side["summary"]
            kw = [v for f, v in e.tags if f == "spell_keyword"]
            if kw:
                kb.spell_keywords = "、".join(sorted(kw))
        elif e.kind == "monster":
            kb.cr = side.get("cr")
            kb.mtype = side.get("mtype")
            kb.size = side.get("size")
        elif e.kind == "item":
            kb.rarity = side.get("rarity")
            kb.attunement = side.get("attunement")
        elif e.kind == "race":
            kb.speed_walk = side.get("speed_walk")
            kb.speed_climb = side.get("speed_climb")
            kb.speed_swim = side.get("speed_swim")
            kb.speed_fly = side.get("speed_fly")
            kb.speed_burrow = side.get("speed_burrow")
            kb.darkvision = side.get("darkvision")
        return kb

    def _hb_hits(self, query: str, kind: str | None = None,
                 limit: int = 8) -> list[SearchHit]:
        """私设 overlay 搜索 → SearchHit 列表（私设优先置顶）。"""
        hb = self._homebrew()
        if hb is None or not hb.loaded:
            return []
        hits: list[SearchHit] = []
        for e in search_overlay(hb.entries(), query, kind, limit):
            summary = e.side.get("summary") or self._summary_of(e.body)
            hits.append(SearchHit(
                kind=e.kind, name=e.name, eng_name=e.eng_name,
                source=e.source, edition=e.edition, summary=summary,
                is_machine=e.is_machine, is_homebrew=True,
            ))
        return hits

    # -- 连接管理 --

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    @property
    def available(self) -> bool:
        """知识库是否可用（数据库存在且 meta 表可读）。"""
        if self._ok is not None:
            return self._ok
        try:
            conn = self._connect()
            conn.execute("SELECT 1 FROM meta LIMIT 1").fetchone()
            self._ok = True
        except sqlite3.Error:
            self._ok = False
        return self._ok

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
        self._ok = None

    def swap_db(self, db_path: str | Path) -> None:
        """换用新数据库文件（在线更新/回滚后调用）。"""
        self.close()
        self._db_path = Path(db_path)

    # -- 查询 --

    def version(self) -> dict[str, str]:
        try:
            rows = self._connect().execute(
                "SELECT key, value FROM meta"
            ).fetchall()
            return {r["key"]: r["value"] for r in rows}
        except sqlite3.Error:
            return {}

    def _fetch_entries(self, rows: list[sqlite3.Row]) -> list[KbEntry]:
        out: list[KbEntry] = []
        for r in rows:
            e = KbEntry(
                kind=r["kind"],
                name=r["name"],
                eng_name=r["eng_name"],
                source=r["source"],
                edition=r["edition"],
                body=r["body"],
                is_machine=r["is_machine"],
            )
            # detail 查询统一带 LEFT JOIN feats，专长行可带出 AI 概要
            if "feat_summary" in r.keys() and r["feat_summary"]:
                e.feat_summary = r["feat_summary"]
            if "feat_ability" in r.keys() and r["feat_ability"]:
                e.feat_ability = r["feat_ability"]
            if "feat_type_label" in r.keys() and r["feat_type_label"]:
                e.feat_type_label = r["feat_type_label"]
            # v0.27.0：法术一句话概要（spells 侧表）
            if "spell_summary" in r.keys() and r["spell_summary"]:
                e.spell_summary = r["spell_summary"]
            # v0.44.0：法术语义标签（entry_tags spell_keyword 汇总）
            if "spell_keywords" in r.keys() and r["spell_keywords"]:
                e.spell_keywords = r["spell_keywords"]
            # v0.33.0：职业/子职一句话概要 + 职业定位（classes 侧表）
            if "class_summary" in r.keys() and r["class_summary"]:
                e.class_summary = r["class_summary"]
            if "class_role" in r.keys() and r["class_role"]:
                e.class_role = r["class_role"]
            # v0.34.0：种族/背景一句话概要（races.summary / backgrounds 侧表）
            if "race_summary" in r.keys() and r["race_summary"]:
                e.race_summary = r["race_summary"]
            if "background_summary" in r.keys() and r["background_summary"]:
                e.background_summary = r["background_summary"]
            # v0.50.0：可定制职业选项类型标签（entry_tags feature_type 汇总）
            if "opt_type_label" in r.keys() and r["opt_type_label"]:
                e.opt_type_label = r["opt_type_label"]
            out.append(e)
        return out

    @staticmethod
    def _summary_of(body: str, limit: int = 60) -> str:
        """取正文中第一个非「【…】」标题行/法术元信息行的开头作为摘要。"""
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("【"):
                continue
            # v0.44.0：法术卡片体首行为环位行（三环 塑能…），跳过元信息行
            if KnowledgeBaseManager._is_spell_meta_line(stripped):
                continue
            text = stripped[:limit]
            return text + ("…" if len(stripped) > limit else "")
        return body[:limit]

    @staticmethod
    def _is_spell_meta_line(line: str) -> bool:
        """法术卡片元信息行判定（环位行/施法时间/施法距离/法术成分/持续时间/版本行）。"""
        return (
            line.startswith(("施法时间：", "施法距离：", "法术成分：", "持续时间：", "版本："))
            or re.match(r"^[一二三四五六七八九]?环\s|^[\u4e00-\u9fa5]{1,2}戏法（", line) is not None
            or re.match(r"^[\u4e00-\u9fa5]{1,2}戏法$", line) is not None
        )

    def search(
        self,
        query: str,
        kind: str | None = None,
        limit: int = 8,
        fulltext: bool = False,
    ) -> list[SearchHit]:
        """模糊搜索：别名精确 → name LIKE（可含 body）→ 逐字缩短。

        fulltext=True 时第二档追加 body LIKE（/查询 -全文 用，仍限量）。
        """
        q = (query or "").strip()
        if not q:
            return []
        q_lower = q.lower()
        conn = self._connect()
        # 别名查询带 JOIN（用 e.kind）；直查 entries 无别名（用 kind）。
        join_kind = " AND e.kind = ?" if kind else ""
        plain_kind = " AND kind = ?" if kind else ""
        params_join: tuple[Any, ...] = (q_lower,) + ((kind,) if kind else ())
        params_plain: tuple[Any, ...] = ((kind,) if kind else ())

        # 第一档：别名精确命中
        rows = conn.execute(
            "SELECT e.*, f.summary AS feat_summary, sp.summary AS spell_summary,"
            " cl.summary AS class_summary, cl.role AS class_role,"
                " r.summary AS race_summary, bg.summary AS background_summary"
            " FROM aliases a JOIN entries e ON e.id = a.entry_id"
            " LEFT JOIN feats f ON f.entry_id = e.id"
            " LEFT JOIN spells sp ON sp.entry_id = e.id"
            " LEFT JOIN classes cl ON cl.entry_id = e.id"
                " LEFT JOIN races r ON r.entry_id = e.id"
                " LEFT JOIN backgrounds bg ON bg.entry_id = e.id"
            f" WHERE a.alias = ?{join_kind}"
            " ORDER BY e.edition DESC, e.source LIMIT ?",
            params_join + (limit,),
        ).fetchall()
        # 私设 overlay 同档命中置顶（v0.36.0：房规优先展示）
        hb_hits = self._hb_hits(q, kind, limit)
        if rows or hb_hits:
            return hb_hits + self._hits_from(rows)

        # 第二档：名称包含（中文按子串匹配天然有效）；fulltext 时追加正文匹配
        like = f"%{q}%"
        body_or = " OR e.body LIKE ?" if fulltext else ""
        rows = conn.execute(
            "SELECT e.*, f.summary AS feat_summary, sp.summary AS spell_summary,"
            " cl.summary AS class_summary, cl.role AS class_role,"
                " r.summary AS race_summary, bg.summary AS background_summary"
            " FROM entries e LEFT JOIN feats f ON f.entry_id = e.id"
            " LEFT JOIN spells sp ON sp.entry_id = e.id"
            " LEFT JOIN classes cl ON cl.entry_id = e.id"
                " LEFT JOIN races r ON r.entry_id = e.id"
                " LEFT JOIN backgrounds bg ON bg.entry_id = e.id"
            " WHERE (e.name LIKE ? OR e.eng_name LIKE ?)"
            f"{body_or}{plain_kind}"
            " ORDER BY e.kind, e.name, e.edition DESC, e.source LIMIT ?",
            (like, like) + ((like,) if fulltext else ()) + params_plain + (limit,),
        ).fetchall()
        hb_hits = self._hb_hits(q, kind, limit)
        if rows or hb_hits:
            return hb_hits + self._hits_from(rows)

        # 第三档：逐字缩短（错别字/半截名容错，如「火球木」→「火球」）
        short = q
        while len(short) > 1:
            short = short[:-1]
            like = f"%{short}%"
            body_or = " OR e.body LIKE ?" if fulltext else ""
            rows = conn.execute(
                "SELECT e.*, f.summary AS feat_summary, sp.summary AS spell_summary,"
                " cl.summary AS class_summary, cl.role AS class_role,"
                " r.summary AS race_summary, bg.summary AS background_summary"
                " FROM entries e LEFT JOIN feats f ON f.entry_id = e.id"
                " LEFT JOIN spells sp ON sp.entry_id = e.id"
                " LEFT JOIN classes cl ON cl.entry_id = e.id"
                " LEFT JOIN races r ON r.entry_id = e.id"
                " LEFT JOIN backgrounds bg ON bg.entry_id = e.id"
                " WHERE (e.name LIKE ? OR e.eng_name LIKE ?)"
                f"{body_or}{plain_kind}"
                " ORDER BY e.kind, e.name, e.edition DESC, e.source LIMIT ?",
                (like, like) + ((like,) if fulltext else ()) + params_plain + (limit,),
            ).fetchall()
            hb_hits = self._hb_hits(q, kind, limit)
            if rows or hb_hits:
                return hb_hits + self._hits_from(rows)
        return []

    def _hits_from(self, rows: list[sqlite3.Row]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for r in rows:
            summary = r["body"]
            # 专长/法术/职业子职优先用 AI 一句话概要（v0.26.0 / v0.27.0 / v0.33.0），
            # 否则截正文首行
            if r["kind"] == "feat" and r["feat_summary"]:
                summary = r["feat_summary"]
            elif r["kind"] == "spell" and r["spell_summary"]:
                summary = r["spell_summary"]
            elif r["kind"] in ("class", "subclass") and r["class_summary"]:
                summary = r["class_summary"]
            elif r["kind"] == "race" and r["race_summary"]:
                summary = r["race_summary"]
            elif r["kind"] == "background" and r["background_summary"]:
                summary = r["background_summary"]
            hits.append(
                SearchHit(
                    kind=r["kind"],
                    name=r["name"],
                    eng_name=r["eng_name"],
                    source=r["source"],
                    edition=r["edition"],
                    summary=summary,
                    is_machine=r["is_machine"],
                )
            )
        return hits

    def detail(self, name: str, kind: str | None = None) -> list[KbEntry]:
        """按名称返回全部版本（同名多版本 = 多行，2024 在前）。"""
        n = (name or "").strip()
        if not n:
            return []
        conn = self._connect()
        if kind:
            rows = conn.execute(
                "SELECT e.*, f.summary AS feat_summary,"
                " sp.summary AS spell_summary,"
                " cl.summary AS class_summary, cl.role AS class_role,"
                " r.summary AS race_summary, bg.summary AS background_summary,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'ability_increase'"
                "   ORDER BY value) t) AS feat_ability,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'feat_type') t)"
                " AS feat_type_label,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'spell_keyword'"
                "   ORDER BY value) t) AS spell_keywords,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'feature_type'"
                "   ORDER BY value) t) AS opt_type_label"
                " FROM entries e LEFT JOIN feats f ON f.entry_id = e.id"
                " LEFT JOIN spells sp ON sp.entry_id = e.id"
                " LEFT JOIN classes cl ON cl.entry_id = e.id"
                " LEFT JOIN races r ON r.entry_id = e.id"
                " LEFT JOIN backgrounds bg ON bg.entry_id = e.id"
                " WHERE e.name = ? AND e.kind = ?"
                " ORDER BY e.edition DESC, e.source",
                (n, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT e.*, f.summary AS feat_summary,"
                " sp.summary AS spell_summary,"
                " cl.summary AS class_summary, cl.role AS class_role,"
                " r.summary AS race_summary, bg.summary AS background_summary,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'ability_increase'"
                "   ORDER BY value) t) AS feat_ability,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'feat_type') t)"
                " AS feat_type_label,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'spell_keyword'"
                "   ORDER BY value) t) AS spell_keywords,"
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'feature_type'"
                "   ORDER BY value) t) AS opt_type_label"
                " FROM entries e LEFT JOIN feats f ON f.entry_id = e.id"
                " LEFT JOIN spells sp ON sp.entry_id = e.id"
                " LEFT JOIN classes cl ON cl.entry_id = e.id"
                " LEFT JOIN races r ON r.entry_id = e.id"
                " LEFT JOIN backgrounds bg ON bg.entry_id = e.id"
                " WHERE e.name = ?"
                " ORDER BY e.kind, e.edition DESC, e.source",
                (n,),
            ).fetchall()
        entries = self._fetch_entries(rows)
        # v0.36.0：同名私设置顶（房规覆盖/新增均优先展示）
        hb = self._homebrew()
        if hb is not None and hb.loaded:
            hb_matches = [
                e for e in hb.entries()
                if e.name == n and (kind is None or e.kind == kind)
            ]
            if hb_matches:
                entries = [self._hb_to_kb_entry(e) for e in hb_matches] + entries
        return entries

    def filter(
        self,
        kind: str,
        level: int | None = None,
        school: str | None = None,
        cr_min: float | None = None,
        cr_max: float | None = None,
        mtype: str | None = None,
        rarity: str | None = None,
        limit: int = 20,
        *,
        tags: list[tuple[str, str]] | None = None,
        concentration: bool | None = None,
        range_type: str | None = None,
        range_max: int | None = None,
        range_min: int | None = None,
        attunement: bool | None = None,
        speed_type: str | None = None,
        speed_min: int | None = None,
        speed_max: int | None = None,
        darkvision_min: int | None = None,
        spell_class: str | None = None,
        level_max: int | None = None,
    ) -> FilterResult:
        """结构化过滤查询（法术环级/学派/成分/专注/距离/形状、怪物 CR/类型/
        特性标签、物品稀有度/特性标签、种族速度/黑暗视觉/特性标签）。

        - tags：[(facet, value), ...] 特性标签，AND 语义（EXISTS 子查询拼接）；
        - concentration/range_type/range_max/range_min 仅法术有效；
        - attunement 仅物品有效；
        - speed_type/speed_min/speed_max/darkvision_min 仅种族有效
          （speed_type 为 walk/climb/swim/fly/burrow，缺省 walk）；
        - spell_class（v0.35.0）：仅法术有效，职业法术表反查（spell_classes 侧表）；
        - level_max（v0.35.0）：仅法术有效，环阶 ≤ 该值；
        - 返回 FilterResult（命中条目 + 未限量总数）。
        """
        if kind not in ("spell", "monster", "item", "race", "feat", "class",
                        "subclass", "background", "optionalfeature"):
            return FilterResult()
        conn = self._connect()
        where: list[str] = ["e.kind = ?"]
        params: list[Any] = [kind]

        if kind == "optionalfeature":
            # v0.50.0：可定制职业选项，无侧表；feature_type/prerequisite
            # 标签反查（tags 参数）。select_extra 带类型标签供列表展示。
            join = ""
            select_extra = (
                " (SELECT group_concat(t.value, '、') FROM"
                "  (SELECT value FROM entry_tags"
                "   WHERE entry_id = e.id AND facet = 'feature_type'"
                "   ORDER BY value) t) AS opt_type_label"
            )
        elif kind in ("class", "subclass"):
            # v0.33.0 职业/子职：classes 侧表（概要/定位）+ class_keyword /
            # subclass_keyword / class_role 标签（tags 参数反查）。
            join = " LEFT JOIN classes cl ON cl.entry_id = e.id"
            select_extra = "cl.summary AS class_summary, cl.role AS class_role"
        elif kind == "feat":
            join = " LEFT JOIN feats f ON f.entry_id = e.id"
            select_extra = "f.summary AS feat_summary"
        elif kind == "spell":
            join = " JOIN spells s ON s.entry_id = e.id"
            select_extra = (
                "s.level AS level, s.school AS school,"
                " s.ritual AS ritual, s.concentration AS concentration,"
                " s.components AS components, s.range_feet AS range_feet,"
                " s.range_type AS range_type, s.summary AS spell_summary"
            )
            if level is not None:
                where.append("s.level = ?")
                params.append(level)
            if school:
                where.append("s.school = ?")
                params.append(school)
            if concentration is not None:
                where.append("s.concentration = ?")
                params.append(1 if concentration else 0)
            if range_type:
                where.append("s.range_type = ?")
                params.append(range_type)
            if range_max is not None:
                where.append("s.range_feet IS NOT NULL AND s.range_feet <= ?")
                params.append(range_max)
            if range_min is not None:
                where.append("s.range_feet IS NOT NULL AND s.range_feet >= ?")
                params.append(range_min)
            if level_max is not None:
                where.append("s.level <= ?")
                params.append(level_max)
            if spell_class:
                where.append(
                    "EXISTS (SELECT 1 FROM spell_classes sc"
                    " WHERE sc.entry_id = e.id AND sc.class_name = ?)"
                )
                params.append(spell_class)
        elif kind == "monster":
            join = " JOIN monsters m ON m.entry_id = e.id"
            select_extra = "m.cr AS cr, m.mtype AS mtype, m.size AS size"
            if cr_min is not None:
                where.append("m.cr >= ?")
                params.append(cr_min)
            if cr_max is not None:
                where.append("m.cr <= ?")
                params.append(cr_max)
            if mtype:
                where.append("m.mtype = ?")
                params.append(mtype)
        elif kind == "item":
            join = " JOIN items i ON i.entry_id = e.id"
            select_extra = "i.rarity AS rarity, i.attunement AS attunement"
            if rarity == "magic":
                # 查询哨兵（RARITY_CN「魔法物品」）：整体反查 = 排除非魔法基础物品
                where.append("i.rarity IS NOT NULL AND i.rarity != 'none'")
            elif rarity:
                where.append("i.rarity = ?")
                params.append(rarity)
            if attunement is not None:
                where.append("i.attunement = ?")
                params.append(1 if attunement else 0)
        elif kind == "background":
            # v0.34.0：backgrounds 侧表（AI 一句话概要）带出
            join = " LEFT JOIN backgrounds bg ON bg.entry_id = e.id"
            select_extra = "bg.summary AS background_summary"

        if kind == "race":
            # v0.34.0：races.summary（AI 一句话概要）带出
            join = " JOIN races r ON r.entry_id = e.id"
            select_extra = (
                "r.speed_walk AS speed_walk, r.speed_climb AS speed_climb,"
                " r.speed_swim AS speed_swim, r.speed_fly AS speed_fly,"
                " r.speed_burrow AS speed_burrow, r.darkvision AS darkvision,"
                " r.summary AS race_summary"
            )
            speed_col = _SPEED_COL.get(speed_type or "walk", "speed_walk")
            if speed_min is not None or speed_max is not None:
                if speed_min is not None:
                    where.append(f"r.{speed_col} IS NOT NULL AND r.{speed_col} >= ?")
                    params.append(speed_min)
                if speed_max is not None:
                    where.append(f"r.{speed_col} IS NOT NULL AND r.{speed_col} <= ?")
                    params.append(speed_max)
            elif speed_type:
                # 仅指定类型（如「会飞的种族」）：该速度列非空即命中
                where.append(f"r.{speed_col} IS NOT NULL")
            if darkvision_min is not None:
                where.append("r.darkvision IS NOT NULL AND r.darkvision >= ?")
                params.append(darkvision_min)

        # 特性标签：每个 (facet, value) 一条 EXISTS，取交集
        # v0.50.0：value 含 % 时走 LIKE（先决条件反查如「第5级」→ "%第5级%"）
        for facet, value in tags or []:
            if not facet or not value:
                continue
            if "%" in value:
                where.append(
                    "EXISTS (SELECT 1 FROM entry_tags t"
                    " WHERE t.entry_id = e.id AND t.facet = ? AND t.value LIKE ?)"
                )
            else:
                where.append(
                    "EXISTS (SELECT 1 FROM entry_tags t"
                    " WHERE t.entry_id = e.id AND t.facet = ? AND t.value = ?)"
                )
            params.extend((facet, value))

        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        order = self._filter_order(kind)
        extra_cols = (", " + select_extra) if select_extra else ""
        sql = (
            "SELECT e.*, e.id AS entry_id" + extra_cols + " FROM entries e" + join
            + where_clause + order + " LIMIT ?"
        )
        count_sql = (
            "SELECT COUNT(*) FROM entries e" + join + where_clause
        )
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(sql, params + [limit]).fetchall()
        entries = self._fetch_filtered(rows, kind)
        # v0.36.0：私设 overlay 同条件命中追加（置顶；字段缺失即不命中）
        hb = self._homebrew()
        if hb is not None and hb.loaded:
            hb_rows = filter_overlay(
                hb.entries(), kind,
                level=level, school=school, cr_min=cr_min, cr_max=cr_max,
                mtype=mtype, rarity=rarity, tags=tags,
                concentration=concentration, range_type=range_type,
                range_min=range_min, range_max=range_max,
                attunement=attunement, speed_type=speed_type,
                speed_min=speed_min, speed_max=speed_max,
                darkvision_min=darkvision_min,
            )
            if hb_rows:
                entries = [self._hb_to_kb_entry(e) for e in hb_rows] + entries
                total = int(total) + len(hb_rows)
        return FilterResult(entries=entries, total=int(total))

    @staticmethod
    def _filter_order(kind: str) -> str:
        """筛选排序：怪物按 CR、法术按环级、物品按稀有度、种族/专长/职业按名称。"""
        if kind == "monster":
            return " ORDER BY m.cr, e.name, e.edition DESC, e.source"
        if kind == "spell":
            return " ORDER BY s.level, e.name, e.edition DESC, e.source"
        if kind in ("race", "feat", "class", "subclass", "background",
                    "optionalfeature"):
            return " ORDER BY e.name, e.edition DESC, e.source"
        return (
            " ORDER BY CASE i.rarity"
            " WHEN 'common' THEN 0 WHEN 'uncommon' THEN 1 WHEN 'rare' THEN 2"
            " WHEN 'very rare' THEN 3 WHEN 'legendary' THEN 4"
            " WHEN 'artifact' THEN 5 WHEN 'varies' THEN 6 ELSE 7 END,"
            " e.name, e.edition DESC, e.source"
        )

    def _fetch_filtered(
        self, rows: list[sqlite3.Row], kind: str
    ) -> list[KbEntry]:
        """filter() 行 → KbEntry（附带侧表字段，供列表展示 CR/环级/稀有度）。"""
        out: list[KbEntry] = []
        for r in rows:
            e = KbEntry(
                kind=r["kind"],
                name=r["name"],
                eng_name=r["eng_name"],
                source=r["source"],
                edition=r["edition"],
                body=r["body"],
                is_machine=r["is_machine"],
                entry_id=r["entry_id"],
            )
            if kind == "spell":
                e.level = r["level"]
                e.school = r["school"]
                e.spell_summary = r["spell_summary"] or None
            elif kind == "monster":
                e.cr = r["cr"]
                e.mtype = r["mtype"]
                e.size = r["size"]
            elif kind == "item":
                e.rarity = r["rarity"]
                e.attunement = r["attunement"]
            elif kind == "race":
                e.speed_walk = r["speed_walk"]
                e.speed_climb = r["speed_climb"]
                e.speed_swim = r["speed_swim"]
                e.speed_fly = r["speed_fly"]
                e.speed_burrow = r["speed_burrow"]
                e.darkvision = r["darkvision"]
                e.race_summary = r["race_summary"] or None
            elif kind == "background":
                e.background_summary = r["background_summary"] or None
            elif kind == "feat":
                e.feat_summary = r["feat_summary"] or None
            elif kind in ("class", "subclass"):
                e.class_summary = r["class_summary"] or None
                e.class_role = r["class_role"] or None
            elif kind == "optionalfeature":
                e.opt_type_label = r["opt_type_label"] or None
            # 其他：KbEntry 保持默认
            out.append(e)
        return out

    def resolve_feat_free_term(self, term: str) -> str | None:
        """专长裸词自由文本 → 命中的 facet（feat_keyword → prereq_race →
        prereq_feature → prereq_feat）。

        用于 /筛专长 裸词消歧。顺序原则：
        - 语义标签（feat_keyword，开放集合）值集命中时**优先**——玩家裸词
          「施法/机动/防御」的意图几乎总是语义标签而非前置条件，且标签词
          （远程/机动/施法…）与封闭的名字集合（种族/专长/特性名）不冲突；
        - 标签未命中时按精确名维度回退：种族 → 特性 → 前置专长名。
        不属于任何维度返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        # 1) 语义标签值集优先（「施法」→ feat_keyword，而非前置特性「施法」）
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'feat_keyword' LIMIT 1",
            (t,),
        ).fetchone()
        if row:
            return "feat_keyword"
        # 2) 精确名维度回退：种族 → 特性 → 前置专长
        row = conn.execute(
            "SELECT facet FROM entry_tags"
            " WHERE value = ? AND facet IN"
            " ('prereq_race','prereq_feature','prereq_feat')"
            " ORDER BY CASE facet WHEN 'prereq_race' THEN 0"
            " WHEN 'prereq_feature' THEN 1 ELSE 2 END LIMIT 1",
            (t,),
        ).fetchone()
        return row["facet"] if row else None

    def resolve_spell_free_term(self, term: str) -> str | None:
        """法术裸词自由文本 → 命中的 facet（spell_keyword → 其他法术标签维度）。

        用于 /筛法术 裸词消歧。顺序原则：
        - 语义标签（spell_keyword，开放集合）值集命中时**优先**——玩家裸词
          「控场/治疗/增益/召唤」的意图几乎总是语义大类而非其他维度，且标签词
          与封闭的枚举集合（伤害/状态/形状/目标/成分）不冲突；
        - 标签未命中时按现有枚举维度回退（伤害/状态/形状/目标/成分/学派/形状）。
        不属于任何维度返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        # 1) 语义标签值集优先（「控场」→ spell_keyword）
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'spell_keyword' LIMIT 1",
            (t,),
        ).fetchone()
        if row:
            return "spell_keyword"
        # 2) 其他法术标签维度（形状/目标/成分/伤害/状态）
        row = conn.execute(
            "SELECT facet FROM entry_tags"
            " WHERE value = ? AND facet IN"
            " ('spell_shape','spell_target','spell_component',"
            "  'dmg_dealt','condition_inflict') LIMIT 1",
            (t,),
        ).fetchone()
        return row["facet"] if row else None

    def resolve_class_free_term(self, term: str) -> str | None:
        """职业裸词自由文本 → 命中的 facet（class_keyword → class_role）。

        用于 /筛职业 裸词消歧。顺序原则：
        - 语义标签（class_keyword，开放集合）值集命中时**优先**——玩家裸词
          「近战/治疗/爆发」的意图几乎总是能力标签而非定位词；
        - 标签未命中时回退职业定位（class_role：武者/奥法/神职/专家）。
        不属于任何维度返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'class_keyword' LIMIT 1",
            (t,),
        ).fetchone()
        if row:
            return "class_keyword"
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'class_role' LIMIT 1",
            (t,),
        ).fetchone()
        return "class_role" if row else None

    def resolve_subclass_free_term(self, term: str) -> str | None:
        """子职裸词自由文本 → 命中的 facet（subclass_keyword）。

        用于 /筛子职 裸词消歧。子职无定位/先决等封闭维度，直接查语义标签
        （subclass_keyword，开放集合）值集；未命中返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'subclass_keyword' LIMIT 1",
            (t,),
        ).fetchone()
        return "subclass_keyword" if row else None

    def resolve_race_free_term(self, term: str) -> str | None:
        """种族裸词自由文本 → 命中的 facet（race_keyword → 既有种族标签维度）。

        用于 /筛种族 裸词消歧。顺序原则：
        - 语义标签（race_keyword，开放集合）值集命中时**优先**——玩家裸词
          「飞行/水陆两栖/变形/天生施法」的意图几乎总是语义大类而非既有
          结构化维度，且标签词与封闭的枚举集合（体型/生物类型/伤害抗性/
          速度类型/天生施法法术名）不冲突；
        - 标签未命中时按既有种族标签维度回退（伤害抗性/免疫/易伤/体型/
          生物类型/速度类型）。
        不属于任何维度返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        # 1) 语义标签值集优先（「飞行」→ race_keyword）
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'race_keyword' LIMIT 1",
            (t,),
        ).fetchone()
        if row:
            return "race_keyword"
        # 2) 既有种族标签维度（伤害/体型/生物类型/速度类型）
        row = conn.execute(
            "SELECT facet FROM entry_tags"
            " WHERE value = ? AND facet IN"
            " ('dmg_resist','dmg_immune','dmg_vuln','size','creature_type',"
            "  'speed_type') LIMIT 1",
            (t,),
        ).fetchone()
        return row["facet"] if row else None

    def resolve_background_free_term(self, term: str) -> str | None:
        """背景裸词自由文本 → 命中的 facet（background_keyword）。

        用于 /筛背景 裸词消歧。背景无既有结构化 facet，直接查语义标签
        （background_keyword，开放集合）值集；未命中返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM entry_tags"
            " WHERE value = ? AND facet = 'background_keyword' LIMIT 1",
            (t,),
        ).fetchone()
        return "background_keyword" if row else None

    def resolve_monster_free_term(self, term: str) -> str | None:
        """怪物裸词自由文本 → 命中的 facet（monster_trait → sense_type →
        alignment → speed_type）。

        用于 /筛怪 裸词消歧（v0.45.0）。顺序原则：
        - 特质名（monster_trait，开放集合——trait 标题中文名）优先：
          玩家裸词「再生/魔法抗性/传奇抗性」的意图几乎总是特性名；
        - 其余封闭维度（sense_type/alignment/speed_type）按值集回退。
        不属于任何维度返回 None（进 unknown）。
        """
        t = (term or "").strip()
        if not t:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT facet FROM entry_tags"
            " WHERE value = ? AND facet IN"
            " ('monster_trait','sense_type','alignment','speed_type')"
            " ORDER BY CASE facet WHEN 'monster_trait' THEN 0"
            " WHEN 'sense_type' THEN 1 WHEN 'alignment' THEN 2 ELSE 3 END"
            " LIMIT 1",
            (t,),
        ).fetchone()
        return row["facet"] if row else None

    def class_features(
        self, class_name: str, subclass: str | None = None,
        feature: str | None = None,
        level_min: int = 0, level_max: int = 0,
        edition: str | None = None,
    ) -> ClassFeatureResult:
        """职业特性：基础特性总表 + 子职特性（subclass 为空时给出候选子职列表）。

        feature 非空时进入「本职特性细化」模式：
          - feature="*" → base_rows 保留全部本职特性（输出全文用）；
          - feature=特性名 → base_rows 只保留名称匹配的行（跨版本，输出该特性全文用）。
        细化模式下 subclass 参数被忽略。
        level_min/level_max（v0.35.0）：基础特性按等级区间过滤（0=不限），
        供升级建议场景取「第 N 级获得什么」。
        edition（v0.48.0）：按规则版本过滤 base_rows 与 subclass_rows
        （仅 "2014"/"2024" 生效；feature 细化模式不过滤，保持单特性跨版本）。
        """
        cn = (class_name or "").strip()
        if not cn:
            return ClassFeatureResult(class_name=cn, eng_name="")
        conn = self._connect()

        cls_row = conn.execute(
            "SELECT * FROM entries WHERE kind = 'class' AND name = ?"
            " ORDER BY edition DESC, source",
            (cn,),
        ).fetchall()
        eng_name = cls_row[0]["eng_name"] if cls_row else ""
        editions = [r["edition"] for r in cls_row]

        # v0.33.0：职业富化（AI 概要 + 定位）从 classes 侧表带出（任一新版有值即可）。
        summary_row = conn.execute(
            "SELECT cl.summary AS cs, cl.role AS cr"
            " FROM entries e LEFT JOIN classes cl ON cl.entry_id = e.id"
            " WHERE e.kind = 'class' AND e.name = ?"
            " AND (cl.summary != '' OR cl.role != '')"
            " ORDER BY e.edition DESC LIMIT 1",
            (cn,),
        ).fetchone()
        class_summary = str(summary_row["cs"] or "") if summary_row else ""
        class_role = str(summary_row["cr"] or "") if summary_row else ""

        def _fetch_rows(where_sql: str, params_: list[Any]) -> list[ClassFeatureRow]:
            rows = conn.execute(
                "SELECT * FROM class_features" + where_sql + " ORDER BY source, level, name",
                params_,
            ).fetchall()
            return [
                ClassFeatureRow(
                    class_name=r["class_name"],
                    subclass_name=r["subclass_name"],
                    source=r["source"],
                    level=r["level"],
                    name=r["name"],
                    summary=r["summary"],
                    body=r["body"],
                )
                for r in rows
            ]

        base_rows = _fetch_rows(
            " WHERE class_name = ? AND subclass_name = ''", [cn]
        )
        # v0.35.0：等级区间过滤（升级建议场景，取特定等级段特性）
        if level_min or level_max:
            base_rows = [
                r for r in base_rows
                if r.level is not None
                and (not level_min or r.level >= level_min)
                and (not level_max or r.level <= level_max)
            ]
        # v0.48.0：版本过滤（仅 2014/2024 生效；单特性细化模式不过滤，跨版本）
        if edition in ("2014", "2024"):
            fq = (feature or "").strip()
            if fq == "" or fq == "*":
                base_rows = [r for r in base_rows if r.edition == edition]

        # v0.29.0 本职特性细化：只返回匹配的特性行（跨版本），subclass 忽略。
        if feature is not None:
            fq = (feature or "").strip()
            if fq and fq != "*":
                base_rows = [r for r in base_rows if r.name == fq]
            return ClassFeatureResult(
                class_name=cn,
                eng_name=eng_name,
                editions=editions,
                base_rows=base_rows,
                feature_query=fq if fq else "*",
                class_summary=class_summary,
                class_role=class_role,
            )

        if subclass:
            # 子职既可按显示名（塑能学派）也可按短名（塑能）匹配。
            sub = (subclass or "").strip()
            sub_rows = _fetch_rows(
                " WHERE class_name = ? AND (subclass_name = ? OR subclass_short = ?)",
                [cn, sub, sub],
            )
            # v0.48.1：默认版本过滤后子职为空 → 回退其他版本展示并标注
            # （如魔射手仅 XGE 2014，2024 群默认版本下直接查会「未找到」）。
            fallback = ""
            if edition in ("2014", "2024"):
                sub_rows = [r for r in sub_rows if r.edition == edition]
                if not sub_rows:
                    alt_rows = _fetch_rows(
                        " WHERE class_name = ? AND (subclass_name = ? OR subclass_short = ?)",
                        [cn, sub, sub],
                    )
                    if alt_rows:
                        alt_ed = alt_rows[0].edition
                        sub_rows = [r for r in alt_rows if r.edition == alt_ed]
                        fallback = alt_ed
            return ClassFeatureResult(
                class_name=cn,
                eng_name=eng_name,
                editions=editions,
                base_rows=base_rows,
                subclass_rows=sub_rows,
                subclass_edition_fallback=fallback,
                class_summary=class_summary,
                class_role=class_role,
            )
        # 未指定子职：给出候选子职名（显示名，去重，含 2014/2024 来源标注）
        cand_rows = conn.execute(
            "SELECT DISTINCT subclass_name FROM class_features"
            " WHERE class_name = ? AND subclass_name != ''"
            " AND subclass_name IS NOT NULL ORDER BY subclass_name",
            (cn,),
        ).fetchall()
        return ClassFeatureResult(
            class_name=cn,
            eng_name=eng_name,
            editions=editions,
            base_rows=base_rows,
            subclass_candidates=[r[0] for r in cand_rows],
            class_summary=class_summary,
            class_role=class_role,
        )

    # -- v0.35.0 构筑咨询查询（spell_classes 侧表 / 专长前置 facet） --

    def spells_by_class(
        self,
        class_name: str,
        edition: str = "",
        spell_keywords: tuple[str, ...] = (),
        level_max: int = 0,
        limit: int = 10,
    ) -> FilterResult:
        """按职业法术表取法术（供构筑咨询/升级建议用）。

        - spell_keywords：语义标签（spell_keyword）AND 过滤；
        - level_max：环阶上限（0=不限）；
        - edition：版本过滤（""=双版本并存）；edition 为后过滤，仅作用于返回条目。
        """
        tags = [("spell_keyword", kw) for kw in spell_keywords] or None
        res = self.filter(
            "spell",
            limit=limit,
            tags=tags,
            spell_class=class_name or None,
            level_max=level_max or None,
        )
        if edition:
            res.entries = [e for e in res.entries if e.edition == edition]
        return res

    def value_facets(self, value: str, facets: tuple[str, ...]) -> list[str]:
        """查询某个标签值在哪些 facet 下真实存在（去重）。

        供构筑咨询 goal 自由文本消歧用：一个词（如「坦克」）可能同时是
        class_keyword/subclass_keyword/race_keyword/feat_keyword 的值，
        返回值集即该词可参与的各反查维度。
        """
        t = (value or "").strip()
        if not t or not facets:
            return []
        conn = self._connect()
        marks = ",".join("?" for _ in facets)
        rows = conn.execute(
            "SELECT DISTINCT facet FROM entry_tags"
            f" WHERE value = ? AND facet IN ({marks})",
            (t, *facets),
        ).fetchall()
        return [r[0] for r in rows]

    def entry_tags_of(
        self, name: str, kind: str,
        facets: tuple[str, ...] | None = None,
    ) -> dict[str, list[str]]:
        """按条目名+kind 取全部/指定 facet 的标签（facet→values 去重）。

        供构筑咨询按角色卡职业名反查职业能力标签用。
        """
        cn = (name or "").strip()
        if not cn:
            return {}
        conn = self._connect()
        sql = (
            "SELECT t.facet, t.value FROM entry_tags t"
            " JOIN entries e ON e.id = t.entry_id"
            " WHERE e.name = ? AND e.kind = ?"
        )
        params: list[Any] = [cn, kind]
        if facets:
            marks = ",".join("?" for _ in facets)
            sql += f" AND t.facet IN ({marks})"
            params.extend(facets)
        rows = conn.execute(sql, params).fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["facet"], []).append(r["value"])
        return out

    def feat_prereq_facets(
        self, entry_id: int
    ) -> dict[str, list[str]]:
        """一次取专长的全部前置 facet（prereq_race/prereq_ability/prereq_feat/
        prereq_feature），供「标注不过滤」的前置校验用（走 idx_tags_fv）。
        """
        conn = self._connect()
        rows = conn.execute(
            "SELECT facet, value FROM entry_tags"
            " WHERE entry_id = ? AND facet LIKE 'prereq_%'"
            " ORDER BY facet, value",
            (entry_id,),
        ).fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["facet"], []).append(r["value"])
        return out

    # -- v0.18.0 规则引擎侧表查询（schema v4，专表专查） --

    def class_combat(self, class_name: str, edition: str = "") -> ClassCombatRow | None:
        """职业战斗数据（生命骰/豁免/施法进度/施法属性）。edition 为空时取任一新版。"""
        cn = (class_name or "").strip()
        if not cn:
            return None
        conn = self._connect()
        sql = (
            "SELECT cc.hd_faces, cc.saves, cc.caster, cc.spell_ability,"
            " e.name, e.source, e.edition"
            " FROM class_combat cc JOIN entries e ON e.id = cc.entry_id"
            " WHERE e.kind = 'class' AND e.name = ?"
        )
        params: list[Any] = [cn]
        if edition:
            sql += " AND e.edition = ?"
            params.append(edition)
        sql += " ORDER BY e.edition DESC, e.source LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        try:
            saves = json.loads(row["saves"] or "[]")
        except (json.JSONDecodeError, TypeError):
            saves = []
        return ClassCombatRow(
            class_name=row["name"],
            source=row["source"],
            edition=row["edition"],
            hd_faces=row["hd_faces"],
            saves=saves if isinstance(saves, list) else [],
            caster=row["caster"] or "",
            spell_ability=row["spell_ability"] or "",
        )

    def subclass_caster(
        self, class_name: str, subclass: str, edition: str = ""
    ) -> tuple[str, str] | None:
        """子职施法进度 → (caster, spell_ability)。显示名或短名均可匹配。

        源数据同名子职存在 caster 为空的重复行（构建期已过滤），此处再兜底
        优先返回 caster 非空且版本匹配的行。
        """
        cn = (class_name or "").strip()
        sn = (subclass or "").strip()
        if not cn or not sn:
            return None
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM subclass_caster"
            " WHERE class_name = ? AND (subclass_name = ? OR subclass_short = ?)",
            (cn, sn, sn),
        ).fetchall()
        if not rows:
            return None
        if edition:
            for r in rows:
                if r["caster"] and edition_of_source(r["source"]) == edition:
                    return (r["caster"], r["spell_ability"] or "")
        return (rows[0]["caster"] or "", rows[0]["spell_ability"] or "")

    def race_ability(self, name: str, edition: str = "") -> AbilityOffer | None:
        """种族属性加值（2014 种族有结构化 ability；2024 种族无，返回 None）。"""
        return self._ability_offer("race", name, edition)

    def race_speed(self, name: str, edition: str = "") -> int | None:
        """种族步行速度（尺/回合），取 races.speed_walk。edition 为空取任一新版。"""
        row = self._race_entry(name, edition)
        if row is None or row["speed_walk"] is None:
            return None
        return int(row["speed_walk"])

    # 种族基础描述段键（体型/年龄/语言等，不是特性，展示时过滤）
    _RACE_BASIC_KEYS: frozenset[str] = frozenset(
        {"年龄", "体型", "速度", "黑暗视觉", "语言", "生物类型", "尺寸", "身高", "重量"}
    )

    def race_features(self, name: str, edition: str = "") -> list[str]:
        """种族特性名列表（卡面展示用）。

        数据来自条目 body 的扁平化段落文本（构建期 _race_body）：跳过
        【种族信息】段，取「特性名：」结尾的段落首行，过滤基础描述键并
        去重。非结构化提取，个别条目可能遗漏或夹带子标题，仅用于展示。
        """
        row = self._race_entry(name, edition)
        if row is None or not row["body"]:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for para in row["body"].split("\n\n"):
            para = para.strip()
            if not para or para.startswith("【种族信息】"):
                continue
            first = para.split("\n", 1)[0].strip()
            if len(first) <= 1 or not first.endswith("："):
                continue
            key = first[:-1].strip()
            if (
                not key
                or len(key) > 20
                or key in self._RACE_BASIC_KEYS
                or key in seen
            ):
                continue
            seen.add(key)
            out.append(key)
        return out[:30]

    def _race_names(self) -> list[str]:
        """库内全部种族名（按长度升序缓存：短名=基础种族优先匹配回退）。"""
        cached = getattr(self, "_race_names_cache", None)
        if cached is None:
            conn = self._connect()
            rows = conn.execute(
                "SELECT DISTINCT e.name FROM races r"
                " JOIN entries e ON e.id = r.entry_id"
            ).fetchall()
            cached = sorted({str(r[0]) for r in rows}, key=len)
            self._race_names_cache = cached
        return cached

    def _race_entry(self, name: str, edition: str = "") -> sqlite3.Row | None:
        """查种族条目行（JOIN races），精确名优先；无结果时按子种族名回退。

        回退规则：输入名以某库内种族名结尾且基础名更短（如「银龙龙裔」→
        「龙裔」、「山丘矮人」→「矮人」）——5etools 2014 子种族在库中无
        独立条目，速度与特性按基础种族取。edition 过滤与精确查询一致。
        """
        cn = (name or "").strip()
        if not cn:
            return None
        conn = self._connect()
        base_sql = (
            "SELECT e.*, r.speed_walk, r.darkvision FROM races r"
            " JOIN entries e ON e.id = r.entry_id"
            " WHERE e.kind = 'race' AND e.name = ?"
        )
        params: list[Any] = [cn]
        if edition:
            base_sql += " AND e.edition = ?"
            params.append(edition)
        base_sql += " ORDER BY e.edition DESC, e.source LIMIT 1"
        row = conn.execute(base_sql, params).fetchone()
        if row is not None:
            return row
        for base in self._race_names():
            if len(base) < len(cn) and cn.endswith(base):
                p2: list[Any] = [base]
                sql2 = (
                    "SELECT e.*, r.speed_walk, r.darkvision FROM races r"
                    " JOIN entries e ON e.id = r.entry_id"
                    " WHERE e.kind = 'race' AND e.name = ?"
                )
                if edition:
                    sql2 += " AND e.edition = ?"
                    p2.append(edition)
                sql2 += " ORDER BY e.edition DESC, e.source LIMIT 1"
                row2 = conn.execute(sql2, p2).fetchone()
                if row2 is not None:
                    return row2
        return None

    def background_ability(self, name: str) -> AbilityOffer | None:
        """背景属性加值（2024 背景才有；2014 背景无 ability，返回 None）。"""
        return self._ability_offer("background", name, "")

    def item_combat(self, name: str) -> ItemCombatRow | None:
        """护甲/武器战斗字段（按物品名精确命中，同名取任一新版）。"""
        cn = (name or "").strip()
        if not cn:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT ic.ac, ic.armor_type, ic.strength, ic.stealth,"
            " ic.dmg1, ic.properties, ic.range_note, e.name, e.source, e.edition"
            " FROM item_combat ic JOIN entries e ON e.id = ic.entry_id"
            " WHERE e.kind = 'item' AND e.name = ?"
            " ORDER BY e.edition DESC, e.source LIMIT 1",
            (cn,),
        ).fetchone()
        if not row:
            return None
        props = [p for p in (row["properties"] or "").split(",") if p]
        return ItemCombatRow(
            name=row["name"],
            source=row["source"],
            edition=row["edition"],
            ac=row["ac"],
            armor_type=row["armor_type"] or "",
            strength=row["strength"],
            stealth=bool(row["stealth"]),
            dmg1=row["dmg1"] or "",
            properties=props,
            range_note=row["range_note"] or "",
        )

    def item_base_item(self, name: str) -> str:
        """魔法武器的基础武器名（entry_tags 的 base_item facet，v0.21.1）。

        具名魔法武器有该标签（雷神之锤→巨锤、阳炎剑→长剑），武器熟练判定
        用它把魔法武器解析回基础武器再按简易/军用分类；+N 武器与基础武器
        本身没有该标签，返回空串（由调用方做词表后缀兜底）。同名多版本
        取任一条。查不到返回空串。
        """
        cn = (name or "").strip()
        if not cn:
            return ""
        conn = self._connect()
        row = conn.execute(
            "SELECT t.value FROM entry_tags t"
            " JOIN entries e ON e.id = t.entry_id"
            " WHERE e.kind = 'item' AND e.name = ? AND t.facet = 'base_item'"
            " ORDER BY e.edition DESC, e.source LIMIT 1",
            (cn,),
        ).fetchone()
        return row["value"] if row else ""

    # -- 商店（v0.20.0，schema v5） --

    def item_price(self, name: str) -> tuple[int | None, float | None] | None:
        """按名称查库价 → (value_cp 铜币, weight_lb 磅)。

        同名多版本取 2024/XPHB 优先（CASE 排前），找不到返回 None；
        条目存在但无 value/weight 时对应字段为 None。
        """
        cn = (name or "").strip()
        if not cn:
            return None
        conn = self._connect()
        row = conn.execute(
            "SELECT i.value_cp, i.weight_lb FROM entries e"
            " JOIN items i ON i.entry_id = e.id"
            " WHERE e.kind = 'item' AND e.name = ?"
            " ORDER BY CASE WHEN e.source = 'XPHB' THEN 0 ELSE 1 END,"
            "  e.edition DESC, e.source LIMIT 1",
            (cn,),
        ).fetchone()
        if not row:
            return None
        return row["value_cp"], row["weight_lb"]

    def list_init_shop_items(
        self,
    ) -> list[tuple[str, str, str, int | None, float | None]]:
        """初始商店候选：PHB/XPHB 非魔法物品 → (名称, 来源, 版本, 价值cp, 重量lb)。

        - 只收 `rarity='none'` 且来源为 PHB(2014)/XPHB(2024) 的物品；
        - 同名多版本去重，2024/XPHB 优先（reprintedAs 已在构建期把旧版跳转为
          新版别名，此处排序是兜底双保险）；
        - 无库价（value_cp IS NULL，如部分无价值杂物）不上架；
        - 重量允许缺失（None，购买时背包条目不写重量字段）。
        """
        conn = self._connect()
        rows = conn.execute(
            "SELECT e.name, e.source, e.edition, i.value_cp, i.weight_lb"
            " FROM entries e JOIN items i ON i.entry_id = e.id"
            " WHERE e.kind = 'item' AND i.rarity = 'none'"
            " AND e.source IN ('PHB', 'XPHB') AND i.value_cp IS NOT NULL"
            " ORDER BY CASE WHEN e.source = 'XPHB' THEN 0 ELSE 1 END,"
            "  e.edition DESC, e.name"
        ).fetchall()
        seen: set[str] = set()
        out: list[tuple[str, str, str, int | None, float | None]] = []
        for r in rows:
            if r["name"] in seen:
                continue
            seen.add(r["name"])
            out.append(
                (r["name"], r["source"], r["edition"], r["value_cp"], r["weight_lb"])
            )
        return out

    def item_stats_lines(self, entries: list[KbEntry]) -> list[str]:
        """物品详情的「价值/重量」附加行（查询期格式化，不冻结进 body）。

        按 (名称, 来源) 对齐每个版本行；缺失字段的条目跳过。显示样例：
        「📦 匕首[2014]：价值：2金｜重量：1 磅」。
        """
        if not entries:
            return []
        names = sorted({e.name for e in entries})
        placeholders = ",".join("?" * len(names))
        rows = self._connect().execute(
            "SELECT e.name, e.source, e.edition, i.value_cp, i.weight_lb"
            " FROM entries e JOIN items i ON i.entry_id = e.id"
            f" WHERE e.kind = 'item' AND e.name IN ({placeholders})",
            names,
        ).fetchall()
        stats = {(r["name"], r["source"]): r for r in rows}
        lines: list[str] = []
        for e in entries:
            r = stats.get((e.name, e.source))
            if r is None:
                continue
            parts: list[str] = []
            if r["value_cp"] is not None:
                parts.append(f"价值：{format_cp(r['value_cp'])}")
            if r["weight_lb"] is not None:
                parts.append(f"重量：{r['weight_lb']:g} 磅")
            if parts:
                lines.append(f"📦 {e.name}[{e.edition_label}]：" + "｜".join(parts))
        return lines

    # -- 车卡起始财富（v0.20.0） --

    def starting_gold(self, class_name: str, edition: str = "") -> tuple[str, int] | None:
        """职业起始金币骰式 → (骰式, 乘数) | None。

        数据来自 class_starting_equipment payload 的 goldAlternative（仅 2014
        职业有，如「{@dice 5d4 × 10|5d4 × 10|起始金币}」→ ("5d4", 10)，即
        掷 5d4 再 ×10 金币）。2024 职业的起始财富内嵌在装备方案
        defaultData 的 {"value": N}（铜币）且需玩家选择 A/B/C 方案，
        不支持自动发放，返回 None。
        """
        cn = (class_name or "").strip()
        if not cn:
            return None
        conn = self._connect()
        params: list[Any] = [cn]
        sql = (
            "SELECT cse.payload FROM class_starting_equipment cse"
            " JOIN entries e ON e.id = cse.entry_id"
            " WHERE e.kind = 'class' AND e.name = ?"
        )
        if edition:
            sql += " AND e.edition = ?"
            params.append(edition)
        sql += " ORDER BY e.edition DESC, e.source LIMIT 1"
        try:
            row = conn.execute(sql, params).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            return None
        gold = payload.get("goldAlternative")
        if not isinstance(gold, str):
            return None
        # {@dice 5d4 × 10|5d4 × 10|起始金币} → 取第一个 | 前的表达式
        m = re.search(r"\{@dice\s+([^|]+)\|", gold)
        expr = m.group(1).strip() if m else gold.strip()
        expr = expr.replace("×", "x").replace("X", "x")
        mm = re.match(r"(.+?)\s*x\s*(\d+)$", expr)
        if mm:
            return mm.group(1).strip(), int(mm.group(2))
        return expr, 1

    # -- 内部辅助 --

    def _ability_offer(
        self, kind: str, name: str, edition: str
    ) -> AbilityOffer | None:
        table = "race_ability" if kind == "race" else "background_ability"
        cn = (name or "").strip()
        if not cn:
            return None
        conn = self._connect()
        sql = (
            "SELECT a.payload FROM " + table + " a"
            " JOIN entries e ON e.id = a.entry_id"
            " WHERE e.kind = ? AND e.name = ?"
        )
        params: list[Any] = [kind, cn]
        if edition:
            sql += " AND e.edition = ?"
            params.append(edition)
        sql += " ORDER BY e.edition DESC, e.source LIMIT 1"
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        return _parse_ability_payload(row["payload"])

    # ------------------------------------------------------------------
    # 格式化（静态方法，供命令与 LLM 工具复用）
    # ------------------------------------------------------------------

    @staticmethod
    def format_entry(entry: KbEntry, max_len: int = MAX_ENTRY_LEN) -> str:
        # v0.44.0：法术走 PHB 卡片式（标题纯净/概要/标签/版本行），其他种类不变
        if entry.kind == "spell":
            return KnowledgeBaseManager._format_spell_entry(entry, max_len)
        # v0.50.0：可定制职业选项（魔能祈唤/战技/超魔法/战斗风格）卡片式
        if entry.kind == "optionalfeature":
            return KnowledgeBaseManager._format_opt_entry(entry, max_len)
        head = f"【{entry.name} {entry.eng_name}】[{entry.edition_label}]"
        if entry.homebrew_label:
            head += f" {entry.homebrew_label}"
        if entry.machine_label:
            head += f" {entry.machine_label}"
        body = entry.body
        if len(body) > max_len:
            body = body[:max_len] + f"\n…（内容过长已截断，可用更精确的条件查询）"
        # 专长带 AI 一句话概要（v0.26.0），头部展示便于 LLM/玩家快速理解
        if entry.feat_summary:
            head += f"\n概要：{entry.feat_summary}"
        # 法术带 AI 一句话概要（v0.27.0）
        if entry.spell_summary:
            head += f"\n概要：{entry.spell_summary}"
        # 职业/子职带 AI 一句话概要 + 职业定位（v0.33.0）
        if entry.class_summary:
            head += f"\n概要：{entry.class_summary}"
        if entry.class_role:
            head += f"\n定位：{entry.class_role}"
        # 种族/背景带 AI 一句话概要（v0.34.0）
        if entry.race_summary:
            head += f"\n概要：{entry.race_summary}"
        if entry.background_summary:
            head += f"\n概要：{entry.background_summary}"
        # 专长类型/属性提升（v0.26.1，/查专长 详情展示）
        if entry.feat_type_label or entry.feat_ability:
            meta_parts = []
            if entry.feat_type_label:
                meta_parts.append(f"类型：{entry.feat_type_label}")
            if entry.feat_ability:
                meta_parts.append(f"属性提升：{entry.feat_ability}")
            head += "\n" + "；".join(meta_parts)
        return f"{head}\n{body}"

    @staticmethod
    def _format_opt_entry(entry: KbEntry, max_len: int = MAX_ENTRY_LEN) -> str:
        """可定制职业选项卡片式（v0.50.0）：标题纯净 + 类型 + 正文 + 版本行。

        先决/消耗行已由构建期 _optionalfeature_body 渲染进 body 首段。
        """
        title = f"{entry.name}｜{entry.eng_name}" if entry.eng_name else entry.name
        head_lines = [title]
        if entry.opt_type_label:
            head_lines.append(f"类型：{entry.opt_type_label}")
        body = entry.body
        if len(body) > max_len:
            body = body[:max_len] + "\n…（内容过长已截断，可用更精确的条件查询）"
        flags = " ".join(
            f for f in (entry.machine_label, entry.homebrew_label) if f
        )
        footer = f"版本：{entry.edition_label}" + (f" {flags}" if flags else "")
        return "\n\n".join(["\n".join(head_lines), body, footer])

    @staticmethod
    def _format_spell_entry(entry: KbEntry, max_len: int = MAX_ENTRY_LEN) -> str:
        """法术卡片式详情（v0.44.0，ADR-0019）。

        标题行纯净（无【】无版本），概要/标签在标题下、环位行前；
        卡片体（环位行/属性行/正文/升环段）为构建期预渲染（chm_parser._build_body
        或 kb_build_lib._spell_body）；版本行放底部并承载机翻/房规标记。
        """
        title = f"{entry.name}｜{entry.eng_name}" if entry.eng_name else entry.name
        head_lines = [title]
        if entry.spell_summary:
            head_lines.append(f"概要：{entry.spell_summary}")
        if entry.spell_keywords:
            head_lines.append(f"标签：{entry.spell_keywords}")
        body = entry.body
        if len(body) > max_len:
            body = body[:max_len] + "\n…（内容过长已截断，可用更精确的条件查询）"
        flags = " ".join(
            f for f in (entry.machine_label, entry.homebrew_label) if f
        )
        footer = f"版本：{entry.edition_label}" + (f" {flags}" if flags else "")
        return "\n\n".join(["\n".join(head_lines), body, footer])

    @staticmethod
    def format_detail(entries: list[KbEntry]) -> str:
        if not entries:
            return ""
        blocks = [KnowledgeBaseManager.format_entry(e) for e in entries]
        if len(entries) > 1:
            head = (
                f"找到 {len(entries)} 个版本（同名不同版法术/条目）：\n"
            )
            return head + "\n\n".join(blocks)
        return blocks[0]

    @staticmethod
    def format_hits(hits: list[SearchHit]) -> str:
        lines = ["未找到完全匹配的条目，以下是相近候选："]
        for i, h in enumerate(hits, 1):
            flag = f" {_MACHINE_FLAG}" if h.is_machine else ""
            if h.is_homebrew:
                flag += f" {HOMEBREW_FLAG}"
            lines.append(
                f"{i}. 【{h.kind}】{h.name} {h.eng_name}"
                f"[{h.edition_label}]{flag} — {h.summary}"
            )
        lines.append("请输入更完整的名称，或 @ 助手用自然语言提问。")
        return "\n".join(lines)

    @staticmethod
    def format_hits_grouped(
        hits: list[SearchHit], query: str = "", limit: int = 20
    ) -> str:
        """跨库广搜结果：按条目类别分组展示（/查询 指令用）。

        编号跨组连续，便于对照；条目过多时提示限量。
        """
        if not hits:
            return f"未找到与「{query}」相关的条目。"
        groups: dict[str, list[SearchHit]] = {}
        for h in hits:
            groups.setdefault(h.kind, []).append(h)
        lines = [f"🔎 跨库搜索「{query}」结果："]
        idx = 0
        for kkind in _KIND_ORDER:
            group = groups.get(kkind)
            if not group:
                continue
            lines.append(f"【{_KIND_LABEL[kkind]}】")
            for h in group:
                idx += 1
                flag = f" {_MACHINE_FLAG}" if h.is_machine else ""
                if h.is_homebrew:
                    flag += f" {HOMEBREW_FLAG}"
                lines.append(
                    f"{idx}. {h.name} {h.eng_name}[{h.edition_label}]{flag}"
                    f" — {h.summary}"
                )
        footer = f"共 {len(hits)} 条"
        if len(hits) >= limit:
            footer += f"，仅显示前 {limit} 条"
        footer += "；输入 /查法术 <名称> 等指令查看详情。"
        lines.append(footer)
        return "\n".join(lines)

    @staticmethod
    def format_filter_result(
        result: FilterResult,
        kind_label: str,
        unknown: list[str] | None = None,
        limit: int = 20,
    ) -> str:
        """特性筛选结果：总数头 + 限量列表 + 未识别条件 + 收窄引导。"""
        unknown = unknown or []
        if not result.entries:
            head = f"没有符合条件的{kind_label}。"
        else:
            shown = min(limit, len(result.entries))
            head = f"共 {result.total} 条符合条件的{kind_label}，仅显示前 {shown} 条："
        lines = [head]
        for i, e in enumerate(result.entries, 1):
            flag = f" {_MACHINE_FLAG}" if e.is_machine else ""
            if e.is_homebrew:
                flag += f" {HOMEBREW_FLAG}"
            meta = KnowledgeBaseManager._entry_meta(e)
            # 专长/法术/职业子职/种族/背景优先用 AI 一句话概要，其余截正文首行
            summary = (
                e.feat_summary
                if e.feat_summary
                else e.spell_summary
                if e.spell_summary
                else e.class_summary
                if e.class_summary
                else e.race_summary
                if e.race_summary
                else e.background_summary
                if e.background_summary
                else KnowledgeBaseManager._summary_of(e.body)
            )
            lines.append(
                f"{i}. {e.name}{meta}{flag} [{e.edition_label}] — {summary}"
            )
        if unknown:
            lines.append("未识别条件：" + "、".join(unknown))
        if kind_label == "怪物":
            # v0.45.0：怪物细分维度提示（伤害四类/状态免疫/速度/感官/阵营/特性）
            lines.append(
                "可追加条件收窄（伤害类型/状态/速度/感官/阵营/特性/CR等），"
                "如：/筛怪 火焰免疫、/筛怪 震慑免疫、/筛怪 真实视觉、"
                "/筛怪 守序善良。"
            )
        else:
            lines.append(
                "可追加条件收窄（伤害类型/状态/环境/CR/环级/稀有度等），"
                "如：/筛怪 火焰 CR5以下、/筛法术 专注 3环。"
            )
        return "\n".join(lines)

    @staticmethod
    def _entry_meta(entry: KbEntry) -> str:
        """筛选列表中的条目特征后缀（怪物 CR / 法术环级 / 物品稀有度 / 种族速度）。"""
        if entry.kind == "monster" and entry.cr is not None:
            return f"（CR {entry.cr}）"
        if entry.kind == "spell" and entry.level is not None:
            return f"（{entry.level}环）"
        if entry.kind == "item" and entry.rarity:
            return f"（{format_rarity(entry.rarity)}）"
        if entry.kind == "race":
            parts = []
            if entry.speed_walk:
                parts.append(f"步行{entry.speed_walk}尺")
            for attr, cn in (
                ("speed_climb", "攀爬"), ("speed_swim", "游泳"),
                ("speed_fly", "飞行"), ("speed_burrow", "掘穴"),
            ):
                v = getattr(entry, attr)
                if v:
                    parts.append(f"{cn}{v}尺")
            if parts:
                return "（" + "｜".join(parts) + "）"
        return ""

    # ------------------------------------------------------------------
    # 职业特性分层展示（v0.48.0，ADR-0023）
    # ------------------------------------------------------------------

    @staticmethod
    def _class_head(result: ClassFeatureResult, editions: list[str]) -> str:
        """职业头部：名称+英文+版本+定位+概要。editions 决定版本标注（已过滤时单版本）。"""
        head = f"【{result.class_name} {result.eng_name}】"
        if editions:
            head += "（" + "、".join(f"{e}版" for e in editions) + "）"
        # v0.33.0：职业富化（AI 一句话概要 + 职业定位）展示在头部
        if result.class_role:
            head += f"\n定位：{result.class_role}"
        if result.class_summary:
            head += f"\n概要：{result.class_summary}"
        return head

    @staticmethod
    def _group_tiers(
        rows: list[ClassFeatureRow],
    ) -> list[ClassTierSegment]:
        """把特性行按 (edition, tier) 分组：edition 降序（新在前）、tier 升序。

        段标题是否带版本前缀由调用方按「多版本存在」决定。
        """
        groups: list[ClassTierSegment] = []
        for row in rows:
            tier = tier_of(row.level)
            if tier is None:
                continue
            seg = next(
                (g for g in groups if g.edition == row.edition and g.tier == tier),
                None,
            )
            if seg is None:
                seg = ClassTierSegment(
                    tier=tier,
                    label=CLASS_TIERS[tier - 1][2],
                    edition=row.edition,
                    rows=[],
                )
                groups.append(seg)
            seg.rows.append(row)
        groups.sort(key=lambda g: (g.edition != "2024", g.edition != "2014", g.tier))
        return groups

    @staticmethod
    def _clean_row_text(text: str) -> str:
        """显示层兜底剥除残留 5etools 标签（v0.48.0；构建期已清洗的新库幂等）。"""
        return clean_5etools_tags(text) if text else text

    @staticmethod
    def build_class_display(result: ClassFeatureResult) -> ClassDisplay:
        """构建分层展示结构（概要层 + 全文层 + 子职 + 提示）。"""
        head = KnowledgeBaseManager._class_head(result, result.editions)
        display = ClassDisplay(head=head)
        display.has_data = bool(
            result.base_rows or result.subclass_rows or result.subclass_candidates
        )

        # 概要层：默认只展示「第一优先级版本」（editions 已按 edition DESC）。
        # 命令层会先按版本过滤，这里兜底处理未过滤（如 LLM 工具）的多版本数据：
        # 只取最新版本的行，其余版本仅出现在提示里（避免概要层重新变长）。
        base_editions = sorted({r.edition for r in result.base_rows},
                               key=lambda e: e != "2024")
        if not result.feature_query and result.base_rows:
            primary = base_editions[0]
            primary_rows = [r for r in result.base_rows if r.edition == primary]
            display.overview = KnowledgeBaseManager._group_tiers(primary_rows)
            # 职业存在其他版本（未展示）→ 提示可看旧版（覆盖参数）。
            for other in result.editions:
                if other != primary:
                    display.prompts.append(
                        f"另有 {other} 版，回复「查职业 <职业> {other}」查看。"
                    )
                    break

        # 全文层：特性细化 / 等级段钻取（base_rows 已按目标过滤）→ 按层级段分组。
        if result.base_rows:
            display.full_segments = KnowledgeBaseManager._group_tiers(result.base_rows)

        # 子职：v0.11.0 一次给齐（全量）；版本策略只保留第一优先级版本。
        if result.subclass_rows:
            sub_editions = sorted({r.edition for r in result.subclass_rows},
                                  key=lambda e: e != "2024")
            primary = sub_editions[0]
            sub_rows = [r for r in result.subclass_rows if r.edition == primary]
            sub_name = sub_rows[0].subclass_name if sub_rows else ""
            lines: list[str] = []
            if sub_name:
                lines.append(f"【{result.class_name}·子职 {sub_name}】")
            # v0.48.1：版本回退标注（默认版本无该子职 → 已回退其他版本展示）
            if result.subclass_edition_fallback:
                lines.append(
                    f"（该子职仅在 {result.subclass_edition_fallback} 版，"
                    f"已按 {result.subclass_edition_fallback} 版展示）"
                )
            for row in sub_rows:
                body = KnowledgeBaseManager._clean_row_text(
                    row.body or row.summary or ""
                )
                if len(body) > MAX_ENTRY_LEN:
                    body = body[:MAX_ENTRY_LEN] + "\n…（内容过长已截断）"
                lines.append(f"◆ {row.level} 级 {row.name}：")
                lines.append(body)
                lines.append("")
            while lines and lines[-1] == "":
                lines.pop()
            display.subclass_part = "\n".join(lines)
        elif result.subclass_candidates:
            display.subclass_part = (
                "可选子职：" + "、".join(result.subclass_candidates)
            )
            display.prompts.append(
                "回复「查职业 <职业> <子职名>」查看某个子职的全部能力。"
            )

        # 提示：概要层 → 钻取引导；全文层 → 单特性钻取。
        if not result.feature_query and result.base_rows:
            display.prompts.append(
                "回复「查职业 <职业> 特性」查看本职特性完整说明。"
            )
            display.prompts.append(
                "回复「查职业 <职业> 第2层」查看某层级详情。"
            )
        elif result.feature_query and result.base_rows:
            display.prompts.append(
                "回复「查职业 <职业> 特性 <特性名>」查看单个特性。"
            )
        return display

    @staticmethod
    def _render_overview_segment(seg: ClassTierSegment) -> str:
        """L1 概要段：每特性一行「N级 名称：一句话概要」。"""
        lines = [f"【{seg.label}】"]
        for row in seg.rows:
            summary = KnowledgeBaseManager._clean_row_text(
                row.summary or _first_line(row.body or "")
            )
            lines.append(f"{row.level}级 {row.name}：{summary}")
        return "\n".join(lines)

    @staticmethod
    def _render_full_segment(seg: ClassTierSegment, class_name: str) -> str:
        """L3 全文段：段标题 + 每条特性完整正文。"""
        lines = [f"【{class_name}·{seg.label}】"]
        for row in seg.rows:
            body = KnowledgeBaseManager._clean_row_text(row.body or row.summary or "")
            if len(body) > MAX_ENTRY_LEN:
                body = body[:MAX_ENTRY_LEN] + "\n…（内容过长已截断）"
            lines.append(f"◆ {row.level} 级 {row.name}：")
            lines.append(body)
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def format_class_features(result: ClassFeatureResult) -> str:
        """职业特性单条消息输出（LLM 工具与兼容路径）。

        - 非细化模式：head + 概要层 + 子职 + 提示（默认只展示最新版本概要）；
        - feature 细化模式：head + 全文层（跨版本按层级段）+ 提示。
        """
        if not result.class_name:
            return "请提供职业名称。"
        display = KnowledgeBaseManager.build_class_display(result)
        parts: list[str] = [display.head]
        if result.feature_query:
            if result.feature_query != "*":
                parts.append(f"特性「{result.feature_query}」")
            if display.full_segments:
                for seg in display.full_segments:
                    parts.append(
                        KnowledgeBaseManager._render_full_segment(
                            seg, result.class_name
                        )
                    )
            else:
                parts.append(
                    f"（未找到该职业的「{result.feature_query}」特性）"
                    if result.feature_query != "*"
                    else "（未找到该职业的本职特性数据）"
                )
        else:
            for seg in display.overview:
                parts.append(KnowledgeBaseManager._render_overview_segment(seg))
            if display.subclass_part:
                parts.append(display.subclass_part)
            elif not display.has_data:
                parts.append("（未找到该职业的特性数据）")
        parts.extend(display.prompts)
        return "\n\n".join(p for p in parts if p)

    @staticmethod
    def class_display_messages(
        result: ClassFeatureResult, full: bool = False
    ) -> list[str]:
        """职业特性分条输出（命令层 multi-yield 用，v0.48.0）。

        - full=False（概要层）：单条；超 3600 字符时按层级拆多条（保险）。
        - full=True（全文层/钻取）：head + 每条一层的全文；每条独立一条消息。
        """
        if not result.class_name:
            return ["请提供职业名称。"]
        display = KnowledgeBaseManager.build_class_display(result)
        msgs: list[str] = []
        if full or result.feature_query:
            # 单特性：head + 段合并为单条（内容短，无需分条）
            if result.feature_query and result.feature_query != "*":
                parts = [display.head, f"特性「{result.feature_query}」"]
                for seg in display.full_segments:
                    parts.append(
                        KnowledgeBaseManager._render_full_segment(
                            seg, result.class_name
                        )
                    )
                if display.prompts:
                    parts.append("\n".join(display.prompts))
                if not display.full_segments:
                    parts.append(
                        f"（未找到该职业的「{result.feature_query}」特性）"
                    )
                return ["\n\n".join(parts)]
            # 全量（feature="*" / 钻取）：head 一条，每层一条
            msgs.append(display.head)
            for seg in display.full_segments:
                msgs.append(
                    KnowledgeBaseManager._render_full_segment(
                        seg, result.class_name
                    )
                )
            if display.prompts:
                msgs.append("\n".join(display.prompts))
            if not display.full_segments:
                msgs.append(
                    f"（未找到该职业的「{result.feature_query}」特性）"
                    if result.feature_query
                    else "（未找到该职业的本职特性数据）"
                )
            return msgs

        # 概要层：整段拼接，超长按层级拆
        body_parts = [display.head]
        for seg in display.overview:
            body_parts.append(KnowledgeBaseManager._render_overview_segment(seg))
        if display.subclass_part:
            body_parts.append(display.subclass_part)
        elif not display.has_data:
            body_parts.append("（未找到该职业的特性数据）")
        body_parts.extend(display.prompts)
        joined = "\n\n".join(p for p in body_parts if p)
        if len(joined) <= 3600:
            return [joined]
        # 保险拆条：head+提示一条，每层一条
        head = display.head
        prompts = "\n".join(display.prompts)
        seg_msgs = []
        for seg in display.overview:
            seg_msgs.append(KnowledgeBaseManager._render_overview_segment(seg))
        if display.subclass_part:
            seg_msgs.append(display.subclass_part)
        tail = [prompts] if prompts else []
        return [head] + seg_msgs + tail

    @staticmethod
    def format_version(version: dict[str, str]) -> str:
        if not version:
            return "知识库不可用（缺少 meta 信息）。"
        lines = [
            f"📚 知识库版本：{version.get('data_version', '未知')}",
            f"数据来源：5etools 中文站（cn2.0）@ {version.get('source_commit', '未知')[:12]}",
            f"构建时间：{version.get('build_time', '未知')}",
            "数据许可：CC BY-NC-SA 4.0，译文版权归原译者（不全书等）所有。",
        ]
        if version.get("schema_version"):
            lines.append(f"Schema 版本：{version['schema_version']}")
        return "\n".join(lines)

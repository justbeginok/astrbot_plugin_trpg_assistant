"""homebrew.py — 运行期私设（homebrew）加载器（v0.36.0）。

普通 DM 拿不到 5etools 数据源、不会跑 build_kb.py 全量重建，因此私设不走
构建期补丁通道，而是**运行期增量加载**：

- 目录约定：`{AstrBot data_dir}/trpg_homebrew/*.json`（用户放置，可分享、
  升级插件不丢；文件在 data_dir 不在插件包内）。
- 双格式：5etools 社区标准 JSON（顶层 key=条目类型，值=条目数组，
  正文走 entries 字段由 kb_build_lib 渲染）与简化格式（条目内显式
  kind/name/source/body 字段，body 直接给纯文本）。
- 合并语义：私设 source 独立 → 纯新增；与官方 (kind,name,source) 相同 →
  覆盖官方（房规修正），查询结果标注「房规」。
- 健壮性：单个文件解析失败只跳过该文件并记日志，绝不影响插件运行。

模块结构遵循项目惯例：dataclass + Manager（单 asyncio.Lock，reload 原子
替换）+ format_* 静态方法。本模块只负责「文件 → 内存 overlay 池」，与
KbManager 的查询合并见 kb.py（KbManager._merge_homebrew_*）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .kb_build_lib import (
    _ability_payload,
    _item_combat_cols,
    _item_value_weight,
    _kind_body,
    _monster_type,
    _parse_cr,
    _race_speed_cols,
)
from .kb_enums import FEAT_CATEGORY_CN, edition_of_source

logger = logging.getLogger("astrbot_plugin_trpg_assistant")

# 私设默认来源码（用户可覆盖）；独立 source 保证不与官方条目撞唯一键。
DEFAULT_SOURCE = "HOMEBREW"

# 私设展示标记（命令/工具返回里追加在条目名后）。
HOMEBREW_FLAG = "🏠房规"

# 支持的类型：中文名 / 英文名（含复数）→ canonical kind。
_CN_KIND: dict[str, str] = {
    "法术": "spell", "怪物": "monster", "物品": "item", "专长": "feat",
    "背景": "background", "状态": "condition", "种族": "race",
    "职业": "class", "子职": "subclass",
}
_EN_KIND: dict[str, str] = {
    "spell": "spell", "spells": "spell",
    "monster": "monster", "monsters": "monster",
    "item": "item", "items": "item",
    "feat": "feat", "feats": "feat",
    "background": "background", "backgrounds": "background",
    "condition": "condition", "conditions": "condition",
    "race": "race", "races": "race",
    "class": "class", "classes": "class",
    "subclass": "subclass", "subclasses": "subclass",
}

_EDITION_OK = ("2014", "2024", "other")

# canonical kind → 中文标签（manage_homebrew 锚点/冲突清单展示用）。
KIND_LABEL: dict[str, str] = {v: k for k, v in _CN_KIND.items()}


def resolve_kind(raw: Any) -> str | None:
    """kind 归一：中文/英文（含复数）→ canonical；无效返回 None。"""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s in _CN_KIND:
        return _CN_KIND[s]
    return _EN_KIND.get(s.lower())


@dataclass
class HomebrewEntry:
    """一条运行期私设条目（内存 overlay 中的最小单元）。"""

    kind: str
    name: str
    source: str
    edition: str
    body: str
    eng_name: str = ""
    is_machine: int = 0
    # 侧表字段：{字段名: 值}（spell level/school、item rarity/value_cp…）
    side: dict[str, Any] = field(default_factory=dict)
    # 手工标签 [(facet, value), ...]（用户可选写；供 /筛X 反查）
    tags: list[tuple[str, str]] = field(default_factory=list)
    # 来源文件（错误定位 / 统计）
    file: str = ""
    # 是否覆盖官方条目（加载时按官方键集判定置位）
    is_override: bool = False

    @property
    def edition_label(self) -> str:
        return f"{self.source}·{self.edition}"


@dataclass
class HomebrewLoadResult:
    """一次加载/重载的结果：文件数 / 条目数 / 覆盖数 / 错误与告警。"""

    files: int = 0
    entries: int = 0
    overrides: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class HomebrewManager:
    """私设 overlay 池：文件 → 内存条目，reload 原子替换。

    - 目录不存在视为空 overlay（不报错）；
    - load()/reload() 同步执行（私设文件小、查询层亦为同步），替换期间
      Python GIL 保证查询读到的是旧池或新池，不产生半写状态。
    """

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._entries: list[HomebrewEntry] = []
        self._loaded = False

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def loaded(self) -> bool:
        return self._loaded

    def entries(self) -> list[HomebrewEntry]:
        """返回当前 overlay 池（快照列表，调用方只读）。"""
        return list(self._entries)

    def load(self, official_keys: set[tuple[str, str, str]] | None = None
             ) -> HomebrewLoadResult:
        """扫描目录并解析；official_keys 供统计覆盖数。"""
        result = HomebrewLoadResult()
        if not self._directory.is_dir():
            self._entries = []
            self._loaded = True
            return result
        new_entries: list[HomebrewEntry] = []
        files = sorted(self._directory.glob("*.json"))
        for path in files:
            result.files += 1
            try:
                parsed, warn = self._parse_file(path)
                new_entries.extend(parsed)
                result.warnings.extend(warn)
            except Exception as exc:  # noqa: BLE001 — 单文件失败不拖垮加载
                result.errors.append(
                    f"{path.name}: {type(exc).__name__}: {exc}"
                )
        seen: set[tuple[str, str, str]] = set()
        dedup: list[HomebrewEntry] = []
        for e in new_entries:
            key = (e.kind, e.name, e.source)
            if key in seen:
                result.warnings.append(
                    f"{e.file}: 重复条目 {e.kind}「{e.name}」({e.source})，仅保留首个"
                )
                continue
            seen.add(key)
            dedup.append(e)
            if official_keys and key in official_keys:
                e.is_override = True
                result.overrides += 1
        self._entries = dedup
        self._loaded = True
        result.entries = len(dedup)
        return result

    # ------------------------------------------------------------------
    # 文件解析（纯函数，便于单测）
    # ------------------------------------------------------------------

    def _parse_file(self, path: Path) -> tuple[list[HomebrewEntry], list[str]]:
        """解析单个 JSON 文件 → (条目列表, 告警列表)。

        顶层结构支持三种：
        - {"spell": [...], "item": [...]}   —— 5etools 格式（key=类型）；
        - {"items": [...], "法术": [...]}   —— key 带复数/中文亦可；
        - [ {...}, {...} ]                  —— 纯数组，条目内必须带 kind。
        """
        warnings: list[str] = []
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return self._parse_batch(None, raw, path, warnings), warnings
        if not isinstance(raw, dict):
            raise ValueError("JSON 顶层必须是对象或数组")
        out: list[HomebrewEntry] = []
        for key, value in raw.items():
            kind = resolve_kind(key)
            if kind is None or not isinstance(value, list):
                warnings.append(
                    f"{path.name}: 跳过无法识别的顶层键「{key}」（应为条目类型名）"
                )
                continue
            out.extend(self._parse_batch(kind, value, path, warnings))
        return out, warnings

    def _parse_batch(
        self,
        kind_default: str | None,
        items: list[Any],
        path: Path,
        warnings: list[str],
    ) -> list[HomebrewEntry]:
        out: list[HomebrewEntry] = []
        for i, item in enumerate(items):
            try:
                entry = self._parse_entry(kind_default, item, path.name)
            except ValueError as exc:
                warnings.append(f"{path.name}[{i}]: {exc}")
                continue
            if entry is not None:
                out.append(entry)
        return out

    def _parse_entry(
        self, kind_default: str | None, raw: Any, filename: str
    ) -> HomebrewEntry | None:
        if not isinstance(raw, dict):
            raise ValueError("条目必须是 JSON 对象")
        kind = resolve_kind(raw.get("kind")) or kind_default
        if kind is None:
            raise ValueError(f"「{raw.get('name', '?')}」缺少可识别的 kind/类型字段")
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{kind} 条目缺少 name（名称必填）")
        name = name.strip()
        source = str(raw.get("source") or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE
        edition = str(raw.get("edition") or "").strip()
        if edition not in _EDITION_OK:
            edition = edition_of_source(source)
        eng_name = str(raw.get("ENG_name") or raw.get("eng_name") or "").strip()

        body_raw = raw.get("body")
        if isinstance(body_raw, str) and body_raw.strip():
            body = body_raw.strip()
        else:
            # 5etools 格式：正文在 entries / trait / action 等字段（怪物无 entries），
            # 一律交给 _kind_body 渲染后再判空。
            body = _kind_body(kind, raw)
        if not body:
            raise ValueError(
                f"{kind}「{name}」正文为空（请提供 body 或 entries 字段）"
            )

        entry = HomebrewEntry(
            kind=kind,
            name=name,
            source=source,
            edition=edition,
            body=body,
            eng_name=eng_name,
            is_machine=0,
            side=_extract_side_fields(kind, raw),
            tags=_parse_tags(raw.get("tags")),
            file=filename,
        )
        return entry


def _parse_tags(raw: Any) -> list[tuple[str, str]]:
    """tags 字段 → [(facet, value), ...]。

    支持形态：
    - {"施法": ["伤害", "控场"], "定位": ["奥法"]}      —— dict[facet, [values]]
    - [["施法", "伤害"], ["控场", "减速"]]              —— list[对]
    - [{"facet": "施法", "value": "伤害"}]              —— list[对象]
    """
    if raw is None:
        return []
    out: list[tuple[str, str]] = []
    if isinstance(raw, dict):
        for facet, values in raw.items():
            if not isinstance(values, list):
                continue
            for v in values:
                if isinstance(v, str) and v.strip():
                    out.append((str(facet).strip(), v.strip()))
        return out
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                f, v = item
                if isinstance(f, str) and isinstance(v, str) and f.strip() and v.strip():
                    out.append((f.strip(), v.strip()))
            elif isinstance(item, dict):
                f = item.get("facet")
                v = item.get("value")
                if isinstance(f, str) and isinstance(v, str) and f.strip() and v.strip():
                    out.append((f.strip(), v.strip()))
        return out
    return []


def _extract_side_fields(kind: str, raw: dict) -> dict[str, Any]:
    """5etools 格式条目 → 侧表字段（与构建期口径一致，字段缺失即不设）。"""
    side: dict[str, Any] = {}
    if kind == "spell":
        if raw.get("level") is not None:
            side["level"] = int(raw["level"])
        if raw.get("school"):
            side["school"] = str(raw["school"])
        comps = raw.get("components") or {}
        if isinstance(comps, dict):
            cstr = "".join(
                p for p, flag in (("V", comps.get("v")),
                                  ("S", comps.get("s")),
                                  ("M", comps.get("m"))) if flag
            )
            if cstr:
                side["components"] = cstr
        rng = raw.get("range") or {}
        dist = (rng.get("distance") or {}) if isinstance(rng, dict) else {}
        if isinstance(dist, dict) and dist.get("type") == "feet":
            side["range_feet"] = dist.get("amount")
            side["range_type"] = "feet"
        elif isinstance(dist, dict) and dist.get("type"):
            side["range_type"] = dist.get("type")
        if (raw.get("meta") or {}).get("ritual"):
            side["ritual"] = 1
        dur = raw.get("duration") or []
        if isinstance(dur, list) and dur and isinstance(dur[0], dict):
            side["concentration"] = 1 if dur[0].get("concentration") else 0
        if isinstance(raw.get("summary"), str) and raw["summary"].strip():
            side["summary"] = raw["summary"].strip()
    elif kind == "monster":
        cr = _parse_cr(raw.get("cr"))
        if cr is not None:
            side["cr"] = cr
        mtype = _monster_type(raw)
        if mtype:
            side["mtype"] = mtype
        size = raw.get("size")
        if isinstance(size, list) and size:
            side["size"] = str(size[0])
        elif size:
            side["size"] = str(size)
    elif kind == "item":
        rar = raw.get("rarity")
        if isinstance(rar, str) and rar:
            side["rarity"] = rar  # 英文原值（与 items.rarity 口径一致）
        if raw.get("reqAttune"):
            side["attunement"] = 1
        value_cp, weight_lb = _item_value_weight(raw)
        if value_cp is not None:
            side["value_cp"] = value_cp
        if weight_lb is not None:
            side["weight_lb"] = weight_lb
        combat = _item_combat_cols(raw)
        if combat:
            ac, armor_type, strength, stealth, dmg1, props, range_note = combat
            if ac is not None:
                side["ac"] = ac
            if armor_type:
                side["armor_type"] = armor_type
            if strength is not None:
                side["strength"] = strength
            if stealth is not None:
                side["stealth"] = stealth
            if dmg1:
                side["dmg1"] = dmg1
            if props:
                side["properties"] = props
            if range_note:
                side["range_note"] = range_note
    elif kind == "race":
        speed_walk, speed_climb, speed_swim, speed_fly, speed_burrow, dv = (
            _race_speed_cols(raw)
        )
        for key, val in (
            ("speed_walk", speed_walk), ("speed_climb", speed_climb),
            ("speed_swim", speed_swim), ("speed_fly", speed_fly),
            ("speed_burrow", speed_burrow), ("darkvision", dv),
        ):
            if val is not None:
                side[key] = val
        payload = _ability_payload(raw)
        if payload:
            side["ability"] = payload
    elif kind == "feat":
        cat = raw.get("category")
        if isinstance(cat, str) and cat:
            side["feat_type"] = FEAT_CATEGORY_CN.get(cat, cat)
        payload = _ability_payload(raw)
        if payload:
            side["ability_increase"] = payload
    elif kind == "background":
        payload = _ability_payload(raw)
        if payload:
            side["ability"] = payload
    # class/subclass：规则引擎侧表（class_combat 等）暂不支持私设职业，
    # 用户不填即按「无施法信息」处理（见 ARCHITECTURE 边界说明）。
    return side


# ---------------------------------------------------------------------------
# 查询层辅助（KbManager 调用；本模块内实现 overlay 侧的三级搜索/筛选）
# ---------------------------------------------------------------------------


def search_overlay(
    entries: list[HomebrewEntry],
    query: str,
    kind: str | None = None,
    limit: int = 8,
) -> list[HomebrewEntry]:
    """overlay 三级模糊搜索（与 kb.search 同语义：别名精确→LIKE→逐字缩短）。

    私设条目量小，直接内存匹配即可。
    """
    q = (query or "").strip()
    if not q:
        return []
    q_lower = q.lower()
    pool = [e for e in entries if kind is None or e.kind == kind]

    exact = [
        e for e in pool
        if e.name.lower() == q_lower or (e.eng_name and e.eng_name.lower() == q_lower)
    ]
    if exact:
        return exact[:limit]

    like = [e for e in pool if q_lower in e.name.lower() or (
        e.eng_name and q_lower in e.eng_name.lower())]
    if like:
        return like[:limit]

    short = q
    while len(short) > 1:
        short = short[:-1]
        like = [
            e for e in pool
            if short.lower() in e.name.lower() or (
                e.eng_name and short.lower() in e.eng_name.lower())
        ]
        if like:
            return like[:limit]
    return []


def filter_overlay(
    entries: list[HomebrewEntry],
    kind: str,
    *,
    level: int | None = None,
    school: str | None = None,
    cr_min: float | None = None,
    cr_max: float | None = None,
    mtype: str | None = None,
    rarity: str | None = None,
    tags: list[tuple[str, str]] | None = None,
    concentration: bool | None = None,
    range_type: str | None = None,
    range_min: int | None = None,
    range_max: int | None = None,
    attunement: bool | None = None,
    speed_type: str | None = None,
    speed_min: int | None = None,
    speed_max: int | None = None,
    darkvision_min: int | None = None,
) -> list[HomebrewEntry]:
    """overlay 结构化过滤（字段缺失即不命中该条件；tags 全匹配 AND）。"""
    out: list[HomebrewEntry] = []
    for e in entries:
        if e.kind != kind:
            continue
        s = e.side
        if kind == "spell":
            if level is not None and s.get("level") != level:
                continue
            if school and str(s.get("school") or "") != school:
                continue
            if concentration is not None and bool(s.get("concentration")) != concentration:
                continue
            if range_type and s.get("range_type") != range_type:
                continue
            rf = s.get("range_feet")
            if range_min is not None and (not isinstance(rf, (int, float)) or rf < range_min):
                continue
            if range_max is not None and (not isinstance(rf, (int, float)) or rf > range_max):
                continue
        elif kind == "monster":
            cr = s.get("cr")
            if cr_min is not None and (not isinstance(cr, (int, float)) or cr < cr_min):
                continue
            if cr_max is not None and (not isinstance(cr, (int, float)) or cr > cr_max):
                continue
            if mtype and s.get("mtype") != mtype:
                continue
        elif kind == "item":
            if rarity and s.get("rarity") != rarity:
                continue
            if attunement is not None and bool(s.get("attunement")) != attunement:
                continue
        elif kind == "race":
            if speed_type:
                col = {"walk": "speed_walk", "climb": "speed_climb",
                       "swim": "speed_swim", "fly": "speed_fly",
                       "burrow": "speed_burrow"}.get(speed_type)
                sp = s.get(col) if col else None
                if not isinstance(sp, (int, float)):
                    continue
                if speed_min is not None and sp < speed_min:
                    continue
                if speed_max is not None and sp > speed_max:
                    continue
            if darkvision_min is not None:
                dv = s.get("darkvision")
                if not isinstance(dv, (int, float)) or dv < darkvision_min:
                    continue
        if tags:
            # AND 语义：每个传入 (facet, value) 都必须在条目标签中存在
            # （同 facet 允许多值，如 战斗方式: [近战, 双持]）
            for facet, value in tags:
                if (facet, value) not in e.tags:
                    break
            else:
                out.append(e)
            continue
        out.append(e)
    return out

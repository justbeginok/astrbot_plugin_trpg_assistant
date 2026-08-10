"""homebrew_writer.py — 私设文本的校验、文件名处理、合并与原子写（v0.37.0）。

manage_homebrew 工具的确定性内核。设计约定：

- 全部同步纯函数/小 dataclass，不 import astrbot、不触 event/KV；
- **权威解析永远复用 homebrew.HomebrewManager**（临时目录试加载），
  与 /kb reload 走同一条解析链，零漂移；
- flatten_raw_entries 只做「键定位」（merge / 锚点 / 文件名派生），
  绝不做合法性判断——是否合法只信 validate_homebrew_text；
- 落盘用「临时文件 + os.replace」原子写（先例 scripts/build_kb.py）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .homebrew import (
    DEFAULT_SOURCE,
    HomebrewEntry,
    HomebrewManager,
    resolve_kind,
)

# 文件名白名单：保留 Unicode 单词字符（含中文）、数字、"_"、"-"、"."，
# 其余替换为 "_"（Python3 \w 默认匹配 CJK）。
_FILENAME_ILLEGAL = re.compile(r"[^\w.\-]")


@dataclass
class RawEntry:
    """json.loads 后拍平的原始条目（不做正文渲染，仅定位唯一键）。"""

    kind: str            # canonical kind（经 homebrew.resolve_kind 归一）
    name: str
    source: str          # 缺省补 homebrew.DEFAULT_SOURCE
    data: dict           # 原始 dict（已注入 kind/name/source 显式字段）


@dataclass
class HomebrewValidation:
    """一次文本校验结果（convert/write 共用）。"""

    ok: bool                                  # errors 为空
    entries: list[HomebrewEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)    # 文件级错误（含 JSON 语法错误）
    warnings: list[str] = field(default_factory=list)  # 条目级告警（缺 name/正文空等）
    # 与官方 (kind,name,source) 撞键的条目（写入后将覆盖官方）
    override_keys: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class WriteOutcome:
    """一次落盘结果。"""

    path: Path
    mode: str          # "created" | "overwritten" | "merged"
    entries: int       # 写入文件解析后的条目数
    warnings: list[str] = field(default_factory=list)


def validate_homebrew_text(
    text: str,
    official_keys: set[tuple[str, str, str]] | None = None,
) -> HomebrewValidation:
    """权威校验（双程校验的第二程）：临时目录 + HomebrewManager 试加载。

    HomebrewManager.load 是同步 CPU 操作、私设文本小，直接内联调用，
    不需要 asyncio.to_thread（与 /kb reload 同步调 reload_homebrew 一致）。
    """
    text = (text or "").strip()
    if not text:
        return HomebrewValidation(ok=False, errors=["JSON 文本为空"])
    # 先做一次快速语法检查：错误信息比 HomebrewManager 的文件级报错更靠前。
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return HomebrewValidation(ok=False, errors=[f"JSON 语法错误: {exc}"])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.json"
        path.write_text(text, encoding="utf-8")
        mgr = HomebrewManager(Path(tmp))
        result = mgr.load(official_keys)
        entries = mgr.entries()
    return HomebrewValidation(
        ok=result.ok,
        entries=entries,
        errors=list(result.errors),
        warnings=list(result.warnings),
        override_keys=[
            (e.kind, e.name, e.source) for e in entries if e.is_override
        ],
    )


def flatten_raw_entries(raw: Any) -> list[RawEntry]:
    """json.loads 产物 → 拍平条目列表（供 merge / 锚点 / 文件名派生）。

    口径与 HomebrewManager._parse_file 一致：
    - 顶层 list：kind 从条目内 "kind" 字段归一（无法归一 → 跳过该条）；
    - 顶层 dict：顶层键归一为 kind_default，值为 list 才展开；
    - 每条目：name 缺失/空 → 跳过；source 缺省补 DEFAULT_SOURCE；
    - 写回 data["kind"/"name"/"source"]（kind 统一为 canonical），
      保证 merge 输出每条都带显式三字段（简化格式数组）。
    """
    out: list[RawEntry] = []

    def _collect(kind_default: str | None, item: Any) -> None:
        if not isinstance(item, dict):
            return
        kind = resolve_kind(item.get("kind")) or kind_default
        if kind is None:
            return
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return
        source = str(item.get("source") or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE
        item["kind"] = kind
        item["name"] = name.strip()
        item["source"] = source
        out.append(RawEntry(kind=kind, name=name.strip(), source=source, data=item))

    if isinstance(raw, list):
        for item in raw:
            _collect(None, item)
    elif isinstance(raw, dict):
        for key, value in raw.items():
            kind_default = resolve_kind(key)
            if kind_default is None or not isinstance(value, list):
                continue
            for item in value:
                _collect(kind_default, item)
    return out


def sanitize_filename(name: str) -> str | None:
    """显式文件名安全化；非法返回 None。

    - 含路径分隔符 / ".." / 控制字符 → None；
    - 剥掉末尾的 ".json"（大小写不敏感）得到 stem，stem 过字符白名单；
    - stem 为空 → None；长度截断到 60 字符；返回 f"{stem}.json"。
    """
    name = (name or "").strip()
    if not name:
        return None
    if (
        "/" in name
        or "\\" in name
        or ".." in name
        or any(ord(c) < 32 for c in name)
    ):
        return None
    stem = re.sub(r"\.json$", "", name, flags=re.IGNORECASE)
    stem = _FILENAME_ILLEGAL.sub("_", stem).strip()
    if not stem:
        return None
    stem = stem[:60]
    return f"{stem}.json"


def derive_filename(sources: list[str]) -> str:
    """缺省文件名派生：取 sources 众数（并列取首个），
    过 sanitize_filename；结果为 None 时回退 "homebrew.json"。"""
    best: str | None = None
    best_n = 0
    counts: dict[str, int] = {}
    for s in sources:
        counts[s] = counts.get(s, 0) + 1
        if counts[s] > best_n:
            best_n = counts[s]
            best = s
    if best:
        safe = sanitize_filename(best)
        if safe:
            return safe
    return "homebrew.json"


def merge_homebrew_texts(existing_text: str, new_text: str) -> str:
    """条目级合并，返回合并后的 JSON 文本（调用前两个文本都必须已过校验）。

    以 (kind,name,source) 为键：旧条目保持原位，新条目同键→原位替换
    （新盖旧），新键→追加到末尾；新文本内部同键→后者盖前者。
    输出 = 纯数组简化格式（条目 data 原样保留 entries/trait 等 5etools
    字段，HomebrewManager 仍可解析——简化格式 body 优先于 entries）。
    """
    old = flatten_raw_entries(json.loads(existing_text))
    new = flatten_raw_entries(json.loads(new_text))
    order: list[tuple[str, str, str]] = []
    merged: dict[tuple[str, str, str], dict] = {}
    for e in old + new:
        key = (e.kind, e.name, e.source)
        if key not in merged:
            order.append(key)
        merged[key] = e.data
    return json.dumps(
        [merged[k] for k in order], ensure_ascii=False, indent=2
    )


def atomic_write_text(path: Path, text: str) -> None:
    """原子写：同目录临时文件 + os.replace（先例 scripts/build_kb.py）。

    目标目录不存在时自动创建；异常时清理临时文件并重抛。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + "_", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

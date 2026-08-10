"""money.py — 货币结算纯函数（折铜 / 找零 / 解析 / 币制显示）。

DND 标准币制：1 金币 = 10 银币 = 100 铜币。本项目约定「货币即物品条目」
（CONTEXT.md：不做钱包字段），商店结算把玩家背包里的 金币/银币/铜币 三条目
折合成铜币总额扣减，并用 greedy 大币优先策略自动找零。

本模块无任何依赖（不碰 KV、不碰异步），纯函数便于单测：
- COIN_VALUE：币种名 → 铜币面值；
- COIN_DENOMS：面值降序列表，供 make_change 使用；
- to_copper / to_money：数值与币制分量互转；
- parse_money：解析「3金5银10铜」式用户输入（纯数字视为铜币）；
- format_cp：铜币 → 「X金Y银Z铜」显示文本。

注意：背包 ItemEntry.value 字段（v0.20.0 起）统一以铜币为单位（整数值），
货币条目（金币/银币/铜币）的 value 按面值 100/10/1 写入，这样背包总价值
= 折铜后的全团财富。
"""

from __future__ import annotations

import re
from typing import Mapping

# 币种 → 铜币面值（1 金币 = 10 银币 = 100 铜币）。
COIN_VALUE: dict[str, int] = {
    "金币": 100,
    "银币": 10,
    "铜币": 1,
}

# 找零时优先使用大面值（greedy 对本币制最优）。
COIN_DENOMS: list[tuple[str, int]] = sorted(
    COIN_VALUE.items(), key=lambda kv: kv[1], reverse=True
)

# 金额总计不为 0 时缺失全部货币条目（背包里一枚钱都没有）的语义：0 铜币。
ZERO_CP = 0


def to_copper(gold: int = 0, silver: int = 0, copper: int = 0) -> int:
    """三币种分量 → 铜币总额。负分量按 0 容错。"""
    return max(gold, 0) * 100 + max(silver, 0) * 10 + max(copper, 0)


def to_money(cp: int) -> tuple[int, int, int]:
    """铜币总额 → (金币, 银币, 铜币) 分量（贪心大币优先，结果分量均 >= 0）。"""
    cp = max(int(cp), 0)
    gold, rest = divmod(cp, 100)
    silver, copper = divmod(rest, 10)
    return gold, silver, copper


def make_change(cp: int) -> list[tuple[str, int]]:
    """把铜币总额拆成「面值 → 数量」列表（仅含数量 > 0 的币种，大币在前）。

    返回示例：make_change(213) → [("金币", 2), ("银币", 1), ("铜币", 3)]。
    """
    cp = max(int(cp), 0)
    out: list[tuple[str, int]] = []
    for coin, value in COIN_DENOMS:
        if cp >= value:
            n, cp = divmod(cp, value)
            out.append((coin, n))
    return out


def format_cp(cp: int) -> str:
    """铜币总额 → 「X金Y银Z铜」显示文本（0 → 「0铜」）。

    示例：format_cp(213) → 「2金1银3铜」；format_cp(0) → 「0铜」。
    """
    gold, silver, copper = to_money(cp)
    parts: list[str] = []
    if gold:
        parts.append(f"{gold}金")
    if silver:
        parts.append(f"{silver}银")
    if copper or not parts:
        parts.append(f"{copper}铜")
    return "".join(parts)


def _try_parse_amount(token: str) -> int | None:
    """解析一个金额 token（如「3」「5金」「10银币」），失败返回 None。

    token 形如 <数字><币种后缀>：后缀支持 金/金币/银/银币/铜/铜币（简写优先完整名
    匹配）；无后缀按铜币处理。token 内不允许夹带其他字符。
    """
    token = (token or "").strip()
    if not token:
        return None
    # 纯数字 → 铜币
    if token.isdigit():
        return int(token)
    for coin, value in COIN_VALUE.items():
        if token.endswith(coin):
            num_part = token[: -len(coin)]
            if num_part.isdigit():
                return int(num_part) * value
            return None
    # 单字简写（金/银/铜）
    for coin, value in COIN_VALUE.items():
        if token.endswith(coin[0]):
            num_part = token[: -1]
            if num_part.isdigit():
                return int(num_part) * value
            return None
    return None


# 「3金5银10铜」无分隔连写的逐段匹配：数字 + 可选币种后缀（全名优先），
# 段间/段尾空白一并消费（便于「1 金 5 银」式写法；非空白残留仍会触发校验失败）。
_MONEY_TOKEN = re.compile(r"(\d+)\s*(金币|银币|铜币|金|银|铜)?\s*")


def parse_money(text: str) -> int | None:
    """解析金额输入 → 铜币总额。

    支持「3金5银10铜」「3 金币 5 银币」「50」「1金2」等写法；币种简写
    （金/银/铜）与全名（金币/银币/铜币）等价，纯数字视为铜币。文本中存在
    任何无法解析的字符（如「送人的礼物」「-5」）时返回 None，由调用方提示
    格式，避免把无关内容误当作金额。
    """
    text = (text or "").strip()
    if not text:
        return None
    total = 0
    pos = 0
    matched = False
    for m in _MONEY_TOKEN.finditer(text):
        if m.start() != pos:
            return None  # 两段之间有未消费字符
        num = int(m.group(1))
        suffix = m.group(2)
        if suffix:
            total += num * {"金币": 100, "银币": 10, "铜币": 1, "金": 100, "银": 10, "铜": 1}[suffix]
        else:
            total += num
        pos = m.end()
        matched = True
    if not matched or pos != len(text):
        return None
    return total


def inventory_copper(entries: Mapping[str, int]) -> int:
    """把「币种名 → 数量」映射折成铜币总额（背包货币条目快照）。

    仅统计 COIN_VALUE 中的币种；缺失币种视为 0。
    """
    total = 0
    for coin, value in COIN_VALUE.items():
        total += max(int(entries.get(coin, 0)), 0) * value
    return total


def settle_payment(
    coins: Mapping[str, int], total_cp: int
) -> tuple[bool, dict[str, int]]:
    """从现有币种快照中扣款 → (是否成功, 扣款后剩余币种快照)。

    策略：先尽量用整币（大币优先）凑足金额，整币凑不满时破开一枚
    面值刚好能覆盖差额的最小有货大币，多收的差额自动找零成标准币制。
    整个过程最多破开一枚大币，玩家原有币种组合尽量保留。
    货币合计不足 → (False, {})，调用方按「钱不够」处理。

    示例：settle_payment({"金币":1}, 50) → (True, {"银币":5})——
    1 金币被破开支付 50 铜，找回 5 银币。
    """
    total_cp = max(int(total_cp), 0)
    have = {c: max(int(coins.get(c, 0)), 0) for c in COIN_VALUE}
    if inventory_copper(have) < total_cp:
        return False, {}
    remaining = total_cp
    for coin, val in COIN_DENOMS:
        if remaining <= 0:
            break
        take = min(have[coin], remaining // val)
        if take:
            have[coin] -= take
            remaining -= take * val
    if remaining > 0:
        # 破开一枚面值 >= 剩余额的最小有货大币（总额已校验，必有解）。
        for coin, val in reversed(COIN_DENOMS):
            if have[coin] > 0 and val >= remaining:
                have[coin] -= 1
                remaining -= val
                break
    if remaining < 0:
        for coin, n in make_change(-remaining):
            have[coin] = have.get(coin, 0) + n
    return True, have

"""astrbot.api.logger 测试替身。

真实 AstrBot 中 `from astrbot.api import logger` 得到的是模块，
模块暴露 info/warning/error/exception 等日志函数。
本替身以标准 logging 的 "astrbot" logger 为后端，
使测试可借助 caplog 断言日志行为（与真实 AstrBot 行为一致）。
"""

from __future__ import annotations

import logging

_logger: logging.Logger = logging.getLogger("astrbot")


def info(msg: object, *args: object, **kwargs: object) -> None:
    _logger.info(msg, *args, **kwargs)


def warning(msg: object, *args: object, **kwargs: object) -> None:
    _logger.warning(msg, *args, **kwargs)


def error(msg: object, *args: object, **kwargs: object) -> None:
    _logger.error(msg, *args, **kwargs)


def exception(msg: object, *args: object, **kwargs: object) -> None:
    _logger.exception(msg, *args, **kwargs)


def debug(msg: object, *args: object, **kwargs: object) -> None:
    _logger.debug(msg, *args, **kwargs)

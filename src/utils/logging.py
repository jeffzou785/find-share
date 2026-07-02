"""项目统一 logging 配置。"""
from __future__ import annotations

import logging
import sys


def configure_logging(name: str | None = None, level: int = logging.INFO) -> logging.Logger:
    """返回已配置的 logger，默认输出纯消息到 stdout。"""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    return logging.getLogger(name)

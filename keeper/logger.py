"""日志：控制台 + 文件 + 内存环形缓冲（供网页「日志」页读取）。"""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime
from pathlib import Path

from logging.handlers import RotatingFileHandler

_RING: deque[str] = deque(maxlen=600)  # 网页可见的最近 600 行

_LOGGERS: dict[str, logging.Logger] = {}
_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"


def _formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s", "%Y-%m-%d %H:%M:%S")


class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _RING.append(self.format(record))
        except Exception:
            pass


def get_logger(name: str) -> logging.Logger:
    if name in _LOGGERS:
        return _LOGGERS[name]
    lg = logging.getLogger(f"spark.{name}")
    lg.setLevel(logging.INFO)
    if not lg.handlers:
        ch = logging.StreamHandler()
        ch.setFormatter(_formatter())
        lg.addHandler(ch)
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(_LOG_DIR / "spark-keeper.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(_formatter())
            lg.addHandler(fh)
        except Exception:
            pass  # 文件不可写不致命
        ring = _RingHandler()
        ring.setFormatter(_formatter())
        lg.addHandler(ring)
        lg.propagate = False
    _LOGGERS[name] = lg
    return lg


def recent_lines(n: int = 100) -> list[str]:
    """返回内存中最近的 n 行日志（新的在前）。"""
    return list(_RING)[-n:][::-1]


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")

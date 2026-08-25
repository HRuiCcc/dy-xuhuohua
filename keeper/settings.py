"""全局路径与环境配置。

环境变量（.env 可覆盖）：
- PORT / HOST         Web 服务监听地址
- AUTH_TOKEN          控制台访问令牌（必填，勿用默认值）
- TZ                  时区，默认 Asia/Shanghai
- DATA_DIR            数据根目录，默认 <项目根>/data
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .logger import get_logger

logger = get_logger("settings")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8020"))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "spark_secret_token_change_me")
TZ = os.environ.get("TZ", "Asia/Shanghai")
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))

# ---- 默认任务配置 ----
DEFAULTS: dict = {
    "schedule_time": "21:00",
    "jitter_minutes": 30,
    "send_gap_min": 6,
    "send_gap_max": 12,
    "max_friends_per_run": 20,
    "messages": ["🔥 续火花", "今天也要开心哦 🔥", "晚上好 🔥"],
    "auto_run_enabled": True,
    "allow_first_message": False,
    "first_message_daily_limit": 1,
}

# ---- 浏览器会话并发上限（防风控）----
MAX_CONCURRENT_BROWSERS = int(os.environ.get("MAX_BROWSERS", "5"))

# ---- 账号级配置文件名 ----
FRIENDS_FILE = "friends.json"
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"


def account_dir(account_id: str) -> Path:
    """账号数据目录（默认账号为 default）。"""
    d = DATA_DIR / "accounts" / account_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_account_config(account_id: str) -> dict:
    """读取账号任务配置，缺省字段用 DEFAULTS 兜底。"""
    path = account_dir(account_id) / CONFIG_FILE
    cfg: dict = {}
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 损坏则重建
            logger.warning("账号 %s 配置损坏，重建默认值：%s", account_id, exc)
    merged = {**DEFAULTS, **cfg}
    return merged


def save_account_config(account_id: str, cfg: dict) -> None:
    """保存账号任务配置（合并默认值后原子落盘）。"""
    from .store import atomic_write_json

    path = account_dir(account_id) / CONFIG_FILE
    merged = {**DEFAULTS, **cfg}
    atomic_write_json(path, merged)
    logger.info("账号 %s 配置已保存：%s", account_id, merged.get("schedule_time"))

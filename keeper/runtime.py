"""运行编排：全局任务锁 + 停止信号 + 三种运行模式。

模式：
- send  正式发送（对勾选好友逐个发消息）
- dry   模拟演练（走全流程但不真发）
- sync  仅同步好友列表
"""
from __future__ import annotations

import random
import threading
import time

from . import settings
from .accounts import has_login_state
from .browser import open_browser
from .douyin import check_logged_in, send_message, sync_streak_friends
from .logger import get_logger
from .store import atomic_write_json, read_json

logger = get_logger("runtime")

_LOCK = threading.Lock()
_RUNNING: dict = {"active": False, "mode": None, "account_id": None, "started_at": None}
_STOP = threading.Event()

LEDGER_FILE = "ledger.json"


def is_running() -> bool:
    return _RUNNING["active"]


def current() -> dict:
    return dict(_RUNNING)


def request_stop() -> None:
    _STOP.set()
    logger.info("收到强制停止请求")


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def first_message_allowed(account_id: str) -> bool:
    """无火花好友的「首条消息」每日限额判断。"""
    cfg = settings.load_account_config(account_id)
    if cfg.get("allow_first_message"):
        return True
    ledger = read_json(settings.account_dir(account_id) / LEDGER_FILE, default={})
    return int(ledger.get(_today(), 0)) < int(cfg.get("first_message_daily_limit", 0))


def _bump_ledger(account_id: str) -> None:
    path = settings.account_dir(account_id) / LEDGER_FILE
    ledger = read_json(path, default={})
    ledger[_today()] = int(ledger.get(_today(), 0)) + 1
    atomic_write_json(path, ledger)


def run_once(account_id: str, mode: str = "send") -> dict:
    """同步入口：拿不到锁直接返回忙。"""
    if mode not in ("send", "dry", "sync"):
        return {"ok": False, "error": f"未知模式 {mode}"}
    if not _LOCK.acquire(blocking=False):
        return {"ok": False, "error": "已有任务在运行，请先停止"}
    _RUNNING.update({"active": True, "mode": mode, "account_id": account_id, "started_at": time.time()})
    _STOP.clear()
    t = threading.Thread(target=_worker, args=(account_id, mode), daemon=True)
    t.start()
    return {"ok": True, "mode": mode}


def _worker(account_id: str, mode: str) -> None:
    try:
        logger.info("开始执行任务：账号=%s 模式=%s", account_id, {"send": "正式发送", "dry": "模拟演练", "sync": "仅同步好友"}[mode])
        if not has_login_state(account_id):
            logger.warning("账号 %s 未登录，请先扫码", account_id)
            return
        state_path = settings.account_dir(account_id) / settings.STATE_FILE
        with open_browser(state_path=state_path, headless=True) as (page, _ctx, _b, _pw):
            if not check_logged_in(page):
                logger.warning("登录态已失效，请重新扫码")
                return
            # 先同步最新好友（发送模式基于最新火花数据）
            friends = sync_streak_friends(page, account_id)
            if mode == "sync":
                return
            if _STOP.is_set():
                logger.info("任务被用户停止")
                return

            cfg = settings.load_account_config(account_id)
            targets = [f for f in friends if f.get("selected")]
            if cfg.get("max_friends_per_run"):
                targets = targets[: int(cfg["max_friends_per_run"])]
            logger.info("本次目标好友 %d 个", len(targets))

            messages = [m for m in cfg.get("messages", []) if str(m).strip()]
            if mode == "send" and not messages:
                logger.warning("未配置发送文案")
                return

            ok = skip = fail = 0
            for f in targets:
                if _STOP.is_set():
                    logger.info("任务被用户停止")
                    break
                if f.get("spark_days", 0) <= 0 and not first_message_allowed(account_id):
                    logger.info("跳过「%s」：无火花且今日首条额度已用完", f["name"])
                    skip += 1
                    continue
                if mode == "dry":
                    text = random.choice(messages) if messages else "(无文案)"
                    logger.info("[演练] 将向「%s」发送：%s", f["name"], text)
                    ok += 1
                else:
                    text = random.choice(messages)
                    if send_message(page, f["name"], text):
                        if f.get("spark_days", 0) <= 0:
                            _bump_ledger(account_id)
                        ok += 1
                    else:
                        fail += 1
                    gap = random.uniform(int(cfg.get("send_gap_min", 6)), int(cfg.get("send_gap_max", 12)))
                    logger.info("随机间隔 %.0f 秒后继续…", gap)
                    if _STOP.wait(timeout=gap):
                        logger.info("任务被用户停止")
                        break
            logger.info("任务结束：成功 %d / 失败 %d / 跳过 %d", ok, fail, skip)
    except Exception as exc:
        logger.exception("任务执行异常：%s", exc)
    finally:
        _RUNNING.update({"active": False, "mode": None, "account_id": None, "started_at": None})
        try:
            _LOCK.release()
        except Exception:
            pass

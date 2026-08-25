"""定时调度：每个账号独立的每日任务（带随机浮动），加一个每周维护任务。

设计：不用 cron 表达式。每次执行完后，为该账号计算「明天的目标时间」：
schedule_time ± jitter 随机偏移，若已过今天则顺延到明天。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from . import runtime, settings
from .accounts import list_accounts
from .logger import get_logger

logger = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None
_JOBS: dict[str, object] = {}  # account_id -> job


def _parse_time(t: str) -> tuple[int, int]:
    try:
        h, m = t.strip().split(":")
        return int(h), int(m)
    except Exception:
        return 21, 0


def next_run_at(account_id: str) -> datetime:
    """计算账号下一次执行时间（含随机浮动）。"""
    cfg = settings.load_account_config(account_id)
    h, m = _parse_time(cfg.get("schedule_time", "21:00"))
    jitter = max(0, int(cfg.get("jitter_minutes", 0)))
    base = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    offset = timedelta(minutes=random.randint(-jitter, jitter) if jitter else 0)
    target = base + offset
    if target <= datetime.now():
        target += timedelta(days=1)
    return target


def _account_job_body(account_id: str) -> None:
    if not runtime.is_running():
        runtime.run_once(account_id, mode="send")
    else:
        logger.warning("账号 %s 定时触发时已有任务在跑，跳过本轮", account_id)


def _schedule_account(account_id: str) -> None:
    when = next_run_at(account_id)
    job = _scheduler.add_job(
        _account_job_body,
        DateTrigger(run_date=when),
        args=[account_id],
        id=f"streak-{account_id}",
        replace_existing=True,
        misfire_grace_time=1800,
    )
    _JOBS[account_id] = job
    logger.info("账号 %s 已排程：下次 %s", account_id, when.strftime("%m-%d %H:%M"))


def _weekly_maintenance() -> None:
    """每周维护：清理过期扫码会话、输出健康摘要。"""
    import keeper.login as login_mod

    for sid in list(login_mod._SESSIONS.keys()):
        s = login_mod._SESSIONS.get(sid)
        if s and s.status in ("expired", "failed", "cancelled"):
            login_mod._SESSIONS.pop(sid, None)
    logger.info("每周维护完成：过期扫码会话已清理，系统正常")


def rebuild() -> None:
    """按当前账号注册表重建全部定时任务。"""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=settings.TZ)
        _scheduler.start()
        _scheduler.add_job(
            _weekly_maintenance,
            "cron",
            day_of_week="mon",
            hour=3,
            minute=0,
            id="weekly-maintenance",
            replace_existing=True,
        )
    for job_id in list(_scheduler.get_jobs()):
        if job_id.id.startswith("streak-"):
            job_id.remove()
    _JOBS.clear()
    for acc in list_accounts():
        if acc.get("enabled") and settings.load_account_config(acc["id"]).get("auto_run_enabled", True):
            _schedule_account(acc["id"])


def schedule_snapshot() -> list[dict]:
    """各账号下次执行时间（供网页展示）。"""
    out = []
    for acc in list_accounts():
        cfg = settings.load_account_config(acc["id"])
        job = _JOBS.get(acc["id"])
        out.append(
            {
                "account_id": acc["id"],
                "name": acc["name"],
                "enabled": acc.get("enabled", True) and cfg.get("auto_run_enabled", True),
                "schedule_time": cfg.get("schedule_time"),
                "jitter_minutes": cfg.get("jitter_minutes"),
                "next_run": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job and job.next_run_time else None,
            }
        )
    return out

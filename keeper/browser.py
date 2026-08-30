"""Playwright 浏览器会话池。

- 全局信号量限制同时打开的浏览器数量（防风控）
- 真实 Chrome 指纹 + playwright_stealth 注入
- 中文环境 + Asia/Shanghai 时区
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from . import settings
from .logger import get_logger

logger = get_logger("browser")

_slots = threading.Semaphore(settings.MAX_CONCURRENT_BROWSERS)

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_COMMON_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
]


def _apply_stealth(page) -> None:
    try:
        from playwright_stealth import Stealth

        Stealth().apply_stealth_sync(page)
        return
    except Exception:
        pass
    try:
        from playwright_stealth import stealth_sync

        stealth_sync(page)
    except Exception as exc:
        logger.debug("stealth 注入跳过：%s", exc)


def _new_context(browser, state_file: str | None):
    """创建浏览器上下文并注入登录态。

    关键：仅注入 cookies，不回放 storage_state 里的 localStorage。
    抖音会因 localStorage 字段与 cookies 不匹配而拒绝聊天列表接口，
    甩出 "word word" 骨架/占位页（火花天数随之消失）。仅 cookies 注入可避免。
    参考社区项目 douyin-cloud-streak 的做法。
    """
    base = dict(
        viewport={"width": 1366, "height": 768},
        user_agent=_CHROME_UA,
        locale="zh-CN",
        timezone_id=settings.TZ,
        ignore_https_errors=True,
    )
    if state_file and Path(state_file).exists():
        try:
            import json
            state = json.loads(Path(state_file).read_text(encoding="utf-8"))
            cookies = state.get("cookies") or []
            if cookies:
                ctx = browser.new_context(**base)
                ctx.add_cookies(cookies)
                return ctx
        except Exception as exc:
            logger.warning("仅注入 cookies 失败，回退 storage_state：%s", exc)
    if state_file:
        return browser.new_context(storage_state=state_file, **base)
    return browser.new_context(**base)


@contextmanager
def open_browser(state_path: Path | None = None, headless: bool = True):
    """打开一个带登录态的浏览器上下文。

    state_path: 登录态 storage_state 文件路径；不存在则开全新会话。
    yield: (page, context, browser, playwright)
    """
    from playwright.sync_api import sync_playwright

    state_file = str(state_path) if state_path and state_path.exists() else None

    acquired = _slots.acquire(timeout=120)
    if not acquired:
        raise TimeoutError("浏览器会话池已满，请稍后再试")

    pw = sync_playwright().start()
    browser = None
    try:
        browser = pw.chromium.launch(headless=headless, args=_COMMON_ARGS)
        context = _new_context(browser, state_file)
        page = context.new_page()
        _apply_stealth(page)
        yield page, context, browser, pw
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        _slots.release()

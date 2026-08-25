"""扫码登录会话管理（借鉴社区项目经验，独立实现）。

流程：queuing → starting → waiting_scan → success / expired / failed / cancelled
- 直接打开抖音聊天页（isPopup=1），登录面板直出
- 预点击序列：收面板 → 切「扫码登录」→ 点二维码容器
- 二维码提取：多选择器 + iframe 兜底 + http 转 base64 + 整页截图保底
- 过期检测 + 自动刷新（限 5 次）；登录成功判定多 Cookie 变体
- 二次刷脸验证：按尺寸启发式扫 img/canvas 取新码
- 硬超时看门狗防线程卡死
"""
from __future__ import annotations

import base64
import random
import subprocess
import threading
import time

import requests
from playwright.sync_api import sync_playwright

from . import settings
from .browser import _slots
from .logger import get_logger
from .store import atomic_write_json

logger = get_logger("login")

CHAT_URL = "https://www.douyin.com/chat?isPopup=1"
SESSION_TIMEOUT = 300
QR_REFRESH_LIMIT = 5

_LOGIN_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"}
_QR_SELECTORS = [
    "#animate_qrcode_container img",
    '[data-e2e="login-qrcode"] img',
    'div[class*="qrcode"] img',
]
_QR_EXPIRED_TEXTS = ["二维码已过期", "已失效", "已过期", "点击刷新", "刷新"]

_WEBGL_SPOOF = (
    "const _spoof=(proto)=>{const g=proto.getParameter;"
    "proto.getParameter=function(p){if(p===37445)return 'Intel Inc.';"
    "if(p===37446)return 'Intel Iris OpenGL Engine';return g.apply(this,[p]);};};"
    "if(window.WebGLRenderingContext)_spoof(WebGLRenderingContext.prototype);"
    "if(window.WebGL2RenderingContext)_spoof(WebGL2RenderingContext.prototype);"
)

_FACE_QR_JS = """
() => {
    const pick = (el) => {
        const rect = el.getBoundingClientRect();
        if (rect.width < 100 || rect.width > 350 || Math.abs(rect.width - rect.height) > 15) return null;
        const src = el.src || "";
        if (src.includes("base64,")) return src;
        try {
            const c = document.createElement("canvas");
            c.width = el.naturalWidth || rect.width;
            c.height = el.naturalHeight || rect.height;
            c.getContext("2d").drawImage(el, 0, 0, c.width, c.height);
            return c.toDataURL("image/png");
        } catch (e) { return null; }
    };
    const imgs = document.querySelectorAll("img");
    for (let i = imgs.length - 1; i >= 0; i--) {
        const r = pick(imgs[i]);
        if (r) return r;
    }
    const canvases = document.querySelectorAll("canvas");
    for (let j = canvases.length - 1; j >= 0; j--) {
        const c = canvases[j];
        const rect = c.getBoundingClientRect();
        if (rect.width >= 100 && rect.width <= 350 && Math.abs(rect.width - rect.height) <= 15) {
            try { return c.toDataURL("image/png"); } catch (e) {}
        }
    }
    return null;
}
"""


class CancelledError(Exception):
    pass


_guard = threading.Lock()
_sessions: dict[str, dict] = {}
_stop_flags: dict[str, threading.Event] = {}


def _new_state(account_id: str, **fields) -> dict:
    st = {
        "status": "starting",
        "message": "正在启动扫码环境…",
        "qrcode": "",
        "started_at": time.time(),
        "error": "",
    }
    st.update(fields)
    return st


def _public(st: dict) -> dict:
    return {
        "status": st["status"],
        "message": st["message"],
        "qrcode": st["qrcode"] if st["status"] == "waiting_scan" else "",
        "error": st["error"],
    }


def _set(aid: str, **fields) -> None:
    with _guard:
        st = _sessions.get(aid)
        if st is not None:
            st.update(fields)


def _is_stopped(aid: str) -> bool:
    flag = _stop_flags.get(aid)
    return bool(flag and flag.is_set())


def start(account_id: str) -> dict:
    """幂等启动：同账号已有活跃会话则直接返回其状态。"""
    with _guard:
        old = _sessions.get(account_id)
        if old and old["status"] in ("queuing", "starting", "waiting_scan"):
            return {"ok": True, "resumed": True, **_public(old)}
        flag = threading.Event()
        _stop_flags[account_id] = flag
        st = _new_state(account_id, status="queuing", message="正在排队获取浏览器名额…")
        _sessions[account_id] = st
    threading.Thread(target=_session_worker, args=(account_id, flag), daemon=True).start()
    watchdog = threading.Timer(SESSION_TIMEOUT + 90, lambda: _hard_expire(account_id))
    watchdog.daemon = True
    watchdog.start()
    logger.info("[%s] 扫码会话已启动", account_id)
    return {"ok": True, "resumed": False, **_public(st)}


def status(account_id: str) -> dict:
    with _guard:
        st = _sessions.get(account_id)
        if not st:
            return {"status": "idle", "message": "", "qrcode": "", "error": ""}
        return _public(st)


def cancel(account_id: str) -> dict:
    with _guard:
        st = _sessions.get(account_id)
        if not st or st["status"] in ("success", "failed", "expired", "cancelled"):
            _sessions.pop(account_id, None)
            return {"ok": True, "message": "无进行中的扫码会话"}
        flag = _stop_flags.get(account_id)
    if flag:
        flag.set()
    for _ in range(30):
        time.sleep(0.1)
        with _guard:
            cur = _sessions.get(account_id)
            if not cur or cur["status"] not in ("queuing", "starting", "waiting_scan"):
                break
    else:
        with _guard:
            cur = _sessions.get(account_id)
            if cur and cur["status"] == "waiting_scan":
                cur["status"] = "cancelled"
                cur["message"] = "已取消"
    logger.info("[%s] 扫码会话已取消", account_id)
    return {"ok": True, "message": "已取消"}


def _hard_expire(aid: str) -> None:
    flag = _stop_flags.get(aid)
    if flag:
        flag.set()
    with _guard:
        st = _sessions.get(aid)
        if st and st["status"] in ("queuing", "starting", "waiting_scan"):
            st.update(status="expired", message="扫码会话超时，请重新发起扫码", qrcode="")
            logger.warning("[%s] 扫码会话触发硬超时保护", aid)


def _launch_browser(pw):
    """Linux 上优先 Xvfb 有头内核（风控识别率低），否则无头回退。"""
    import shutil

    common = dict(
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--disable-extensions",
            "--disable-software-rasterizer",
            "--renderer-process-limit=2",
            "--no-zygote",
            "--mute-audio",
        ],
        ignore_default_args=["--enable-automation"],
    )
    if shutil.which("Xvfb"):
        for _ in range(4):
            display = f":{random.randint(90, 180)}"
            try:
                xproc = subprocess.Popen(
                    ["Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(0.8)
                if xproc.poll() is not None:
                    continue
                try:
                    import os

                    browser = pw.chromium.launch(
                        headless=False, env={**os.environ, "DISPLAY": display}, **common
                    )
                    return browser, xproc
                except Exception:
                    xproc.terminate()
            except Exception:
                continue
    return pw.chromium.launch(headless=True, **common), None


def _session_worker(aid: str, stop_flag: threading.Event) -> None:
    pw = browser = xvfb_proc = None
    try:
        if not _slots.acquire(timeout=120):
            raise RuntimeError("浏览器会话池已满，请稍后再试")
        if _is_stopped(aid):
            raise CancelledError()

        _set(aid, status="starting", message="正在打开抖音登录页…")
        pw = sync_playwright().start()
        browser, xvfb_proc = _launch_browser(pw)
        chrome_major = (browser.version or "").split(".")[0] or "124"
        context = browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id=settings.TZ,
            ignore_https_errors=True,
        )
        context.add_init_script(_WEBGL_SPOOF)
        page = context.new_page()
        try:
            from playwright_stealth import Stealth

            Stealth().apply_stealth_sync(page)
        except Exception:
            pass

        page.goto(CHAT_URL, timeout=60000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # 预点击：收面板残留 → 切「扫码登录」→ 点二维码容器
        try:
            page.locator(
                "#douyin_login_comp_flat_panel > div > div:nth-child(2) > div > div:nth-child(4) > p"
            ).click(timeout=1500)
        except Exception:
            pass
        try:
            page.get_by_text("扫码登录").first.click(timeout=1500)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        try:
            page.locator("#animate_qrcode_container").first.click(timeout=1500)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        qr_data = _wait_and_extract_qrcode(page)
        if not qr_data:
            raise RuntimeError("未能从页面提取到登录二维码，请稍后重试")
        _set(aid, status="waiting_scan", message="请使用抖音 App 扫码登录", qrcode=qr_data)

        deadline = time.time() + SESSION_TIMEOUT
        refresh_count = 0
        face_clicked = False
        polls = 0
        while time.time() < deadline:
            if _is_stopped(aid):
                raise CancelledError()
            cookies = context.cookies("https://www.douyin.com")
            if any(c.get("name") in _LOGIN_COOKIE_NAMES and c.get("value") for c in cookies):
                _save_state(context, aid)
                _set(aid, status="success", message=f"登录成功！已保存登录态（{len(cookies)} 条 Cookie）")
                logger.info("[%s] 扫码登录成功", aid)
                return

            polls += 1
            if polls % 10 == 0:
                names = ",".join(sorted({c.get("name", "") for c in cookies if c.get("name")}))
                logger.info("[%s] 等待扫码确认中，当前 Cookie：%s", aid, names or "无")

            if _qr_expired(page):
                refresh_count += 1
                if refresh_count > QR_REFRESH_LIMIT:
                    raise RuntimeError("二维码刷新次数过多，请重新发起扫码")
                logger.info("[%s] 二维码已过期，第 %s 次自动刷新", aid, refresh_count)
                _click_qr_refresh(page)
                page.wait_for_timeout(2500)
                qr_data = _wait_and_extract_qrcode(page, timeout_ms=30000)
                if qr_data:
                    _set(aid, qrcode=qr_data, message=f"二维码已自动刷新（第 {refresh_count} 次），请重新扫码")

            # 二次刷脸风控：点「手机刷脸验证」，持续提取新二维码
            if not face_clicked:
                if _js_click_first(page, ["手机刷脸验证", "刷脸验证"]):
                    face_clicked = True
                    _set(aid, message="触发安全验证：请用抖音 App 扫描新二维码并按提示完成验证")
                    logger.info("[%s] 触发二次安全验证", aid)
                    page.wait_for_timeout(3000)
            else:
                _js_click_first(page, ["已完成", "验证成功"])
                qr_face = _extract_face_qr(page)
                if qr_face:
                    _set(aid, qrcode=qr_face)

            page.wait_for_timeout(1500)

        _set(aid, status="expired", message="扫码超时，请重新发起扫码", qrcode="")
        logger.info("[%s] 扫码会话超时结束", aid)

    except CancelledError:
        _set(aid, status="cancelled", message="已取消", qrcode="")
    except Exception as exc:
        msg = str(exc)[:200]
        _set(aid, status="failed", message="扫码会话异常", error=msg, qrcode="")
        logger.warning("[%s] 扫码会话异常：%s", aid, msg)
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        if xvfb_proc:
            try:
                xvfb_proc.terminate()
            except Exception:
                pass
        try:
            _slots.release()
        except Exception:
            pass
        _stop_flags.pop(aid, None)
        threading.Timer(120, lambda: _sessions.pop(aid, None)).start()


def _js_click_first(page, texts: list[str]) -> bool:
    for t in texts:
        try:
            loc = page.get_by_text(t, exact=False)
            if loc.count():
                loc.first.evaluate("el => el.click()")
                return True
        except Exception:
            continue
    return False


def _extract_face_qr(page) -> str | None:
    try:
        data = page.evaluate(_FACE_QR_JS)
        return data if data and data.startswith("data:image") else None
    except Exception:
        return None


def _wait_and_extract_qrcode(page, timeout_ms: int = 45000) -> str | None:
    deadline = time.time() + timeout_ms / 1000
    src = ""
    while time.time() < deadline:
        for sel in _QR_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count():
                    first = loc.first
                    if first.is_visible():
                        candidate = first.get_attribute("src") or ""
                        if len(candidate) > 50:
                            src = candidate
                            break
            except Exception:
                continue
        if src:
            break
        # iframe 兜底
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            for sel in _QR_SELECTORS:
                try:
                    loc = frame.locator(sel)
                    if loc.count() and loc.first.is_visible():
                        candidate = loc.first.get_attribute("src") or ""
                        if len(candidate) > 50:
                            src = candidate
                            break
                except Exception:
                    continue
            if src:
                break
        if src:
            break
        page.wait_for_timeout(800)

    if src.startswith("data:image"):
        return src
    if src.startswith("http"):
        try:
            resp = requests.get(src, timeout=8)
            return "data:image/png;base64," + base64.b64encode(resp.content).decode()
        except Exception:
            pass
    if src:
        return f"data:image/png;base64,{src}"
    # 保底：整页截图（用户至少能看到登录框）
    for _ in range(2):
        try:
            shot = page.screenshot(timeout=8000)
            return "data:image/png;base64," + base64.b64encode(shot).decode()
        except Exception:
            try:
                page.wait_for_timeout(1500)
            except Exception:
                break
    return None


def _qr_expired(page) -> bool:
    for text in _QR_EXPIRED_TEXTS:
        try:
            loc = page.get_by_text(text, exact=False)
            if loc.count():
                for i in range(min(loc.count(), 3)):
                    if loc.nth(i).is_visible():
                        return True
        except Exception:
            continue
    return False


def _click_qr_refresh(page) -> None:
    for sel in ["#animate_qrcode_container", 'div[class*="qrcode"]', 'div[class*="refresh"]']:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=3000)
                return
        except Exception:
            continue
    try:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
    except Exception:
        pass


def _save_state(context, account_id: str) -> None:
    state_path = settings.account_dir(account_id) / settings.STATE_FILE
    raw = context.storage_state()
    if isinstance(raw, dict):
        raw.setdefault("cookies", [])
        raw.setdefault("origins", [])
    atomic_write_json(state_path, raw)
    atomic_write_json(settings.account_dir(account_id) / "state_meta.json", {"saved_at": time.time(), "account_id": account_id})
    logger.info("[%s] 登录态已保存到 %s", account_id, state_path)

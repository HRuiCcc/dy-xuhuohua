"""抖音网页版操作层：登录态检查 / 火花好友同步 / 发送消息。

所有 DOM 选择器集中在顶部常量区，抖音改版时只需改这里。
解析失败不抛异常——返回空结果并记录诊断日志，由调用方决定重试。
"""
from __future__ import annotations

import re
import time

from . import settings
from .logger import get_logger
from .store import atomic_write_json, read_json

logger = get_logger("douyin")

HOME_URL = "https://www.douyin.com/"
CHAT_URL = "https://www.douyin.com/chat?isPopup=1"
CREATOR_CHAT_API = "https://creator.douyin.com/creator-micro/data/following/chat"

# 登录成功判定 Cookie 变体（抖音各端下发名称不同）
LOGIN_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"}

# ---- 选择器常量（抖音改版改这里）----
SEL_MESSAGE_INPUT = [
    '[contenteditable="true"]',
    'textarea[placeholder*="发消息"]',
    '[class*="chat-input"] [contenteditable="true"]',
]
SEL_SEARCH_BOX = [
    'input[placeholder*="搜索"]',
    '[class*="search"] input',
]
SEL_CHAT_ITEMS = [
    '[class*="conversation"]',
    '[class*="chat-item"]',
    '[class*="session-item"]',
]

_DAY_RE = re.compile(r"(\d{1,3})\s*天")
_SPARK_RE = re.compile(r"火花|🔥")
_SPARK_DAYS_RE = re.compile(r"火花[^0-9]{0,6}(\d{1,3})")
_TIME_LINE_RE = re.compile(r"^\d{1,2}:\d{2}$")
_BARE_NUM_RE = re.compile(r"^\d{2,3}$")
_FIRE_CLS_RE = re.compile(r"spark|streak|flame|fire", re.I)

# 读屏标签打开后，抖音会给会话行补充 aria-label（如「侯，火花392天」），
# 这是最可靠的信号；关闭时天数往往只剩一个裸数字，需要配合火花图标类名判断。
_JS_ENABLE_SCREEN_READER = r"""
() => {
  const els = [...document.querySelectorAll('div,span,li,button,p,a')];
  const el = els.find(e => {
    const t = (e.innerText || '').trim();
    return (t === '读屏标签' || t.includes('开启读屏标签')) && e.offsetWidth > 0;
  });
  if (el) { try { el.click(); return true; } catch (err) { return false; } }
  return false;
}
"""

_JS_COLLECT_ROWS = r"""
() => {
  const out = [];
  const seen = new Set();
  const grab = (el) => {
    const text = el.innerText || '';
    const lines = text.split('\n').map(s => s.trim()).filter(s => s && s.length <= 40).slice(0, 8);
    const aria = (el.getAttribute('aria-label') || '') + ' ' +
      [...el.querySelectorAll('[aria-label]')].slice(0, 20).map(e => e.getAttribute('aria-label')).join(' ');
    const title = el.getAttribute('title') || '';
    const fireCls = [...el.querySelectorAll('[class*="spark" i], [class*="streak" i], [class*="flame" i], [class*="fire" i]')]
      .slice(0, 20).map(e => (typeof e.className === 'string' ? e.className : '')).join(' ');
    const key = text.slice(0, 120) + '|' + aria.slice(0, 80) + '|' + fireCls.slice(0, 80);
    if (key.length < 4 || seen.has(key)) return;
    seen.add(key);
    out.push({ lines, aria, title, cls: fireCls });
  };
  const sel = '[class*="conversation"], [class*="chat-item"], [class*="session-item"], [class*="message-item"], [class*="contact-item"]';
  for (const el of document.querySelectorAll(sel)) { if (out.length > 300) break; grab(el); }
  if (!out.length) {
    for (const el of document.querySelectorAll('li')) { if (out.length > 300) break; grab(el); }
  }
  return out;
}
"""


def parse_rows(rows: list[dict]) -> list[dict]:
    """把页面行数据解析成火花好友列表（纯函数，可单测）。

    每行信号优先级：
    1. aria/title 里的「火花 N」或「N天」
    2. 行内文本「N天」（行内任意位置有火花/🔥文字才认）
    3. 火花图标类名 + 行首裸数字（issue #1 的 Docker 页面形态）
    没有火花信号的行一律丢弃——宁可漏，不可给无关会话发消息。
    """
    found: list[dict] = []
    seen: set[str] = set()

    for row in rows:
        lines = [str(x).strip() for x in row.get("lines", []) if str(x).strip()]
        aria = row.get("aria") or ""
        title = row.get("title") or ""
        cls = row.get("cls") or ""
        hay = " ".join([aria, title, *lines])

        days = None
        m = _SPARK_DAYS_RE.search(aria + " " + title) or _DAY_RE.search(aria + " " + title)
        if m:
            days = int(m.group(1))
        else:
            has_fire_text = bool(_SPARK_RE.search(hay))
            has_fire_cls = bool(_FIRE_CLS_RE.search(cls))
            if has_fire_text:
                m = _DAY_RE.search(hay)
                if m:
                    days = int(m.group(1))
                else:
                    for line in lines:  # 「火花 392」（无天字）或裸数字
                        if _BARE_NUM_RE.match(line):
                            days = int(line)
                            break
            elif has_fire_cls:
                for line in lines:
                    if _BARE_NUM_RE.match(line):
                        days = int(line)
                        break
        if days is None:
            continue

        name = _extract_name(lines, aria)
        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        found.append({"name": name, "spark": days})
    return found


def _extract_name(lines: list[str], aria: str) -> str:
    """行内取名字：第一个像昵称的行；行首优先，跳过时间/天数/消息形态。"""
    candidates = list(lines)
    m = re.search(r"^([^，,：:]{1,20})[，,].*火花", aria)
    if m:
        candidates.insert(0, m.group(1).strip())
    for line in candidates:
        t = line.strip()
        if not t or _TIME_LINE_RE.match(t) or _BARE_NUM_RE.match(t):
            continue
        if t.startswith("[") and t.endswith("]"):
            continue
        if t.startswith(("分享", "在线", "离线")):
            continue
        if "：" in t or ":" in t:
            continue
        return t
    m = re.match(r"^([^，,：:]{1,20})", aria)
    return m.group(1).strip() if m else ""


def check_logged_in(page) -> bool:
    """当前页面是否处于登录态（多 Cookie 变体判定）。"""
    try:
        cookies = page.context.cookies()
        if any(c.get("name") in LOGIN_COOKIE_NAMES and c.get("value") for c in cookies):
            return True
    except Exception:
        pass
    try:
        # 有头像 / 无登录按钮
        if page.query_selector('img[alt*="头像"], [class*="avatar"]') and not page.query_selector(
            'button:has-text("登录")'
        ):
            return True
    except Exception:
        pass
    return False


def sync_streak_friends(page, account_id: str) -> list[dict]:
    """同步火花好友列表：网页解析为主，创作者接口兜底。"""
    found: list[dict] = []

    # 方案一：聊天页 DOM 解析（先尝试打开读屏标签，信号更全）
    try:
        page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        try:
            if page.evaluate(_JS_ENABLE_SCREEN_READER):
                logger.info("已尝试开启读屏标签，等待页面刷新…")
                page.wait_for_timeout(2000)
        except Exception as exc:
            logger.debug("读屏标签开关跳过：%s", exc)
        for _ in range(6):
            rows = page.evaluate(_JS_COLLECT_ROWS) or []
            items = parse_rows(rows)
            if items:
                found = items
                break
            page.wait_for_timeout(2500)
    except Exception as exc:
        logger.warning("聊天页解析失败：%s", exc)

    # 方案二：创作者接口兜底
    if not found:
        try:
            resp = page.request.get(CREATOR_CHAT_API, timeout=20000)
            data = resp.json()
            convs = data.get("data") or data.get("conversations") or data.get("list") or []
            for c in convs:
                name = (c.get("nickname") or c.get("name") or "").strip()
                days = c.get("streak") or c.get("streak_days") or c.get("spark") or 0
                if name:
                    found.append({"name": name, "spark": int(days or 0)})
            if found:
                logger.info("创作者接口返回 %d 个会话", len(found))
        except Exception as exc:
            logger.warning("创作者接口兜底失败：%s", exc)

    if not found:
        _dump_diagnose(page)
        logger.warning("未解析到任何火花好友，请确认登录态与抖音页面结构")

    # 与本地缓存合并（保留勾选状态，按昵称匹配）
    cache = read_json(settings.account_dir(account_id) / settings.FRIENDS_FILE, default={"friends": []})
    old = cache.get("friends", []) if isinstance(cache, dict) else []
    old_map = {f.get("name"): f for f in old}

    merged = []
    for f in found:
        name = f["name"]
        prev = old_map.get(name, {})
        merged.append(
            {
                "name": name,
                "spark_days": f.get("spark", 0),
                "selected": bool(prev.get("selected", True if f.get("spark", 0) > 0 else False)),
            }
        )
    atomic_write_json(settings.account_dir(account_id) / settings.FRIENDS_FILE, {"friends": merged, "synced_at": time.time()})
    logger.info("账号 %s 同步好友完成：%d 个（火花好友 %d）", account_id, len(merged), sum(1 for f in merged if f["spark_days"] > 0))
    return merged


def _dump_diagnose(page) -> None:
    try:
        text = page.evaluate("() => document.body ? document.body.innerText.slice(0, 800) : ''")
        logger.warning("页面诊断片段：%s", (text or "").replace("\n", " | ")[:800])
    except Exception:
        pass


def send_message(page, friend_name: str, text: str) -> bool:
    """给指定好友发一条消息。"""
    page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(2.5)

    # 搜索好友
    clicked = False
    for sel in SEL_SEARCH_BOX:
        try:
            box = page.query_selector(sel)
            if not box or not box.is_visible():
                continue
            box.click(timeout=3000)
            box.fill("")
            box.type(friend_name, delay=80)
            time.sleep(2)
            # 点击搜索结果
            for cand in page.query_selector_all(f'[class*="search-result"], [class*="user-card"], li'):
                try:
                    if friend_name in (cand.inner_text() or "") and cand.is_visible():
                        cand.click(timeout=3000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
        except Exception:
            continue
    if not clicked:
        logger.warning("未找到好友「%s」，跳过", friend_name)
        return False

    time.sleep(2)
    # 输入框
    for sel in SEL_MESSAGE_INPUT:
        try:
            inp = page.query_selector(sel)
            if inp and inp.is_visible():
                inp.click(timeout=3000)
                inp.type(text, delay=60)
                page.keyboard.press("Enter")
                logger.info("已发送 → %s：%s", friend_name, text)
                return True
        except Exception:
            continue
    logger.warning("「%s」输入框不可用，发送失败", friend_name)
    return False

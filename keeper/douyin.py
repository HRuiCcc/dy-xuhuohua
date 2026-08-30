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


def _norm_name(s) -> str:
    """昵称归一化：剔除 NBSP/零宽字符 + trim，用于精确比对。"""
    return str(s or "").replace("\u00a0", " ").replace("\u200b", "").strip()


# 火花天数字段名候选（接口层）：抖音后端字段名比前端混淆类名稳定得多，
# 私信列表的火花数字即由 im/user/info 等接口渲染，直接读接口字段最通用。
_STREAK_KEY_HINTS = ("streak", "keep_fire", "keepfire", "spark", "fire", "interact", "continuous")
_streak_logged_keys: set[str] = set()


def _probe_streak(item) -> int:
    """从 im/user/info 会话好友项中启发式探测火花天数字段（跨版本通用）。

    策略：字段名含火花语义关键词（streak/fire/spark/interact/continuous/keep），
    且值为 0~9999 的整数（或可解析字符串）即视为火花天数。
    返回 0 表示未识别或确无火花（0 同时代表无火花，语义一致）。
    """
    if not isinstance(item, dict):
        return 0
    for k, v in item.items():
        kl = str(k).lower()
        if not any(h in kl for h in _STREAK_KEY_HINTS):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and 0 <= int(v) <= 9999:
            if k not in _streak_logged_keys:
                _streak_logged_keys.add(k)
                logger.info("接口探测到火花字段 %s=%s", k, v)
            return int(v)
        if isinstance(v, str):
            m = re.search(r"\d{1,4}", v)
            if m and 0 <= int(m.group()) <= 9999:
                if k not in _streak_logged_keys:
                    _streak_logged_keys.add(k)
                    logger.info("接口探测到火花字段 %s=%r", k, v)
                return int(m.group())
    return 0

# 火花天数提取（参考社区项目 douyin-cloud-streak）。
# 关键结论：F12 确认抖音网页版火花天数渲染在专属叶子元素 commonStreaknormalText
# 内（纯数字，图标为火焰 PNG），该 class 仅出现在有火花的会话行。
# 故只锚定这一个精确叶子类，不回退到宽泛父级/整行正则，避免误抓时间、未读/群消息数。
_JS_COLLECT = r"""
() => {
  const out = [];
  const seen = new Set();
  const rows = document.querySelectorAll('[class*="conversationConversationItemwrapper"]');
  const cleanName = (el) => {
    let direct = "";
    el.childNodes.forEach(n => { if (n.nodeType === 3) direct += n.textContent; });
    let name = direct.trim();
    if (!name) {
      const clone = el.cloneNode(true);
      clone.querySelectorAll('[class*="TagNextToTitle"], [class*="timeStr"], [class*="streak"], [class*="Streak"], [class*="badge"]').forEach(x => x.remove());
      name = (clone.textContent || "").trim();
    }
    return name.replace(/\s+/g, " ").trim();
  };
  rows.forEach(row => {
    const rect = row.getBoundingClientRect();
    if (rect.height < 30 || rect.width < 100) return;
    let titleEl = row.querySelector('.conversationConversationItemtitle');
    const wrap = titleEl ? null : row.querySelector('[class*="Itemtitle"]');
    if (!titleEl && wrap) titleEl = wrap.querySelector('.conversationConversationItemtitle');
    let finalName = "";
    if (titleEl) finalName = cleanName(titleEl);
    else if (wrap) {
      const c2 = wrap.cloneNode(true);
      c2.querySelectorAll('[class*="TagNextToTitle"], [class*="timeStr"], [class*="streak"], [class*="Streak"], [class*="badge"]').forEach(x => x.remove());
      finalName = (c2.textContent || "").replace(/\s+/g, " ").trim();
    }
    if (!finalName) return;
    if (/^\d+$/.test(finalName)) return;
    if (/^\d{1,2}:\d{2}$/.test(finalName)) return;
    if (['消息','私信','朋友私信','通知'].includes(finalName)) return;
    if (finalName.length > 40) return;
    if (seen.has(finalName)) return;
    seen.add(finalName);

    // 锚定到火花专属叶子元素 commonStreaknormalText（F12 确认：火花天数=纯数字，
    // 图标为火焰 PNG）。该 class 仅出现在有火花的会话行；未读红点/群消息数使用其它
    // class，故不会冲突。只认这一个精确叶子类，不回退到任何宽泛父级，避免误抓时间/未读数。
    let spark = 0;
    const st = row.querySelector('.commonStreaknormalText, [class*="commonStreaknormalText"]');
    if (st) {
      const m = (st.textContent || "").match(/(\d{1,4})/);
      if (m) spark = parseInt(m[1], 10);
    }
    out.push({ name: finalName, spark: spark });
  });
  return out;
}
"""

# 滚动聊天列表（虚拟滚动），返回是否到底
_SCROLL_LIST_JS = r"""
(step) => {
  const cand = document.querySelector(
    '.conversationConversationListwrapper, [class*="conversationList"], [class*="chatList"], [class*="ContactList"], [class*="contactList"]'
  );
  let el = null;
  if (cand && cand.scrollHeight > cand.clientHeight) {
    el = cand;
  } else {
    const all = [...document.querySelectorAll('div')].filter(
      x => x.scrollHeight > x.clientHeight + 100 && x.clientHeight > 200 &&
           x.getBoundingClientRect().left < 400
    );
    if (all.length) el = all[0];
  }
  if (!el) return { moved: false, atBottom: true };
  const before = el.scrollTop;
  el.scrollTop = before + step;
  return {
    moved: el.scrollTop > before,
    atBottom: el.scrollTop + el.clientHeight >= el.scrollHeight - 8,
  };
}
"""


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


def _dismiss_dialogs(page) -> bool:
    """点消全屏通知/协议/提示弹窗，防止遮挡私信列表。

    只点语义明确的 harmless 文案（我知道了/知道了/稍后再说/不再提示），
    绝不盲点「确定/确认/关闭」——这些会出现在退出登录、删除会话等危险框上。
    """
    dismissed = False
    for text in ["我知道了", "知道了", "稍后再说", "不再提示"]:
        try:
            loc = page.get_by_text(text, exact=True)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=1500)
                dismissed = True
                page.wait_for_timeout(400)
        except Exception:
            pass
    for sel in [".semi-modal-close", 'button[aria-label="Close"]', 'button[aria-label="关闭"]',
                '[class*="close-icon"]', '[class*="modalClose"]', '[class*="dialog-close"]']:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=1500)
                dismissed = True
                page.wait_for_timeout(400)
        except Exception:
            pass
    return dismissed


def sync_streak_friends(page, account_id: str) -> list[dict]:
    """同步火花好友列表：DOM 解析 + im/user/info 接口双重数据源（接口兜底最稳）。

    抖音网页版私信列表的火花天数常不以文本渲染（仅火焰图标），
    但后端 im/user/info 接口会带 streak/fire 等字段，直接读接口字段最通用。
    """
    found: list[dict] = []
    api_names: list[dict] = []
    api_seen: set[str] = set()
    im_hits: list[str] = []
    _field_logged: list[bool] = []

    def _on_im_any(resp):
        """记录所有 im 相关接口响应（含状态码），确认接口路径是否被拒/变化。"""
        try:
            u = resp.url
            if ("aweme/v1/web/im" in u or "im/user/info" in u) and len(im_hits) < 30:
                im_hits.append(f"{resp.status} {u[:140]}")
        except Exception:
            pass

    def _on_api_user_info(resp):
        """拦截 im/user/info 接口，收集会话好友昵称/备注，并探测火花天数字段。"""
        try:
            if "im/user/info" not in resp.url:
                return
            if resp.status != 200:
                return
            data = resp.json()
            items = data.get("data", []) or []
            if isinstance(items, dict):
                items = items.get("user_list") or items.get("list") or items.get("users") or []
            if not isinstance(items, list):
                items = []
            # 诊断：仅首次打印第一个 user item 的所有字段名，便于校准 _probe_streak 的字段匹配
            if not _field_logged and items and isinstance(items[0], dict):
                _field_logged.append(True)
                logger.info(
                    "im/user/info 返回字段: %s",
                    sorted(k for k in items[0].keys() if not k.startswith("_")),
                )
            for item in items:
                if not isinstance(item, dict):
                    continue
                nick = _norm_name(item.get("remark_name") or item.get("nickname") or "")
                if not nick or nick in api_seen:
                    continue
                api_seen.add(nick)
                api_names.append({"name": nick, "spark": _probe_streak(item)})
        except Exception:
            pass

    # 必须在 goto 之前挂监听，才能捕获加载聊天页时触发的接口
    page.on("response", _on_im_any)
    page.on("response", _on_api_user_info)

    try:
        page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        _dismiss_dialogs(page)
        # 等真实会话列表挂载：抖音冷启动先渲染骨架屏（占位文本连续的 word），
        # 会话接口返回后才挂载真实项；固定 sleep 会在骨架屏阶段就提取导致 0 结果。
        try:
            page.wait_for_selector(
                ".conversationConversationItemtitle, [class*='conversationItem']",
                timeout=40000,
            )
        except Exception:
            pass

        seen: set[str] = set()
        for _ in range(12):
            items = page.evaluate(_JS_COLLECT) or []
            for it in items:
                n = (it.get("name") or "").strip()
                if n and n not in seen:
                    seen.add(n)
                    found.append({"name": n, "spark": int(it.get("spark", 0) or 0)})
            try:
                sc = page.evaluate(_SCROLL_LIST_JS, 700) or {}
            except Exception:
                sc = {}
            if sc.get("atBottom"):
                break
            page.wait_for_timeout(300)
        # 诊断：统计页面上 commonStreaknormalText 元素数（即真正的火花行数），
        # 应与下方“火花好友 N”接近。若该数明显大于实际火花数，说明类名被复用需收紧。
        try:
            _streak_cnt = page.evaluate(
                "() => document.querySelectorAll('.commonStreaknormalText, [class*=\"commonStreaknormalText\"]').length"
            )
            logger.info("页面 commonStreaknormalText 元素数: %s", _streak_cnt)
        except Exception:
            pass
    except Exception as exc:
        logger.warning("聊天页解析失败：%s", exc)

    # 诊断：打印 im 接口命中（确认接口路径是否被拒/变化）
    if im_hits:
        for h in im_hits[:10]:
            logger.info("IM接口命中: %s", h)
    else:
        logger.warning("未捕获到任何 im/user/info 接口响应（可能接口路径变化或被风控拦截）")

    # 接口数据补充/回填：DOM 漏掉的最新会话好友并入；DOM 有同名但火花为空的，
    # 用接口探测到的火花值回填（接口字段比前端混淆类名稳定，最通用）。
    if api_names:
        logger.info("im/user/info 接口返回 %d 个会话好友", len(api_names))
        by_name = {f["name"]: f for f in found}
        for n in api_names:
            cur = by_name.get(n["name"])
            if cur is None:
                found.append(n)
            elif not cur.get("spark") and n.get("spark"):
                cur["spark"] = n["spark"]

    # DOM 全空时（之前 0 个），直接用接口数据
    if not found and api_names:
        found = list(api_names)
        logger.info("DOM 提取为空，改用 im/user/info 接口数据 %d 条", len(found))

    if not found:
        _dump_diagnose(page)
        logger.warning("未解析到任何火花好友，请确认登录态与抖音页面结构")
    elif any(f["spark"] for f in found):
        sample = [(f["name"], f["spark"]) for f in found if f["spark"]][:5]
        logger.info("火花样本（前 5 条）: %s", sample)

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
                # 火花好友默认勾选为发送目标；非火花沿用上次选择（默认不勾选）。
                # 修复：此前用 prev 的 selected 覆盖默认值，导致旧缓存(False)把火花好友也关掉。
                "selected": bool(f.get("spark", 0) > 0) or bool(prev.get("selected", False)),
            }
        )
    atomic_write_json(settings.account_dir(account_id) / settings.FRIENDS_FILE, {"friends": merged, "synced_at": time.time()})
    logger.info("账号 %s 同步好友完成：%d 个（火花好友 %d）", account_id, len(merged), sum(1 for f in merged if f["spark_days"] > 0))
    return merged


def _dump_diagnose(page) -> None:
    """同步失败时输出更丰富的页面诊断，用于区分「页面没加载/被风控」与「选择器不匹配当前抖音结构」。

    重点回答三个问题：
    - 页面正文里到底有没有「火花/🔥」关键词？
    - 有没有「X天」这类连续天数文本？
    - 有没有异常占位（如 word word...，疑似骨架屏/风控挑战残留）？
    若命中「火花」元素，直接 dump 其 outerHTML，便于据此写出正确选择器。
    """
    try:
        probe = page.evaluate(r"""() => {
            const body = document.body ? document.body.innerText : '';
            const hasSparkWord = /火花|🔥/.test(body);
            const hasDay = /\d{1,3}\s*天/.test(body);
            const hasWord = /word\s+word/.test(body);
            const sparkEls = [];
            if (document.body) {
                const all = document.body.querySelectorAll('*');
                for (const el of all) {
                    const t = (el.innerText || '').trim();
                    if (/火花|🔥/.test(t) && t.length < 120) {
                        sparkEls.push(el.outerHTML.slice(0, 400));
                        if (sparkEls.length >= 3) break;
                    }
                }
            }
            return {
                len: body.length,
                hasSparkWord: hasSparkWord,
                hasDay: hasDay,
                hasWord: hasWord,
                sparkEls: sparkEls,
                snippet: (body || '').replace(/\s+/g, ' ').slice(0, 1500)
            };
        }""")
        logger.warning(
            "页面诊断：长度=%s 含火花词=%s 含X天=%s 含word占位=%s",
            probe.get("len"), probe.get("hasSparkWord"), probe.get("hasDay"), probe.get("hasWord"),
        )
        if probe.get("sparkEls"):
            for i, h in enumerate(probe["sparkEls"], 1):
                logger.warning("火花元素#%d 结构: %s", i, h)
        else:
            logger.warning("正文快照: %s", probe.get("snippet"))
    except Exception as exc:
        logger.warning("诊断采集失败: %s", exc)


def _open_conversation(page, friend_name: str) -> bool:
    """在私信列表里按昵称精确匹配并点开指定好友的会话。

    之前「未找到好友」的根因是发送逻辑依赖顶部搜索框（抖音搜索框文案/结果
    结构常变，旧选择器匹配不到）。改为直接复用与同步相同的会话列表选择器，
    按标题精确匹配后用 Playwright 点击最稳；列表里没有时（如很久没聊）再走
    搜索兜底。
    """
    target = _norm_name(friend_name)
    try:
        page.wait_for_selector(
            ".conversationConversationItemtitle, [class*='conversationItem']",
            timeout=40000,
        )
    except Exception:
        logger.warning("私信列表未加载，无法定位好友「%s」", friend_name)
        return False

    for _ in range(15):
        idx = page.evaluate(
            r"""(target) => {
                const rows = document.querySelectorAll('[class*="conversationConversationItemwrapper"]');
                const clean = (el) => {
                    let direct = "";
                    el.childNodes.forEach(n => { if (n.nodeType === 3) direct += n.textContent; });
                    let name = direct.trim();
                    if (!name) name = (el.textContent || "").trim();
                    return name.replace(/\s+/g, " ").trim();
                };
                for (let i = 0; i < rows.length; i++) {
                    const row = rows[i];
                    const te = row.querySelector('.conversationConversationItemtitle')
                             || row.querySelector('[class*="Itemtitle"]');
                    if (!te) continue;
                    if (clean(te) === target) return i;
                }
                return -1;
            }""",
            target,
        )
        if isinstance(idx, int) and idx >= 0:
            try:
                loc = page.locator('[class*="conversationConversationItemwrapper"]').nth(idx)
                loc.scroll_into_view_if_needed()
                loc.click(timeout=5000)
                return True
            except Exception as exc:
                logger.warning("点开好友「%s」会话失败：%s", friend_name, exc)
                return False
        sc = page.evaluate(_SCROLL_LIST_JS, 700) or {}
        if sc.get("atBottom"):
            break
        page.wait_for_timeout(300)
    return False


def _search_and_open(page, friend_name: str) -> bool:
    """兜底：用顶部搜索框查找并点开好友（会话列表里没找到时再用）。"""
    for sel in SEL_SEARCH_BOX:
        try:
            box = page.query_selector(sel)
            if not box or not box.is_visible():
                continue
            box.click(timeout=3000)
            box.fill("")
            box.type(friend_name, delay=80)
            time.sleep(2)
            for cand in page.query_selector_all('[class*="search-result"], [class*="user-card"], li'):
                try:
                    if friend_name in (cand.inner_text() or "") and cand.is_visible():
                        cand.click(timeout=3000)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def send_message(page, friend_name: str, text: str) -> bool:
    """给指定好友发一条消息。"""
    page.goto(CHAT_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(2.5)
    _dismiss_dialogs(page)

    # 1) 优先在会话列表里直接点开好友（最稳，复用同步选择器）
    if not _open_conversation(page, friend_name):
        # 2) 兜底：搜索框
        if not _search_and_open(page, friend_name):
            logger.warning("未找到好友「%s」，跳过", friend_name)
            return False

    time.sleep(2)
    # 3) 等消息输入框出现并发送
    try:
        page.wait_for_selector('[contenteditable="true"]', timeout=15000)
    except Exception:
        logger.warning("「%s」未出现消息输入框，发送失败", friend_name)
        return False
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

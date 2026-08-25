/* 火花管家 · 控制台逻辑（无依赖原生 JS） */
"use strict";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "spark_token";

let token = localStorage.getItem(TOKEN_KEY) || "";
let currentAccount = "default";
let qrPollTimer = null;

/* ---------- 通用请求 ---------- */
async function api(path, opts = {}) {
  const headers = Object.assign(
    { "Content-Type": "application/json", "X-Auth-Token": token, "X-Account": currentAccount },
    opts.headers || {}
  );
  const resp = await fetch(path, { ...opts, headers });
  if (resp.status === 401) throw new Error("令牌无效");
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败 ${resp.status}`);
  }
  return resp.json();
}

function toast(msg, isErr = false) {
  // 简单的轻提示：复用侧栏状态 pill 下方区域
  const box = $("friend-count") || document.body;
  const el = document.createElement("div");
  el.textContent = msg;
  el.style.cssText = `position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--panel);border:1px solid ${isErr ? "var(--red)" : "var(--cyan)"};color:${isErr ? "var(--red)" : "var(--text)"};padding:10px 18px;border-radius:10px;z-index:99;font-size:13px`;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/* ---------- 令牌门 ---------- */
async function tryToken() {
  try {
    const r = await api("/api/auth/check", { method: "POST", body: JSON.stringify({ token }) });
    if (r.ok) return true;
  } catch (e) { /* fallthrough */ }
  return false;
}

async function enter() {
  token = $("token-input").value.trim();
  if (!token) return;
  const ok = await tryToken();
  if (ok) {
    localStorage.setItem(TOKEN_KEY, token);
    $("token-modal").classList.add("hidden");
    $("app").classList.remove("hidden");
    await refreshAll();
  } else {
    $("token-err").textContent = "令牌无效，请检查 .env 中的 AUTH_TOKEN";
  }
}

/* ---------- 视图切换 ---------- */
const TITLES = { overview: "概览", accounts: "账号管理", cred: "登录凭证", friends: "火花好友", schedule: "定时与发送", logs: "运行日志" };
function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("view-" + name).classList.remove("hidden");
  $("view-title").textContent = TITLES[name];
  if (name === "logs") loadLogs();
  if (name === "friends") loadFriends();
  if (name === "accounts") loadAccounts();
  if (name === "cred") loadCredState();
  if (name === "schedule") loadConfig();
  if (name === "overview") loadStatus();
}

/* ---------- 状态与概览 ---------- */
async function loadStatus() {
  const s = await api("/api/status");
  currentAccount = s.account_id;
  syncAccountSelect(s.accounts);
  // 侧栏 pill
  $("side-running").textContent = s.running.active ? `运行中·${s.running.mode}` : "空闲";
  $("side-running").className = "pill " + (s.running.active ? "busy" : "idle");
  $("side-state").textContent = s.login_state ? "已登录" : "未登录";
  $("side-state").className = "pill " + (s.login_state ? "ok" : "idle");
  $("stop-btn").disabled = !s.running.active;

  const streakCount = s.accounts.length;
  $("stat-cards").innerHTML = `
    <div class="stat"><div class="v">${streakCount}</div><div class="l">账号数</div></div>
    <div class="stat"><div class="v">${s.login_state ? "✓" : "✗"}</div><div class="l">当前账号登录态</div></div>
    <div class="stat"><div class="v">${s.running.active ? "运行中" : "空闲"}</div><div class="l">任务状态</div></div>
    <div class="stat"><div class="v">${s.version}</div><div class="l">SparkKeeper 版本</div></div>`;

  const tb = $("schedule-table").querySelector("tbody");
  tb.innerHTML = s.schedule.map((x) => `
    <tr>
      <td>${x.name}</td>
      <td>${x.schedule_time}</td>
      <td>±${x.jitter_minutes} 分钟</td>
      <td>${x.next_run || "未排程"}</td>
      <td><span class="tag ${x.enabled ? "green" : "red"}">${x.enabled ? "启用" : "停用"}</span></td>
    </tr>`).join("");
}

function syncAccountSelect(accounts) {
  const sel = $("account-select");
  sel.innerHTML = accounts.map((a) => `<option value="${a.id}">${a.name}</option>`).join("");
  sel.value = currentAccount;
}

/* ---------- 账号 ---------- */
async function loadAccounts() {
  const r = await api("/api/accounts");
  syncAccountSelect(r.accounts);
  const tb = $("accounts-table").querySelector("tbody");
  tb.innerHTML = r.accounts.map((a) => `
    <tr>
      <td><b>${a.name}</b> ${a.id === "default" ? '<span class="tag">默认</span>' : ""}</td>
      <td style="font-family:ui-monospace,monospace">${a.id}</td>
      <td>${a.enabled ? '<span class="tag green">启用</span>' : '<span class="tag red">停用</span>'}</td>
      <td>
        <button class="btn" data-rename="${a.id}" data-name="${a.name}">改名</button>
        <button class="btn" data-toggle="${a.id}" data-on="${a.enabled}">${a.enabled ? "停用" : "启用"}</button>
        ${a.id !== "default" ? `<button class="btn danger" data-del="${a.id}">删除</button>` : ""}
      </td>
    </tr>`).join("");
}

/* ---------- 凭证（扫码登录） ---------- */
async function loadCredState() {
  const s = await api("/api/status");
  $("cred-state").textContent = s.login_state
    ? "✅ 当前账号已登录（凭证保存在服务器本地）"
    : "⚠️ 当前账号未登录，点击下方按钮扫码";
}

async function startQrLogin() {
  $("qr-box").classList.remove("hidden");
  $("qr-img").removeAttribute("src");
  $("qr-status").textContent = "正在打开抖音登录页…";
  $("qr-login-btn").disabled = true;
  try {
    await api("/api/login/start", { method: "POST", body: "{}" });
  } catch (e) {
    $("qr-status").textContent = "❌ 启动失败：" + e.message;
    $("qr-login-btn").disabled = false;
    return;
  }
  if (qrPollTimer) clearInterval(qrPollTimer);
  qrPollTimer = setInterval(async () => {
    try {
      const st = await api("/api/login/status");
      $("qr-status").textContent = st.message || st.status;
      if (st.qrcode && st.status === "waiting_scan") {
        if (!$("qr-img").getAttribute("src")) $("qr-img").src = st.qrcode;
      }
      if (["success", "expired", "failed", "cancelled"].includes(st.status)) {
        clearInterval(qrPollTimer);
        qrPollTimer = null;
        $("qr-login-btn").disabled = false;
        if (st.status === "success") {
          $("qr-status").textContent = "✅ " + (st.message || "登录成功！");
          loadCredState(); loadStatus();
        } else if (st.status === "failed") {
          $("qr-status").textContent = "❌ " + (st.error || st.message || "登录失败");
        }
      }
    } catch (e) { /* 轮询失败忽略 */ }
  }, 2500);
}

/* ---------- 好友 ---------- */
async function loadFriends() {
  const r = await api("/api/friends");
  const friends = r.friends || [];
  $("friend-count").textContent = `共 ${friends.length} 人 · 勾选 ${friends.filter((f) => f.selected).length} 人`;
  $("friends-list").innerHTML = friends.length ? friends.map((f) => {
    const hue = Array.from(f.name || "?").reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
    const initial = (f.name || "?")[0];
    return `
    <label class="friend-row">
      <input type="checkbox" data-name="${f.name}" ${f.selected ? "checked" : ""}>
      <span class="avatar" style="background:linear-gradient(135deg,hsl(${hue},72%,56%),hsl(${(hue + 45) % 360},74%,42%))">${initial}</span>
      <span class="fname">${f.name}</span>
      ${f.spark_days > 0 ? `<span class="days">🔥 ${f.spark_days} 天</span>` : '<span class="days">无火花</span>'}
    </label>`;
  }).join("") : '<p class="muted">还没有数据，先点「同步联系人」</p>';
}

async function saveSelection() {
  const names = Array.from(document.querySelectorAll("#friends-list input:checked")).map((i) => i.dataset.name);
  await api("/api/friends/select", { method: "POST", body: JSON.stringify({ names, selected: true }) });
  toast("勾选已保存");
  loadFriends();
}

/* ---------- 配置 ---------- */
async function loadConfig() {
  const c = await api("/api/config");
  $("cfg-time").value = c.schedule_time || "21:00";
  $("cfg-jitter").value = c.jitter_minutes;
  $("cfg-gap-min").value = c.send_gap_min;
  $("cfg-gap-max").value = c.send_gap_max;
  $("cfg-max").value = c.max_friends_per_run;
  $("cfg-first-limit").value = c.first_message_daily_limit;
  $("cfg-messages").value = (c.messages || []).join("\n");
  $("cfg-auto").checked = !!c.auto_run_enabled;
  $("cfg-allow-first").checked = !!c.allow_first_message;
}

async function saveConfig() {
  const body = {
    schedule_time: $("cfg-time").value,
    jitter_minutes: parseInt($("cfg-jitter").value, 10),
    send_gap_min: parseInt($("cfg-gap-min").value, 10),
    send_gap_max: parseInt($("cfg-gap-max").value, 10),
    max_friends_per_run: parseInt($("cfg-max").value, 10),
    first_message_daily_limit: parseInt($("cfg-first-limit").value, 10),
    messages: $("cfg-messages").value.split("\n").map((s) => s.trim()).filter(Boolean),
    auto_run_enabled: $("cfg-auto").checked,
    allow_first_message: $("cfg-allow-first").checked,
  };
  await api("/api/config", { method: "PUT", body: JSON.stringify(body) });
  toast("设置已保存，定时任务已重排");
}

/* ---------- 日志 ---------- */
async function loadLogs() {
  const r = await api("/api/logs?lines=200");
  $("logs-box").textContent = r.lines.join("\n") || "暂无日志";
}

/* ---------- 事件绑定 ---------- */
$("token-btn").onclick = enter;
$("token-input").addEventListener("keydown", (e) => { if (e.key === "Enter") enter(); });
$("account-select").onchange = (e) => { currentAccount = e.target.value; refreshAll(); };

document.querySelectorAll("#nav .nav-item").forEach((b) => (b.onclick = () => switchView(b.dataset.view)));
document.querySelectorAll("[data-run]").forEach((b) => {
  b.onclick = async () => {
    const r = await api("/api/run", { method: "POST", body: JSON.stringify({ mode: b.dataset.run }) });
    if (r.ok) toast(`已启动：${b.dataset.run === "dry" ? "模拟演练" : b.dataset.run === "sync" ? "仅同步好友" : "正式发送"}（查看日志页）`);
    else toast(r.error || "启动失败", true);
  };
});
$("stop-btn").onclick = async () => { await api("/api/stop", { method: "POST", body: "{}" }); toast("已发送停止请求"); };

$("create-account-btn").onclick = async () => {
  const name = $("new-account-name").value.trim();
  if (!name) return;
  await api("/api/accounts", { method: "POST", body: JSON.stringify({ name }) });
  $("new-account-name").value = "";
  toast("账号已创建");
  loadAccounts(); loadStatus();
};
$("accounts-table").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.dataset.rename) {
    const name = prompt("新名称：", btn.dataset.name);
    if (name) { await api(`/api/accounts/${btn.dataset.rename}/rename`, { method: "POST", body: JSON.stringify({ name }) }); loadAccounts(); }
  } else if (btn.dataset.toggle) {
    await api(`/api/accounts/${btn.dataset.toggle}/toggle`, { method: "POST", body: JSON.stringify({ enabled: btn.dataset.on === "false" }) });
    loadAccounts(); loadStatus();
  } else if (btn.dataset.del) {
    if (confirm("确认删除该账号？数据会归档保留。")) {
      await api(`/api/accounts/${btn.dataset.del}`, { method: "DELETE" });
      loadAccounts(); loadStatus();
    }
  }
});

$("qr-login-btn").onclick = startQrLogin;
$("qr-cancel-btn").onclick = async () => {
  if (qrPollTimer) { clearInterval(qrPollTimer); qrPollTimer = null; }
  try { await api("/api/login/cancel", { method: "POST", body: "{}" }); } catch (e) { /* ignore */ }
  $("qr-box").classList.add("hidden");
  $("qr-login-btn").disabled = false;
};

$("sync-btn").onclick = async () => {
  const r = await api("/api/friends/sync", { method: "POST", body: "{}" });
  if (r.ok) { toast("同步已开始，10~30 秒后点「刷新」查看"); setTimeout(loadFriends, 8000); }
  else toast(r.error || "启动失败", true);
};
$("auto-select-btn").onclick = async () => { await api("/api/friends/auto-select", { method: "POST", body: "{}" }); toast("已自动勾选火花好友"); loadFriends(); };
$("save-select-btn").onclick = saveSelection;

$("cfg-save-btn").onclick = saveConfig;
$("logs-refresh-btn").onclick = loadLogs;
$("logs-auto").onchange = (e) => {
  if (e.target.checked) window.__logTimer = setInterval(() => { if (!$("view-logs").classList.contains("hidden")) loadLogs(); }, 4000);
  else clearInterval(window.__logTimer);
};

/* ---------- 刷新调度 ---------- */
async function refreshAll() {
  try { await loadStatus(); } catch (e) { /* 未授权时忽略 */ }
}
setInterval(() => {
  if ($("app") && !$("app").classList.contains("hidden")) {
    if (!$("view-overview").classList.contains("hidden")) loadStatus().catch(() => {});
    if (!$("view-logs").classList.contains("hidden") && $("logs-auto").checked) loadLogs().catch(() => {});
  }
}, 5000);

/* ---------- 启动 ---------- */
(async function init() {
  if (token) {
    if (await tryToken()) {
      $("token-modal").classList.add("hidden");
      $("app").classList.remove("hidden");
      await refreshAll();
      return;
    }
  }
  $("token-modal").classList.remove("hidden");
})();

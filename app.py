"""火花管家 Web 服务入口（FastAPI）。

全部 /api/* 接口需要请求头 X-Auth-Token 校验；
账号上下文通过 X-Account 请求头指定（缺省 default）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from keeper import __app_name__, __version__, auth, settings
from keeper import accounts as acc_mgr
from keeper import login, logger, runtime, scheduler
from keeper.logger import get_logger

log = get_logger("api")

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.rebuild()
    log.info("%s v%s 启动完成", __app_name__, __version__)
    yield
    try:
        scheduler._scheduler.shutdown(wait=False)  # noqa: SLF001
    except Exception:
        pass


app = FastAPI(title=__app_name__, version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


# ---------- 鉴权依赖 ----------
def require_token(x_auth_token: str | None = Header(default=None), token: str | None = None) -> None:
    if not auth.check_token(x_auth_token or token):
        raise HTTPException(status_code=401, detail="令牌无效")


def require_account(x_account: str | None = Header(default=None)) -> dict:
    return acc_mgr.resolve_account(x_account)


# ---------- 请求体模型 ----------
class TokenBody(BaseModel):
    token: str


class AccountBody(BaseModel):
    name: str


class ToggleBody(BaseModel):
    enabled: bool


class SelectBody(BaseModel):
    names: list[str]
    selected: bool = True


class RunBody(BaseModel):
    mode: str = "send"


class ConfigBody(BaseModel):
    schedule_time: str | None = None
    jitter_minutes: int | None = None
    send_gap_min: int | None = None
    send_gap_max: int | None = None
    max_friends_per_run: int | None = None
    messages: list[str] | None = None
    auto_run_enabled: bool | None = None
    allow_first_message: bool | None = None
    first_message_daily_limit: int | None = None


# ---------- 页面 ----------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


# ---------- 鉴权 ----------
@app.post("/api/auth/check")
def auth_check(body: TokenBody):
    return {"ok": auth.check_token(body.token)}


# ---------- 状态 ----------
@app.get("/api/status", dependencies=[Depends(require_token)])
def status(account: dict = Depends(require_account)):
    return {
        "app": __app_name__,
        "version": __version__,
        "running": runtime.current(),
        "accounts": acc_mgr.list_accounts(),
        "schedule": scheduler.schedule_snapshot(),
        "login_state": acc_mgr.has_login_state(account["id"]),
        "account_id": account["id"],
    }


# ---------- 账号 ----------
@app.get("/api/accounts", dependencies=[Depends(require_token)])
def list_accounts():
    return {"accounts": acc_mgr.list_accounts()}


@app.post("/api/accounts", dependencies=[Depends(require_token)])
def create_account(body: AccountBody):
    return {"ok": True, "account": acc_mgr.create_account(body.name)}


@app.post("/api/accounts/{aid}/rename", dependencies=[Depends(require_token)])
def rename_account(aid: str, body: AccountBody):
    a = acc_mgr.rename_account(aid, body.name)
    if not a:
        raise HTTPException(404, "账号不存在")
    return {"ok": True, "account": a}


@app.post("/api/accounts/{aid}/toggle", dependencies=[Depends(require_token)])
def toggle_account(aid: str, body: ToggleBody):
    a = acc_mgr.toggle_account(aid, body.enabled)
    if not a:
        raise HTTPException(404, "账号不存在")
    scheduler.rebuild()
    return {"ok": True, "account": a}


@app.delete("/api/accounts/{aid}", dependencies=[Depends(require_token)])
def delete_account(aid: str):
    if not acc_mgr.delete_account(aid):
        raise HTTPException(400, "默认账号不可删除")
    scheduler.rebuild()
    return {"ok": True}


# ---------- 登录 ----------
@app.post("/api/login/start", dependencies=[Depends(require_token)])
def login_start(account: dict = Depends(require_account)):
    return login.start(account["id"])


@app.get("/api/login/status", dependencies=[Depends(require_token)])
def login_status(account: dict = Depends(require_account)):
    return login.status(account["id"])


@app.post("/api/login/cancel", dependencies=[Depends(require_token)])
def login_cancel(account: dict = Depends(require_account)):
    return login.cancel(account["id"])


# ---------- 好友 ----------
@app.get("/api/friends", dependencies=[Depends(require_token)])
def get_friends(account: dict = Depends(require_account)):
    from keeper.store import read_json

    data = read_json(settings.account_dir(account["id"]) / settings.FRIENDS_FILE, default={})
    friends = data.get("friends", []) if isinstance(data, dict) else []
    return {"friends": friends, "synced_at": data.get("synced_at") if isinstance(data, dict) else None}


@app.post("/api/friends/sync", dependencies=[Depends(require_token)])
def sync_friends(account: dict = Depends(require_account)):
    return runtime.run_once(account["id"], mode="sync")


@app.post("/api/friends/select", dependencies=[Depends(require_token)])
def select_friends(body: SelectBody, account: dict = Depends(require_account)):
    from keeper.store import atomic_write_json, read_json

    path = settings.account_dir(account["id"]) / settings.FRIENDS_FILE
    data = read_json(path, default={"friends": []})
    names = set(body.names)
    for f in data.get("friends", []):
        if f.get("name") in names:
            f["selected"] = body.selected
    atomic_write_json(path, data)
    return {"ok": True, "selected": len(names)}


@app.post("/api/friends/auto-select", dependencies=[Depends(require_token)])
def auto_select(account: dict = Depends(require_account)):
    from keeper.store import atomic_write_json, read_json

    path = settings.account_dir(account["id"]) / settings.FRIENDS_FILE
    data = read_json(path, default={"friends": []})
    n = 0
    for f in data.get("friends", []):
        if f.get("spark_days", 0) > 0 and not f.get("selected"):
            f["selected"] = True
            n += 1
    atomic_write_json(path, data)
    return {"ok": True, "newly_selected": n}


# ---------- 配置 ----------
@app.get("/api/config", dependencies=[Depends(require_token)])
def get_config(account: dict = Depends(require_account)):
    return settings.load_account_config(account["id"])


@app.put("/api/config", dependencies=[Depends(require_token)])
def put_config(body: ConfigBody, account: dict = Depends(require_account)):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    cfg = {**settings.load_account_config(account["id"]), **patch}
    settings.save_account_config(account["id"], cfg)
    scheduler.rebuild()
    return {"ok": True, "config": cfg}


# ---------- 运行控制 ----------
@app.post("/api/run", dependencies=[Depends(require_token)])
def run_now(body: RunBody, account: dict = Depends(require_account)):
    return runtime.run_once(account["id"], mode=body.mode)


@app.post("/api/stop", dependencies=[Depends(require_token)])
def stop_now():
    runtime.request_stop()
    return {"ok": True}


# ---------- 日志 ----------
@app.get("/api/logs", dependencies=[Depends(require_token)])
def get_logs(lines: int = 100):
    return {"lines": logger.recent_lines(min(max(lines, 1), 500))}


if __name__ == "__main__":
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info")

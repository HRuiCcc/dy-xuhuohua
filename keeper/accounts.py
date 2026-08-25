"""多账号注册表：每个账号独立数据目录，互不串数据。"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from . import settings
from .logger import get_logger
from .store import atomic_write_json, read_json

logger = get_logger("accounts")

_REGISTRY_FILE = settings.DATA_DIR / "accounts.json"


def _load_registry() -> dict:
    data = read_json(_REGISTRY_FILE, default={})
    if not isinstance(data, dict) or "accounts" not in data:
        data = {"accounts": [{"id": "default", "name": "默认账号", "enabled": True, "created_at": time.time()}]}
    return data


def _save_registry(reg: dict) -> None:
    atomic_write_json(_REGISTRY_FILE, reg)


def list_accounts() -> list[dict]:
    return _load_registry()["accounts"]


def get_account(account_id: str) -> dict | None:
    for a in list_accounts():
        if a["id"] == account_id:
            return a
    return None


def resolve_account(account_id: str | None) -> dict:
    """解析账号上下文：未指定或不存在时回退到 default。"""
    if account_id and get_account(account_id):
        return get_account(account_id)
    return get_account("default") or list_accounts()[0]


def create_account(name: str) -> dict:
    reg = _load_registry()
    base = re.sub(r"[^\w\u4e00-\u9fff-]", "", name.strip()) or "account"
    acc_id, i = base, 2
    while any(a["id"] == acc_id for a in reg["accounts"]):
        acc_id = f"{base}-{i}"
        i += 1
    acc = {"id": acc_id, "name": name.strip() or acc_id, "enabled": True, "created_at": time.time()}
    reg["accounts"].append(acc)
    _save_registry(reg)
    settings.account_dir(acc_id)  # 确保目录存在
    logger.info("新建账号 %s（%s）", acc["name"], acc_id)
    return acc


def rename_account(account_id: str, name: str) -> dict | None:
    reg = _load_registry()
    for a in reg["accounts"]:
        if a["id"] == account_id:
            a["name"] = name.strip() or a["name"]
            _save_registry(reg)
            return a
    return None


def toggle_account(account_id: str, enabled: bool) -> dict | None:
    reg = _load_registry()
    for a in reg["accounts"]:
        if a["id"] == account_id:
            a["enabled"] = bool(enabled)
            _save_registry(reg)
            return a
    return None


def delete_account(account_id: str) -> bool:
    if account_id == "default":
        return False  # 默认账号不可删
    reg = _load_registry()
    reg["accounts"] = [a for a in reg["accounts"] if a["id"] != account_id]
    _save_registry(reg)
    # 数据归档而不是硬删：移到 data/_archived/
    src = settings.account_dir(account_id)
    if src.exists():
        archive = settings.DATA_DIR / "_archived" / f"{account_id}-{int(time.time())}"
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(archive))
        logger.info("账号 %s 已删除，数据归档至 %s", account_id, archive)
    return True


def has_login_state(account_id: str) -> bool:
    """是否存在可用的登录态文件。"""
    p = settings.account_dir(account_id) / settings.STATE_FILE
    return p.exists() and p.stat().st_size > 200  # 过小的文件视为损坏

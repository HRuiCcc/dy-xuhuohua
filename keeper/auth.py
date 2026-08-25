"""控制台令牌鉴权。"""
from __future__ import annotations

import hmac

from . import settings


def check_token(token: str | None) -> bool:
    """常数时间比较，防时序侧信道。"""
    if not token:
        return False
    return hmac.compare_digest(str(token), settings.AUTH_TOKEN)

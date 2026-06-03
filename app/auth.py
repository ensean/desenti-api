"""Bearer Token 认证与简单内存限流。

注意：内存限流仅适用于单进程/单实例。多实例部署时应替换为
Redis 等共享存储实现。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from .config import Settings
from .errors import AuthFailedError, RateLimitedError


def extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization 头解析 Bearer Token。"""
    if not authorization:
        raise AuthFailedError("缺少 Authorization 请求头")
    parts = authorization.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthFailedError("Authorization 头格式应为 'Bearer <API_KEY>'")
    return parts[1].strip()


def verify_api_key(token: str, settings: Settings) -> str:
    if token not in settings.api_key_set:
        raise AuthFailedError("API Key 无效")
    return token


class RateLimiter:
    """滑动窗口限流器（按 API Key 计数，窗口 60 秒）。"""

    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self._window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            cutoff = now - self._window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_per_minute:
                raise RateLimitedError()
            q.append(now)

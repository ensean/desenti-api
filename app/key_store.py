"""运行时可管理的 API Key 存储（文件后端，热更新）。

- 一行一个 Key，`#` 注释与空行忽略；
- 基于 mtime 热更新：管理页面写入后，鉴权侧下次访问自动重载；
- 原子写入（临时文件 + rename），避免热更新读到半写状态。

与 settings.api_keys（env 引导 Key）的关系：
  鉴权放行集合 = env 引导 Key ∪ 本文件中的受管 Key。
  引导 Key 不可经管理页面删除；受管 Key 可增删。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path


class KeyStore:
    def __init__(self, path: str | Path = "api_keys.txt"):
        self._path = Path(path)
        self._mtime = -1.0
        self._keys: list[str] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._keys = []
            self._mtime = -1.0
            return
        mtime = self._path.stat().st_mtime
        if mtime == self._mtime:
            return
        keys: list[str] = []
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line not in keys:
                keys.append(line)
        self._keys = keys
        self._mtime = mtime

    def keys(self) -> list[str]:
        with self._lock:
            self._load()
            return list(self._keys)

    def _write(self, keys: list[str]) -> None:
        header = "# 受管 API Key，每行一个。由管理页面维护。\n"
        body = header + "\n".join(keys) + ("\n" if keys else "")
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, self._path)
        self._mtime = self._path.stat().st_mtime
        self._keys = keys

    def add(self, key: str) -> bool:
        """新增一个 Key。已存在返回 False。"""
        key = key.strip()
        if not key:
            return False
        with self._lock:
            self._load()
            if key in self._keys:
                return False
            self._write(self._keys + [key])
            return True

    def remove(self, key: str) -> bool:
        """删除一个受管 Key。不存在返回 False。"""
        with self._lock:
            self._load()
            if key not in self._keys:
                return False
            self._write([k for k in self._keys if k != key])
            return True


_instance: KeyStore | None = None
_lock = threading.Lock()


def get_key_store(path: str | Path = "api_keys.txt") -> KeyStore:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = KeyStore(path)
    return _instance

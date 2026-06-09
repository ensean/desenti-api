"""管理页面与后端接口：维护敏感词典与 API Key。

安全要点：
  - 全部管理接口需 `X-Admin-Token` 头，与 DESENTI_ADMIN_TOKEN 常量时间比较；
  - 未设置 DESENTI_ADMIN_TOKEN 时，整个 /admin 直接禁用（404）；
  - 管理操作可增删 Key、改检测规则，属高权限，切勿把 /admin 暴露给公网，
    建议在 CloudFront/网关层限制路径或来源（见 deploy/README）。
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings, get_settings
from .key_store import get_key_store

router = APIRouter(prefix="/admin", tags=["admin"])

_STATIC_DIR = Path(__file__).parent / "static"
_MAX_DICT_BYTES = 1024 * 1024  # 词典文件上限 1MB


def require_admin(
    x_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> Settings:
    # 未配置管理令牌 -> 管理功能整体禁用
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="admin disabled")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=401, detail="invalid admin token")
    return settings


def _mask(key: str) -> str:
    if len(key) <= 8:
        return key[:2] + "…"
    return f"{key[:4]}…{key[-4:]}"


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_page(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    # 页面本身公开可见（仅静态 HTML，不含数据）；所有数据接口仍需令牌。
    if not settings.admin_token:
        raise HTTPException(status_code=404, detail="admin disabled")
    html = (_STATIC_DIR / "admin.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# 敏感词典
# ---------------------------------------------------------------------------

@router.get("/api/dict")
async def get_dict_content(settings: Settings = Depends(require_admin)) -> JSONResponse:
    path = Path(settings.dict_file)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return JSONResponse({"content": content, "path": str(path)})


@router.put("/api/dict")
async def save_dict_content(
    settings: Settings = Depends(require_admin),
    content: str = Body(..., embed=True),
) -> JSONResponse:
    if len(content.encode("utf-8")) > _MAX_DICT_BYTES:
        raise HTTPException(status_code=400, detail="词典内容超过 1MB 限制")
    path = Path(settings.dict_file)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    import os
    os.replace(tmp, path)
    # dict_engine 基于 mtime 热更新，写盘后下次识别自动生效
    return JSONResponse({"ok": True, "bytes": len(content.encode("utf-8"))})


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------

@router.get("/api/keys")
async def list_keys(settings: Settings = Depends(require_admin)) -> JSONResponse:
    bootstrap = sorted(settings.api_key_set)
    managed = get_key_store(settings.api_keys_file).keys()
    items = [{"masked": _mask(k), "source": "bootstrap", "removable": False} for k in bootstrap]
    items += [{"masked": _mask(k), "value": k, "source": "managed", "removable": True}
              for k in managed]
    return JSONResponse({"keys": items})


@router.post("/api/keys")
async def add_key(
    settings: Settings = Depends(require_admin),
    prefix: str = Body(default="key", embed=True),
) -> JSONResponse:
    # 生成强随机 Key（前缀便于日志识别来源）
    safe_prefix = "".join(c for c in prefix if c.isalnum() or c in "-_")[:24] or "key"
    new_key = f"{safe_prefix}-{secrets.token_hex(20)}"
    store = get_key_store(settings.api_keys_file)
    if not store.add(new_key):
        raise HTTPException(status_code=409, detail="key already exists")
    # 仅此一次返回完整 Key，请立即保存
    return JSONResponse({"ok": True, "key": new_key})


@router.delete("/api/keys")
async def delete_key(
    settings: Settings = Depends(require_admin),
    key: str = Query(...),
) -> JSONResponse:
    if key in settings.api_key_set:
        raise HTTPException(status_code=400, detail="引导 Key（env）不可经管理页面删除")
    store = get_key_store(settings.api_keys_file)
    if not store.remove(key):
        raise HTTPException(status_code=404, detail="key not found")
    return JSONResponse({"ok": True})

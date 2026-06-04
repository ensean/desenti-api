"""API 集成测试。"""

from __future__ import annotations

import os

# 测试用 API Key
os.environ["DESENTI_API_KEYS"] = "test-key,other-key"

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402

get_settings.cache_clear()
client = TestClient(app, raise_server_exceptions=False)

AUTH = {"Authorization": "Bearer test-key"}


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ner_success():
    body = {"text": "甲方：深圳市星幻科技有限公司\n法定代表人：张三\n电话：13800138000"}
    r = client.post("/api/v1/contract/ner", json=body, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["data"]["statistics"]["total_entities"] >= 1
    assert "model_version" in data["meta"]
    assert "processing_time_ms" in data["meta"]


def test_auth_missing():
    r = client.post("/api/v1/contract/ner", json={"text": "abc"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_FAILED"


def test_auth_invalid_key():
    r = client.post(
        "/api/v1/contract/ner",
        json={"text": "abc"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_FAILED"


def test_empty_text():
    r = client.post("/api/v1/contract/ner", json={"text": "   "}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "EMPTY_TEXT"


def test_text_too_long():
    big = "甲" * (100 * 1024 + 1)
    r = client.post("/api/v1/contract/ner", json={"text": big}, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "TEXT_TOO_LONG"


def test_invalid_options_min_confidence():
    body = {"text": "abc", "options": {"min_confidence": 2.0}}
    r = client.post("/api/v1/contract/ner", json=body, headers=AUTH)
    # pydantic 校验失败 -> 422；属于参数错误范畴
    assert r.status_code in (400, 422)


def test_entity_type_filter_api():
    body = {
        "text": "法定代表人：张三 电话：13800138000",
        "options": {"entity_types": ["phone"]},
    }
    r = client.post("/api/v1/contract/ner", json=body, headers=AUTH)
    assert r.status_code == 200
    types = {e["type"] for e in r.json()["data"]["entities"]}
    assert types <= {"phone"}


def test_cors_preflight_options():
    # 浏览器预检：OPTIONS 应被 CORS 中间件处理，返回允许头
    r = client.options(
        "/api/v1/contract/ner",
        headers={
            "Origin": "https://quick.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "https://quick.example.com")
    allow_methods = r.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods


def test_cors_actual_request_has_headers():
    # 实际请求带 Origin 时，响应应含 Access-Control-Allow-Origin
    body = {"text": "电话13800138000", "options": {"mode": "fast"}}
    r = client.post(
        "/api/v1/contract/ner",
        json=body,
        headers={**AUTH, "Origin": "https://quick.example.com"},
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in ("*", "https://quick.example.com")

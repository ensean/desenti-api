"""管理页面后端接口测试。

用 dependency_overrides 注入固定 Settings + 重置单例，
使本模块不受其他测试模块的 env/单例顺序影响，且测试后清理不外泄。
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import app.key_store as ks
import app.main as m
import app.ner.dict_engine as de
from app.config import Settings, get_settings
from app.main import app

_TMP = tempfile.mkdtemp()
KEYS_FILE = os.path.join(_TMP, "api_keys.txt")
DICT_FILE = os.path.join(_TMP, "sensitive_dict.txt")

with open(DICT_FILE, "w", encoding="utf-8") as f:
    f.write("[company_name]\n初始公司\n")

TEST_SETTINGS = Settings(
    admin_token="admin-secret",
    api_keys="bootstrap-key",
    api_keys_file=KEYS_FILE,
    dict_file=DICT_FILE,
    spacy_enabled=False,
)

client = TestClient(app, raise_server_exceptions=False)
ADMIN = {"X-Admin-Token": "admin-secret"}


@pytest.fixture(autouse=True)
def _ctx():
    app.dependency_overrides[get_settings] = lambda: TEST_SETTINGS
    ks._instance = ks.KeyStore(KEYS_FILE)
    orig_dict = m._engine.sensitive_dict
    orig_spacy = m._engine.spacy_backend
    m._engine.sensitive_dict = de.SensitiveDict(DICT_FILE)
    m._engine.spacy_backend = None
    yield
    app.dependency_overrides.pop(get_settings, None)
    m._engine.sensitive_dict = orig_dict
    m._engine.spacy_backend = orig_spacy


def test_admin_page_served():
    r = client.get("/admin")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_requires_token():
    assert client.get("/admin/api/keys").status_code == 401
    assert client.get("/admin/api/keys", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_list_keys_bootstrap_masked():
    r = client.get("/admin/api/keys", headers=ADMIN)
    assert r.status_code == 200
    keys = r.json()["keys"]
    boot = [k for k in keys if k["source"] == "bootstrap"]
    assert boot and boot[0]["removable"] is False
    assert "bootstrap-key" not in str(boot[0])  # 不明文回显引导 Key


def test_add_use_delete_key():
    r = client.post("/admin/api/keys", json={"prefix": "quick"}, headers=ADMIN)
    assert r.status_code == 200
    new_key = r.json()["key"]
    assert new_key.startswith("quick-")

    # 新 Key 立即可用于业务接口
    r2 = client.post(
        "/api/v1/contract/ner",
        json={"text": "电话13800138000", "options": {"mode": "fast"}},
        headers={"Authorization": f"Bearer {new_key}"},
    )
    assert r2.status_code == 200

    # 删除后失效
    assert client.delete(f"/admin/api/keys?key={new_key}", headers=ADMIN).status_code == 200
    r3 = client.post(
        "/api/v1/contract/ner",
        json={"text": "x", "options": {"mode": "fast"}},
        headers={"Authorization": f"Bearer {new_key}"},
    )
    assert r3.status_code == 401


def test_cannot_delete_bootstrap_key():
    r = client.delete("/admin/api/keys?key=bootstrap-key", headers=ADMIN)
    assert r.status_code == 400


def test_dict_get_save_hot_reload():
    r = client.get("/admin/api/dict", headers=ADMIN)
    assert r.status_code == 200
    assert "初始公司" in r.json()["content"]

    r2 = client.put(
        "/admin/api/dict",
        json={"content": "[company_name]\n神秘量子研究所\n"},
        headers=ADMIN,
    )
    assert r2.status_code == 200

    # 热更新后业务接口应命中新词条
    r3 = client.post(
        "/api/v1/contract/ner",
        json={"text": "本协议由神秘量子研究所提供。", "options": {"mode": "fast"}},
        headers={"Authorization": "Bearer bootstrap-key"},
    )
    ents = r3.json()["data"]["entities"]
    assert any(e["value"] == "神秘量子研究所" and e["type"] == "company_name" for e in ents)

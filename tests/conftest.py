import os
from pathlib import Path
import pytest

from qoder2oapi.config import settings
from qoder2oapi.runtime_settings import RuntimeSettings
from qoder2oapi.token_store import TokenStore


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir()
    monkeypatch.setattr(settings, "qoder2oapi_data_dir", str(test_data_dir))
    monkeypatch.setattr(settings, "qoder2oapi_api_key", "test-secret-key-1234")

    new_settings = RuntimeSettings(data_dir=test_data_dir)
    new_store = TokenStore(data_dir=test_data_dir)

    from qoder2oapi import runtime_settings as rs_module
    monkeypatch.setattr(rs_module, "runtime_settings", new_settings)
    from qoder2oapi import pool as pool_module
    monkeypatch.setattr(pool_module, "runtime_settings", new_settings)
    monkeypatch.setattr(pool_module, "token_store", new_store)
    from qoder2oapi import refresh as refresh_module
    monkeypatch.setattr(refresh_module, "runtime_settings", new_settings)
    monkeypatch.setattr(refresh_module, "token_store", new_store)
    from qoder2oapi import quota as quota_module
    monkeypatch.setattr(quota_module, "token_store", new_store)
    from qoder2oapi import checkin as checkin_module
    monkeypatch.setattr(checkin_module, "runtime_settings", new_settings)
    monkeypatch.setattr(checkin_module, "token_store", new_store)
    monkeypatch.setattr(checkin_module, "pool", pool_module.pool)
    from qoder2oapi.routes import admin as admin_routes
    monkeypatch.setattr(admin_routes, "runtime_settings", new_settings)
    monkeypatch.setattr(admin_routes, "token_store", new_store)

    from qoder2oapi import token_store as ts_module
    monkeypatch.setattr(ts_module, "token_store", new_store)
    from qoder2oapi import oauth
    monkeypatch.setattr(oauth, "token_store", new_store)
    from qoder2oapi.routes import openai as openai_routes
    monkeypatch.setattr(openai_routes, "token_store", new_store)
    monkeypatch.setattr(quota_module, "token_store", new_store)
    from qoder2oapi import catalog as catalog_module
    monkeypatch.setattr(catalog_module, "token_store", new_store)
    from qoder2oapi import infer as infer_module
    monkeypatch.setattr(infer_module, "token_store", new_store)
    monkeypatch.setattr(pool_module, "token_store", new_store)
    monkeypatch.setattr(refresh_module, "token_store", new_store)
    from qoder2oapi import app as app_module
    monkeypatch.setattr(app_module, "token_store", new_store)
    try:
        import tests.test_checkin as tc_module
        monkeypatch.setattr(tc_module, "token_store", new_store)
        monkeypatch.setattr(tc_module, "runtime_settings", new_settings)
        monkeypatch.setattr(tc_module, "pool", pool_module.pool)
    except Exception:
        pass
    yield

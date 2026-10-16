import asyncio
from pathlib import Path
import time
import httpx
import pytest

from qoder2oapi.metrics import MetricsManager, SNAPSHOT_LAST_FETCHED_AT


@pytest.fixture
def temp_metrics(tmp_path: Path):
    db_file = tmp_path / "test_metrics.db"
    return MetricsManager(db_path=db_file)


def test_db_initialization_and_snapshot_pricing(temp_metrics: MetricsManager):
    pricing_list = temp_metrics.get_model_pricing_list()
    assert len(pricing_list) >= 14
    keys = {p["model_key"] for p in pricing_list}
    expected_keys = {
        "dfmodel",
        "dmodel",
        "qmodel_38max",
        "qmodel_latest",
        "qmodel",
        "qfmodel",
        "q37fmodel",
        "gmodel",
        "gfmodel",
        "gm51model",
        "kmodel_latest",
        "kmodel",
        "mmodel",
        "auto",
        "lite",
    }
    assert expected_keys.issubset(keys)
    for p in pricing_list:
        assert p["last_fetched_at"] == SNAPSHOT_LAST_FETCHED_AT


def test_calculate_official_cost(temp_metrics: MetricsManager):
    # deepseek-v4.1-flash (dfmodel): prompt 1.0, cached 0.2, completion 2.0 per 1M tokens
    # Prompt: 1000 total tokens (800 non-cached, 200 cached), completion: 500
    # Cost = (800 * 1.0 + 200 * 0.2 + 500 * 2.0) / 1,000,000
    # = (800 + 40 + 1000) / 1,000,000 = 1840 / 1,000,000 = 0.00184 RMB
    cost = temp_metrics.calculate_official_cost(
        "deepseek-v4.1-flash",
        prompt_tokens=1000,
        cached_tokens=200,
        completion_tokens=500,
    )
    assert cost == 0.00184

    # Test alias and prefix resolution: "cn/deepseek-v4.1-flash"
    cost_prefixed = temp_metrics.calculate_official_cost(
        "cn/deepseek-v4.1-flash",
        prompt_tokens=1000,
        cached_tokens=200,
        completion_tokens=500,
    )
    assert cost_prefixed == cost

    # Qwen3.8-Flash non-zero official pricing test
    # prompt: 0.5/1M, cached: 0.1/1M, completion: 1.5/1M
    cost_qwen_flash = temp_metrics.calculate_official_cost(
        "qwen3.8-flash",
        prompt_tokens=5000,
        cached_tokens=1000,
        completion_tokens=5000,
    )
    # (4000 * 0.5 + 1000 * 0.1 + 5000 * 1.5) / 1,000,000 = (2000 + 100 + 7500) / 1,000,000 = 0.0096 RMB
    assert abs(cost_qwen_flash - 0.0096) < 1e-6

    # Free model test: lite remains 0.0
    cost_free = temp_metrics.calculate_official_cost(
        "lite",
        prompt_tokens=5000,
        completion_tokens=5000,
    )
    assert cost_free == 0.0


def test_record_request_and_overview(temp_metrics: MetricsManager):
    overview_empty = temp_metrics.get_metrics_overview()
    assert overview_empty["total_requests"] == 0
    assert overview_empty["avg_ttft"] == 0.0
    assert overview_empty["avg_tps"] == 0.0

    # Insert request 1
    req1_id = temp_metrics.record_request(
        account_id="acc_1",
        model="deepseek-v4.1-flash",
        status_code=200,
        ttft_ms=150.0,
        total_duration_ms=1000.0,
        tps=25.0,
        prompt_tokens=1000,
        cached_tokens=200,
        completion_tokens=500,
        credits_deducted=1.0,
    )
    assert req1_id == 1

    # Insert request 2
    req2_id = temp_metrics.record_request(
        account_id="acc_2",
        model="deepseek-v4-pro",
        status_code=200,
        ttft_ms=250.0,
        total_duration_ms=2000.0,
        tps=15.0,
        prompt_tokens=2000,
        cached_tokens=0,
        completion_tokens=1000,
        credits_deducted=5.0,
    )
    assert req2_id == 2

    overview = temp_metrics.get_metrics_overview()
    assert overview["total_requests"] == 2
    assert overview["avg_ttft"] == 200.0  # (150 + 250) / 2
    assert overview["avg_tps"] == 20.0  # (25 + 15) / 2
    assert overview["prompt_tokens"] == 3000
    assert overview["cached_tokens"] == 200
    assert overview["completion_tokens"] == 1500
    assert overview["cache_hit_rate"] == round(200 / 3000, 4)
    assert overview["total_credits"] == 6.0
    assert overview["total_official_cost"] > 0
    assert overview["total_saved"] == overview["total_official_cost"]


def test_get_metrics_by_model(temp_metrics: MetricsManager):
    temp_metrics.record_request(
        account_id="acc_1",
        model="qwen3.8-max",
        status_code=200,
        ttft_ms=300.0,
        tps=30.0,
        prompt_tokens=100,
        completion_tokens=50,
    )
    temp_metrics.record_request(
        account_id="acc_1",
        model="qwen3.8-max",
        status_code=200,
        ttft_ms=200.0,
        tps=20.0,
        prompt_tokens=200,
        completion_tokens=100,
    )
    temp_metrics.record_request(
        account_id="acc_2",
        model="lite",
        status_code=200,
        ttft_ms=100.0,
        tps=50.0,
        prompt_tokens=50,
        completion_tokens=50,
    )

    by_model = temp_metrics.get_metrics_by_model()
    assert len(by_model) == 2
    qwen_stat = next(m for m in by_model if m["model"] == "qwen3.8-max")
    assert qwen_stat["request_count"] == 2
    assert qwen_stat["avg_ttft"] == 250.0
    assert qwen_stat["avg_tps"] == 25.0
    assert qwen_stat["prompt_tokens"] == 300
    assert qwen_stat["completion_tokens"] == 150

    lite_stat = next(m for m in by_model if m["model"] == "lite")
    assert lite_stat["request_count"] == 1
    assert lite_stat["avg_ttft"] == 100.0


def test_get_hourly_trends(temp_metrics: MetricsManager):
    now_ms = int(time.time() * 1000)
    # Record one request now
    temp_metrics.record_request(
        model="auto",
        status_code=200,
        timestamp=now_ms,
        ttft_ms=100.0,
        tps=10.0,
        prompt_tokens=100,
        completion_tokens=50,
    )
    # Record one request 2 hours ago
    temp_metrics.record_request(
        model="auto",
        status_code=200,
        timestamp=now_ms - 2 * 3600 * 1000,
        ttft_ms=200.0,
        tps=20.0,
        prompt_tokens=200,
        completion_tokens=100,
    )
    # Record one request 30 hours ago (should be excluded when hours=24)
    temp_metrics.record_request(
        model="auto",
        status_code=200,
        timestamp=now_ms - 30 * 3600 * 1000,
        ttft_ms=300.0,
        tps=30.0,
    )

    trends_24h = temp_metrics.get_hourly_trends(hours=24)
    assert len(trends_24h) == 2
    total_reqs = sum(t["request_count"] for t in trends_24h)
    assert total_reqs == 2

    trends_48h = temp_metrics.get_hourly_trends(hours=48)
    assert len(trends_48h) == 3


@pytest.mark.asyncio
async def test_async_and_concurrency(temp_metrics: MetricsManager):
    async def worker(idx: int):
        return await temp_metrics.arecord_request(
            model="glm-5.3",
            status_code=200,
            prompt_tokens=10 * idx,
            completion_tokens=5 * idx,
            ttft_ms=100.0 + idx,
            tps=10.0 + idx,
        )

    tasks = [worker(i) for i in range(1, 21)]
    results = await asyncio.gather(*tasks)
    assert len(results) == 20

    overview = temp_metrics.get_metrics_overview()
    assert overview["total_requests"] == 20


@pytest.mark.asyncio
async def test_admin_metrics_endpoints(monkeypatch, tmp_path: Path):
    from qoder2oapi.app import create_app
    import qoder2oapi.metrics as metrics_mod
    from qoder2oapi.config import settings

    # Point metrics_manager to temporary db
    mock_mgr = MetricsManager(db_path=tmp_path / "mock_metrics.db")
    monkeypatch.setattr(metrics_mod, "metrics_manager", mock_mgr)
    import qoder2oapi.routes.admin as admin_routes
    monkeypatch.setattr(admin_routes, "metrics_manager", mock_mgr)

    mock_mgr.record_request(
        model="deepseek-v4.1-flash",
        status_code=200,
        prompt_tokens=1000,
        cached_tokens=500,
        completion_tokens=200,
        ttft_ms=150.0,
        tps=40.0,
        credits_deducted=0.1,
    )

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        auth_header = {"Authorization": f"Bearer {settings.qoder2oapi_api_key}"}

        # 1. Overview
        resp = await client.get("/api/admin/metrics/overview", headers=auth_header)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 1
        assert data["prompt_tokens"] == 1000
        assert data["cached_tokens"] == 500
        assert data["cache_hit_rate"] == 0.5
        assert data["total_official_cost"] > 0

        # 2. Models
        resp = await client.get("/api/admin/metrics/models", headers=auth_header)
        assert resp.status_code == 200
        models_data = resp.json()
        assert len(models_data) == 1
        assert models_data[0]["model"] == "deepseek-v4.1-flash"

        # 3. Trends
        resp = await client.get("/api/admin/metrics/trends?hours=24", headers=auth_header)
        assert resp.status_code == 200
        trends_data = resp.json()
        assert len(trends_data) == 1

        # 4. Pricing
        resp = await client.get("/api/admin/metrics/pricing", headers=auth_header)
        assert resp.status_code == 200
        pricing_data = resp.json()
        assert any(p["model_key"] == "dfmodel" for p in pricing_data)

        # 5. Stats page HTML
        resp = await client.get("/stats")
        assert resp.status_code == 200
        assert "Qoder2OAPI 请求统计" in resp.text
        assert "stats.js" in resp.text

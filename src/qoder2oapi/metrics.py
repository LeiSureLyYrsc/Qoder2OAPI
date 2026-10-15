from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Any

from qoder2oapi.config import data_dir_path
from qoder2oapi.names import alias_to_internal_key, strip_model_prefix

SNAPSHOT_LAST_FETCHED_AT = "2026-10-15T20:00:00+08:00"

# Official pricing per million tokens in RMB (prompt_rate, cached_rate, completion_rate)
# Rates represent RMB / 1M tokens.
MODEL_PRICING_SNAPSHOT: list[dict[str, Any]] = [
    {
        "model_key": "dfmodel",
        "model_name": "deepseek-v4.1-flash",
        "prompt_rate": 1.0,
        "cached_rate": 0.2,
        "completion_rate": 2.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "dmodel",
        "model_name": "deepseek-v4-pro",
        "prompt_rate": 4.0,
        "cached_rate": 1.0,
        "completion_rate": 16.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "qmodel_38max",
        "model_name": "qwen3.8-max",
        "prompt_rate": 20.0,
        "cached_rate": 5.0,
        "completion_rate": 60.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "qmodel_latest",
        "model_name": "qwen3.7-max",
        "prompt_rate": 20.0,
        "cached_rate": 5.0,
        "completion_rate": 60.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "qmodel",
        "model_name": "qwen3.7-plus",
        "prompt_rate": 0.8,
        "cached_rate": 0.2,
        "completion_rate": 2.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "qfmodel",
        "model_name": "qwen3.8-flash",
        "prompt_rate": 0.0,
        "cached_rate": 0.0,
        "completion_rate": 0.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "q37fmodel",
        "model_name": "qwen3.7-flash",
        "prompt_rate": 0.0,
        "cached_rate": 0.0,
        "completion_rate": 0.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "gmodel",
        "model_name": "glm-5.3",
        "prompt_rate": 4.0,
        "cached_rate": 1.0,
        "completion_rate": 16.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "gfmodel",
        "model_name": "glm-5.3-flash",
        "prompt_rate": 0.0,
        "cached_rate": 0.0,
        "completion_rate": 0.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "gm51model",
        "model_name": "glm-5.2",
        "prompt_rate": 4.0,
        "cached_rate": 1.0,
        "completion_rate": 16.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "kmodel_latest",
        "model_name": "kimi-k3",
        "prompt_rate": 15.0,
        "cached_rate": 3.75,
        "completion_rate": 60.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "kmodel",
        "model_name": "kimi-k2.8-preview",
        "prompt_rate": 12.0,
        "cached_rate": 3.0,
        "completion_rate": 48.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "mmodel",
        "model_name": "minimax-m2.7",
        "prompt_rate": 10.0,
        "cached_rate": 2.5,
        "completion_rate": 40.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "auto",
        "model_name": "auto",
        "prompt_rate": 0.8,
        "cached_rate": 0.2,
        "completion_rate": 2.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
    {
        "model_key": "lite",
        "model_name": "lite",
        "prompt_rate": 0.0,
        "cached_rate": 0.0,
        "completion_rate": 0.0,
        "last_fetched_at": SNAPSHOT_LAST_FETCHED_AT,
    },
]


def _resolve_pricing_key(model: str) -> str:
    cleaned = strip_model_prefix(model).strip().lower()
    internal = alias_to_internal_key(cleaned)
    return internal or cleaned


class MetricsManager:
    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            self.db_path = data_dir_path / "metrics.db"
        else:
            self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_pricing (
                        model_key TEXT PRIMARY KEY,
                        model_name TEXT NOT NULL,
                        prompt_rate REAL NOT NULL,
                        cached_rate REAL NOT NULL,
                        completion_rate REAL NOT NULL,
                        last_fetched_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS requests_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp INTEGER NOT NULL,
                        account_id TEXT,
                        model TEXT NOT NULL,
                        status_code INTEGER NOT NULL,
                        ttft_ms REAL,
                        total_duration_ms REAL,
                        tps REAL,
                        prompt_tokens INTEGER DEFAULT 0,
                        cached_tokens INTEGER DEFAULT 0,
                        completion_tokens INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        credits_deducted REAL DEFAULT 0.0,
                        official_cost_rmb REAL DEFAULT 0.0,
                        error_code TEXT
                    );
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_log_ts ON requests_log(timestamp);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_log_model ON requests_log(model);")

                for p in MODEL_PRICING_SNAPSHOT:
                    conn.execute(
                        """
                        INSERT INTO model_pricing (
                            model_key, model_name, prompt_rate, cached_rate, completion_rate, last_fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(model_key) DO UPDATE SET
                            model_name=excluded.model_name,
                            prompt_rate=excluded.prompt_rate,
                            cached_rate=excluded.cached_rate,
                            completion_rate=excluded.completion_rate,
                            last_fetched_at=excluded.last_fetched_at
                        """,
                        (
                            p["model_key"],
                            p["model_name"],
                            p["prompt_rate"],
                            p["cached_rate"],
                            p["completion_rate"],
                            p["last_fetched_at"],
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

    def get_model_pricing_map(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute("SELECT * FROM model_pricing")
                rows = cur.fetchall()
                pricing_map: dict[str, dict[str, Any]] = {}
                for r in rows:
                    item = dict(r)
                    pricing_map[item["model_key"]] = item
                    pricing_map[item["model_name"]] = item
                return pricing_map
            finally:
                conn.close()

    def get_model_pricing_list(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    "SELECT model_key, model_name, prompt_rate, cached_rate, completion_rate, last_fetched_at "
                    "FROM model_pricing ORDER BY model_key"
                )
                return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    def calculate_official_cost(
        self,
        model: str,
        prompt_tokens: int = 0,
        cached_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> float:
        pricing_map = self.get_model_pricing_map()
        target_key = _resolve_pricing_key(model)
        pricing = pricing_map.get(target_key)
        if not pricing:
            pricing = pricing_map.get(model)
        if not pricing:
            return 0.0

        p_rate = float(pricing.get("prompt_rate", 0.0))
        c_rate = float(pricing.get("cached_rate", 0.0))
        comp_rate = float(pricing.get("completion_rate", 0.0))

        # Rates are in RMB per 1M tokens
        prompt_non_cached = max(0, prompt_tokens - max(0, cached_tokens))
        cost = (
            (prompt_non_cached * p_rate)
            + (max(0, cached_tokens) * c_rate)
            + (max(0, completion_tokens) * comp_rate)
        ) / 1_000_000.0
        return round(cost, 6)

    def record_request(
        self,
        *,
        model: str,
        status_code: int,
        account_id: str | None = None,
        timestamp: int | None = None,
        ttft_ms: float | None = None,
        total_duration_ms: float | None = None,
        tps: float | None = None,
        prompt_tokens: int = 0,
        cached_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int | None = None,
        credits_deducted: float = 0.0,
        official_cost_rmb: float | None = None,
        error_code: str | None = None,
    ) -> int:
        if timestamp is None:
            timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)

        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens

        if official_cost_rmb is None:
            official_cost_rmb = self.calculate_official_cost(
                model=model,
                prompt_tokens=prompt_tokens,
                cached_tokens=cached_tokens,
                completion_tokens=completion_tokens,
            )

        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO requests_log (
                        timestamp, account_id, model, status_code, ttft_ms,
                        total_duration_ms, tps, prompt_tokens, cached_tokens,
                        completion_tokens, total_tokens, credits_deducted,
                        official_cost_rmb, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp,
                        account_id,
                        model,
                        status_code,
                        ttft_ms,
                        total_duration_ms,
                        tps,
                        prompt_tokens,
                        cached_tokens,
                        completion_tokens,
                        total_tokens,
                        credits_deducted,
                        official_cost_rmb,
                        str(error_code) if error_code is not None else None,
                    ),
                )
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    async def arecord_request(self, *args, **kwargs) -> int:
        return await asyncio.to_thread(self.record_request, *args, **kwargs)

    def get_metrics_overview(self) -> dict[str, Any]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT
                        COUNT(*) as total_requests,
                        AVG(ttft_ms) as avg_ttft,
                        AVG(tps) as avg_tps,
                        SUM(prompt_tokens) as prompt_tokens,
                        SUM(cached_tokens) as cached_tokens,
                        SUM(completion_tokens) as completion_tokens,
                        SUM(credits_deducted) as total_credits,
                        SUM(official_cost_rmb) as total_official_cost
                    FROM requests_log
                    """
                )
                row = cur.fetchone()
                total_requests = int(row["total_requests"] or 0)
                avg_ttft = float(row["avg_ttft"]) if row["avg_ttft"] is not None else 0.0
                avg_tps = float(row["avg_tps"]) if row["avg_tps"] is not None else 0.0
                prompt_tokens = int(row["prompt_tokens"] or 0)
                cached_tokens = int(row["cached_tokens"] or 0)
                completion_tokens = int(row["completion_tokens"] or 0)
                total_credits = float(row["total_credits"] or 0.0)
                total_official_cost = float(row["total_official_cost"] or 0.0)

                # Cache hit rate: cached_tokens / (prompt_tokens + cached_tokens) or cached_tokens / prompt_tokens
                # Usually cached_tokens / prompt_tokens if prompt_tokens includes cached, or cached / (prompt + cached)
                # If cached_tokens <= prompt_tokens: cached_tokens / prompt_tokens
                cache_hit_rate = 0.0
                if prompt_tokens > 0:
                    cache_hit_rate = min(1.0, cached_tokens / prompt_tokens)

                # Total saved: official_cost_rmb - total_credits (in RMB equivalent / proxy saving value)
                total_saved = max(0.0, total_official_cost)

                return {
                    "total_requests": total_requests,
                    "avg_ttft": round(avg_ttft, 2),
                    "avg_tps": round(avg_tps, 2),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cached_tokens": cached_tokens,
                    "cache_hit_rate": round(cache_hit_rate, 4),
                    "total_credits": round(total_credits, 4),
                    "total_official_cost": round(total_official_cost, 4),
                    "total_saved": round(total_saved, 4),
                }
            finally:
                conn.close()

    def get_metrics_by_model(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.execute(
                    """
                    SELECT
                        model,
                        COUNT(*) as request_count,
                        AVG(ttft_ms) as avg_ttft,
                        AVG(tps) as avg_tps,
                        SUM(prompt_tokens) as prompt_tokens,
                        SUM(cached_tokens) as cached_tokens,
                        SUM(completion_tokens) as completion_tokens,
                        SUM(credits_deducted) as total_credits,
                        SUM(official_cost_rmb) as total_official_cost
                    FROM requests_log
                    GROUP BY model
                    ORDER BY request_count DESC
                    """
                )
                rows = cur.fetchall()
                results = []
                for r in rows:
                    p_toks = int(r["prompt_tokens"] or 0)
                    c_toks = int(r["cached_tokens"] or 0)
                    hit_rate = (min(1.0, c_toks / p_toks)) if p_toks > 0 else 0.0
                    results.append(
                        {
                            "model": r["model"],
                            "request_count": int(r["request_count"]),
                            "avg_ttft": round(float(r["avg_ttft"] or 0.0), 2),
                            "avg_tps": round(float(r["avg_tps"] or 0.0), 2),
                            "prompt_tokens": p_toks,
                            "cached_tokens": c_toks,
                            "completion_tokens": int(r["completion_tokens"] or 0),
                            "cache_hit_rate": round(hit_rate, 4),
                            "total_credits": round(float(r["total_credits"] or 0.0), 4),
                            "total_official_cost": round(float(r["total_official_cost"] or 0.0), 4),
                        }
                    )
                return results
            finally:
                conn.close()

    def get_hourly_trends(self, hours: int = 24) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                start_ms = now_ms - (hours * 3600 * 1000)
                cur = conn.execute(
                    """
                    SELECT
                        (timestamp / 3600000) * 3600000 as hour_timestamp,
                        COUNT(*) as request_count,
                        AVG(ttft_ms) as avg_ttft,
                        AVG(tps) as avg_tps,
                        SUM(total_tokens) as total_tokens,
                        SUM(official_cost_rmb) as total_official_cost
                    FROM requests_log
                    WHERE timestamp >= ?
                    GROUP BY hour_timestamp
                    ORDER BY hour_timestamp ASC
                    """,
                    (start_ms,),
                )
                rows = cur.fetchall()
                results = []
                for r in rows:
                    hour_dt = datetime.fromtimestamp(r["hour_timestamp"] / 1000, tz=timezone.utc)
                    results.append(
                        {
                            "hour": hour_dt.strftime("%Y-%m-%d %H:00"),
                            "timestamp": int(r["hour_timestamp"]),
                            "request_count": int(r["request_count"]),
                            "avg_ttft": round(float(r["avg_ttft"] or 0.0), 2),
                            "avg_tps": round(float(r["avg_tps"] or 0.0), 2),
                            "total_tokens": int(r["total_tokens"] or 0),
                            "total_official_cost": round(float(r["total_official_cost"] or 0.0), 4),
                        }
                    )
                return results
            finally:
                conn.close()


metrics_manager = MetricsManager()

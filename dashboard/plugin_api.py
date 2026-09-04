"""SCNet Token Plan usage monitor — dashboard plugin backend.

Mounted at /api/plugins/scnet-usage/ by the dashboard plugin system.

Data source
-----------
scnet.cn exposes NO programmatic usage endpoint for Token Plan — the
console web page is session-cookie authenticated and the sk-tp API key
only unlocks the LLM inference routes (verified 2026-09: /usage,
/dashboard/billing/*, /credits, ... all return 401 "用户未登录" while
/models and /chat/completions work with the same key).

So this plugin meters locally instead: Hermes already records every API
call's token buckets into state.db (session_model_usage + sessions
tables, per model / provider / base_url / task). We aggregate the rows
whose billing_base_url points at api.scnet.cn and convert tokens to
Credits with the official Token Plan formula:

    credits = multiplier(model)
              * (input/120 + cached/2000 + output/(60000/1700))

where the per-bucket prices are the published Kimi-K2.6 anchors for a
1.00x model (60,000 Credits = 7.2M uncached input = 120M cached input
= 1.7M output) and multiplier is the model's 综合扣减倍率 from the
Token Plan page (adjusted weekly — user-editable at GET /config).

Semantics note: Hermes' session_model_usage.input_tokens EXCLUDES cached
tokens (normalize_usage subtracts prompt_tokens_details.cached_tokens
before persisting), so input and cache_read are separate, non-over-
lapping buckets — exactly matching the billing formula.

The estimate is therefore exact for calls Hermes recorded and blind to
usage from OTHER tools (Claude Code, Cursor, ...) sharing the same
Token Plan key. The plugin is explicit about this: it shows "Hermes 记录"
and lets the user pin the plan's real monthly quota so the remaining
figure degrades gracefully to "Hermes 份额内剩余".
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants — billing formula + bundled default multipliers
# ---------------------------------------------------------------------------

SCNET_BASE_URL = "https://api.scnet.cn"

# Tokens-per-credit at multiplier 1.0 (official Kimi-K2.6 anchors:
# 60,000 Credits ≈ 7.2M uncached input / 120M cached input / 1.7M output)
INPUT_TPC = 120.0        # uncached input tokens per credit @1x
CACHED_TPC = 2000.0      # cached input tokens per credit @1x
OUTPUT_TPC = 1.7e6 / 60000.0  # output tokens per credit @1x ≈ 28.33

# 综合扣减倍率, 2026-09-01 published values. Adjusted weekly upstream —
# user-editable via GET/POST /config (stored in the plugin config file).
DEFAULT_MULTIPLIERS: Dict[str, float] = {
    "GLM-5.3": 2.29,
    "GLM-5.3-Flash": 0.15,
    "GLM-5.2": 0.85,
    "GLM-5.1": 1.40,
    "GLM-5": 1.11,
    "DeepSeek-V4-Pro-0813": 1.11,
    "DeepSeek-V4-Flash-0731": 0.40,
    "DeepSeek-V4-Pro": 1.06,
    "DeepSeek-V4-Flash": 0.13,
    "Kimi-K3": 4.12,
    "Kimi-K2.7-Code": 1.00,
    "Kimi-K2.6": 1.00,
    "Kimi-K2.5": 0.65,
    "MiniMax-M3": 0.43,
    "MiniMax-M2.7": 0.43,
    "MiniMax-M2.5": 0.30,
    "Qwen3.8-Max": 2.10,
    "Qwen3.8-Flash": 0.14,
}
DEFAULT_MULTIPLIER_UNKNOWN = 1.0

# Plan tiers (for the picker): label -> monthly credits
PLAN_TIERS: Dict[str, float] = {
    "基础版": 60_000,
    "标准版": 240_000,
    "高级版": 600_000,
    "旗舰版": 1_800_000,
}

# ---------------------------------------------------------------------------
# Config persistence (JSON beside the plugin, survives restarts)
# ---------------------------------------------------------------------------

from hermes_constants import get_hermes_home  # noqa: E402


def _config_path() -> Path:
    return get_hermes_home() / "plugins" / "scnet-usage" / "config.json"


def _load_config() -> Dict[str, Any]:
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_config(cfg: Dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _effective_config() -> Dict[str, Any]:
    cfg = _load_config()
    multipliers = dict(DEFAULT_MULTIPLIERS)
    # merge user overrides (only valid positive floats)
    for k, v in (cfg.get("multipliers") or {}).items():
        try:
            if float(v) > 0:
                multipliers[k] = float(v)
        except (TypeError, ValueError):
            continue
    return {
        "monthly_credits": cfg.get("monthly_credits"),  # None = not pinned
        "plan_label": cfg.get("plan_label"),
        "cycle_start_day": int(cfg.get("cycle_start_day") or 1),
        "multipliers": multipliers,
        "unknown_multiplier": float(cfg.get("unknown_multiplier") or DEFAULT_MULTIPLIER_UNKNOWN),
    }


# ---------------------------------------------------------------------------
# DB access (read-only, WAL-safe)
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    home = Path(get_hermes_home())
    return home / "state.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"state.db not found at {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _credits(model: str, input_t: int, cached_t: int, output_t: int, cfg: Dict[str, Any]) -> float:
    mult = cfg["multipliers"].get(model, cfg["unknown_multiplier"])
    raw = input_t / INPUT_TPC + cached_t / CACHED_TPC + output_t / OUTPUT_TPC
    return raw * mult


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _scnet_rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """All session_model_usage rows billed to api.scnet.cn."""
    return list(
        conn.execute(
            """
            SELECT model, task,
                   SUM(input_tokens)      AS input_tokens,
                   SUM(output_tokens)     AS output_tokens,
                   SUM(cache_read_tokens) AS cache_read_tokens,
                   SUM(api_call_count)    AS api_calls,
                   MAX(last_seen)         AS last_seen,
                   MIN(first_seen)        AS first_seen
            FROM session_model_usage
            WHERE billing_base_url LIKE ?
               OR billing_base_url LIKE ?
            GROUP BY model, task
            """,
            (f"%{SCNET_BASE_URL}%", f"%api.scnet.cn%"),
        )
    )


def _cycle_bounds(cycle_start_day: int) -> tuple[float, float, str, str]:
    """Current subscription cycle [start_ts, next_ts) + labels.

    Token Plan cycles are natural months from the purchase day. With the
    default cycle_start_day=1 this is the calendar month; a user who
    bought on the 5th gets 05-month 00:00 to 05-next 00:00 (UTC+8).
    Short months clamp (a day-31 purchase rolls to the 28th/29th/30th).
    """
    import calendar

    now = datetime.now()
    day = min(max(1, cycle_start_day), 31)

    def _clamp(year: int, month: int, d: int) -> datetime:
        d = min(d, calendar.monthrange(year, month)[1])
        return datetime(year, month, d)

    if now.day >= day:
        start = _clamp(now.year, now.month, day)
    else:
        y, m = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        start = _clamp(y, m, day)

    ny, nm = (start.year, start.month + 1) if start.month < 12 else (start.year + 1, 1)
    nxt = _clamp(ny, nm, day)
    return start.timestamp(), nxt.timestamp(), start.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")


@router.get("/usage")
async def get_usage(
    days: int = Query(30, ge=1, le=365),
) -> Dict[str, Any]:
    """Aggregate SCNet usage + estimated Credits for the current cycle."""

    def _run() -> Dict[str, Any]:
        cfg = _effective_config()
        conn = _connect()
        try:
            start_ts, end_ts, start_label, end_label = _cycle_bounds(cfg["cycle_start_day"])

            # ---- per-model totals inside the cycle --------------------
            rows = list(
                conn.execute(
                    """
                    SELECT model, task,
                           SUM(input_tokens)      AS input_tokens,
                           SUM(output_tokens)     AS output_tokens,
                           SUM(cache_read_tokens) AS cache_read_tokens,
                           SUM(api_call_count)    AS api_calls,
                           MAX(last_seen)         AS last_seen
                    FROM session_model_usage
                    WHERE (billing_base_url LIKE ? OR billing_base_url LIKE ?)
                      AND last_seen >= ? AND last_seen < ?
                    GROUP BY model, task
                    """,
                    (f"%{SCNET_BASE_URL}%", "%api.scnet.cn%", start_ts, end_ts),
                )
            )

            by_model: Dict[str, Dict[str, Any]] = {}
            total = {"credits": 0.0, "input": 0, "output": 0, "cached": 0, "calls": 0}
            for r in rows:
                m = r["model"] or "unknown"
                entry = by_model.setdefault(
                    m,
                    {"model": m, "credits": 0.0, "input": 0, "output": 0,
                     "cached": 0, "calls": 0, "multiplier": cfg["multipliers"].get(m)},
                )
                c = _credits(m, r["input_tokens"] or 0, r["cache_read_tokens"] or 0,
                             r["output_tokens"] or 0, cfg)
                entry["credits"] += c
                entry["input"] += r["input_tokens"] or 0
                entry["output"] += r["output_tokens"] or 0
                entry["cached"] += r["cache_read_tokens"] or 0
                entry["calls"] += r["api_calls"] or 0
                total["credits"] += c
                total["input"] += r["input_tokens"] or 0
                total["output"] += r["output_tokens"] or 0
                total["cached"] += r["cache_read_tokens"] or 0
                total["calls"] += r["api_calls"] or 0
            models = sorted(by_model.values(), key=lambda x: -x["credits"])

            # ---- daily series (credits/day over the requested window) --
            cutoff = time.time() - days * 86400
            daily_rows = list(
                conn.execute(
                    """
                    SELECT date(last_seen, 'unixepoch', 'localtime') AS day,
                           model,
                           SUM(input_tokens)      AS input_tokens,
                           SUM(output_tokens)     AS output_tokens,
                           SUM(cache_read_tokens) AS cache_read_tokens
                    FROM session_model_usage
                    WHERE (billing_base_url LIKE ? OR billing_base_url LIKE ?)
                      AND last_seen >= ?
                    GROUP BY day, model
                    """,
                    (f"%{SCNET_BASE_URL}%", "%api.scnet.cn%", cutoff),
                )
            )
            daily: Dict[str, float] = {}
            for r in daily_rows:
                day = r["day"]
                c = _credits(r["model"] or "unknown", r["input_tokens"] or 0,
                             r["cache_read_tokens"] or 0, r["output_tokens"] or 0, cfg)
                daily[day] = daily.get(day, 0.0) + c
            series = [{"day": d, "credits": round(v, 2)} for d, v in sorted(daily.items())]

            today = datetime.now().strftime("%Y-%m-%d")
            today_credits = round(daily.get(today, 0.0), 2)

            # ---- lifetime totals (all time) -----------------------------
            life = conn.execute(
                """
                SELECT COALESCE(SUM(input_tokens), 0)  AS i,
                       COALESCE(SUM(output_tokens), 0) AS o,
                       COALESCE(SUM(cache_read_tokens), 0) AS c,
                       COALESCE(SUM(api_call_count), 0) AS n
                FROM session_model_usage
                WHERE billing_base_url LIKE ? OR billing_base_url LIKE ?
                """,
                (f"%{SCNET_BASE_URL}%", "%api.scnet.cn%"),
            ).fetchone()

            monthly = cfg.get("monthly_credits")
            remaining = (monthly - total["credits"]) if isinstance(monthly, (int, float)) else None

            return {
                "cycle": {"start": start_label, "end": end_label,
                          "start_ts": start_ts, "end_ts": end_ts},
                "multipliers_date": "2026-09-01",
                "totals": {
                    "credits": round(total["credits"], 2),
                    "input_tokens": total["input"],
                    "output_tokens": total["output"],
                    "cached_tokens": total["cached"],
                    "api_calls": total["calls"],
                },
                "plan": {
                    "monthly_credits": monthly,
                    "plan_label": cfg.get("plan_label"),
                    "remaining": round(remaining, 2) if remaining is not None else None,
                    "percent_used": round(total["credits"] / monthly * 100, 1)
                    if isinstance(monthly, (int, float)) and monthly > 0 else None,
                },
                "today_credits": today_credits,
                "models": [
                    {**m, "credits": round(m["credits"], 2)} for m in models
                ],
                "daily": series,
                "lifetime": {
                    "input_tokens": life["i"], "output_tokens": life["o"],
                    "cached_tokens": life["c"], "api_calls": life["n"],
                },
                "source_note": "基于 Hermes 本地记录估算（仅含 Hermes 发起的调用）",
            }
        finally:
            conn.close()

    return await asyncio.to_thread(_run)


@router.get("/config")
async def get_config() -> Dict[str, Any]:
    return _effective_config()


@router.post("/config")
async def post_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """Persist user settings: monthly_credits / plan_label /
    cycle_start_day / multipliers overrides / unknown_multiplier."""
    cfg = _load_config()
    if "monthly_credits" in body:
        v = body["monthly_credits"]
        cfg["monthly_credits"] = float(v) if isinstance(v, (int, float)) and v > 0 else None
    if "plan_label" in body:
        cfg["plan_label"] = str(body["plan_label"]) if body["plan_label"] else None
    if "cycle_start_day" in body:
        try:
            d = int(body["cycle_start_day"])
            if 1 <= d <= 31:
                cfg["cycle_start_day"] = d
        except (TypeError, ValueError):
            raise HTTPException(400, "cycle_start_day must be 1-31")
    if isinstance(body.get("multipliers"), dict):
        overrides = cfg.get("multipliers") or {}
        for k, v in body["multipliers"].items():
            try:
                if float(v) > 0:
                    overrides[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        cfg["multipliers"] = overrides
    if "unknown_multiplier" in body:
        try:
            v = float(body["unknown_multiplier"])
            if v > 0:
                cfg["unknown_multiplier"] = v
        except (TypeError, ValueError):
            pass
    _save_config(cfg)
    return _effective_config()


@router.get("/plans")
async def get_plans() -> Dict[str, Any]:
    """Plan tier presets for the picker UI."""
    return {"tiers": PLAN_TIERS}

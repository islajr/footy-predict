from __future__ import annotations

import asyncio
import uuid
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Any
from fastapi import APIRouter, Depends, Query, BackgroundTasks
from fastapi.responses import StreamingResponse
from src.database import Database
from src.api.deps import get_db
from src.api.schemas import QuotaStatusSchema
from src.config import LEAGUES, API_FOOTBALL, FOOTBALL_DATA_ORG
from src.fetchers.api_football import APIFootballClient
from src.fetchers.football_data_org import FootballDataOrgClient
from src.tracking.recorder import ResultRecorder

logger = logging.getLogger("footy.api.fetch")
router = APIRouter(prefix="/fetch", tags=["Fetch"])

# In-memory store for tracking background fetch tasks
FETCH_TASKS: dict[str, dict[str, Any]] = {}


def get_smart_fetch_plan(db: Database) -> dict[str, Any]:
    """Decide which leagues to fetch based on day of week and match schedules."""
    enabled_slugs = db.get_enabled_leagues()
    if not enabled_slugs:
        return {
            "strategy": "smart",
            "leagues_to_fetch": [],
            "rationale": "No leagues are currently enabled in settings.",
            "quota_cost_est": 0
        }

    # Check if database is empty of matches. If so, do full refresh.
    with db._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    
    if count == 0:
        return {
            "strategy": "smart",
            "leagues_to_fetch": enabled_slugs,
            "rationale": "Database matches table is empty. Triggering bootstrap fetch for all enabled leagues.",
            "quota_cost_est": len(enabled_slugs) * 4
        }

    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()  # 0=Monday, 6=Sunday

    # Thursday–Friday: full refresh for the upcoming weekend fixtures.
    if weekday in (3, 4):
        return {
            "strategy": "smart",
            "leagues_to_fetch": enabled_slugs,
            "rationale": "Thursday/Friday: weekend preview full refresh for all enabled leagues.",
            "quota_cost_est": len(enabled_slugs) * 4
        }

    def find_active_leagues_in_range(start_dt: datetime, end_dt: datetime) -> set[str]:
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = end_dt.strftime("%Y-%m-%d") + "T23:59:59"
        with db._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT l.slug
                FROM matches m
                JOIN leagues l ON m.league_id = l.id
                WHERE m.date >= ? AND m.date <= ?
                """,
                (start_str, end_str)
            ).fetchall()
            return {r[0] for r in rows if r[0] in enabled_slugs}

    # Saturday/Sunday: fetch only leagues with matches today/tomorrow
    if weekday in (5, 6):
        active_slugs = find_active_leagues_in_range(now_utc, now_utc + timedelta(days=1))
        if not active_slugs:
            return {
                "strategy": "smart",
                "leagues_to_fetch": enabled_slugs,
                "rationale": "Weekend: No scheduled matches found today/tomorrow in DB. Falling back to all enabled.",
                "quota_cost_est": len(enabled_slugs) * 4
            }
        return {
            "strategy": "smart",
            "leagues_to_fetch": list(active_slugs),
            "rationale": f"Weekend: Fetching active leagues with matches today/tomorrow ({', '.join(active_slugs)}).",
            "quota_cost_est": len(active_slugs) * 4
        }

    # Monday–Wednesday: fetch only leagues with midweek fixtures (next 3 days)
    active_slugs = find_active_leagues_in_range(now_utc, now_utc + timedelta(days=3))
    if not active_slugs:
        return {
            "strategy": "smart",
            "leagues_to_fetch": enabled_slugs,
            "rationale": "Midweek: No midweek matches found in DB. Falling back to all enabled.",
            "quota_cost_est": len(enabled_slugs) * 4
        }
    return {
        "strategy": "smart",
        "leagues_to_fetch": list(active_slugs),
        "rationale": f"Midweek: Fetching leagues with midweek fixtures in next 3 days ({', '.join(active_slugs)}).",
        "quota_cost_est": len(active_slugs) * 4
    }


async def run_background_fetch(task_id: str, league_slug: str | None, db: Database):
    """Worker function executing the async fetches for leagues."""
    task = FETCH_TASKS[task_id]
    try:
        api_client = APIFootballClient(db)
        fdo_client = FootballDataOrgClient(db)

        if league_slug:
            if league_slug not in LEAGUES:
                task["status"] = "failed"
                task["progress"] = f"Unknown league: {league_slug}"
                task["logs"].append(f"ERROR: Unknown league: {league_slug}")
                return
            target_leagues = [LEAGUES[league_slug]]
        else:
            strategy = db.get_setting("fetch_strategy") or "full"
            if strategy == "smart":
                plan = get_smart_fetch_plan(db)
                slugs = plan["leagues_to_fetch"]
                task["logs"].append(f"Smart fetch plan activated. Rationale: {plan['rationale']}")
                task["logs"].append(f"Estimated API cost: {plan['quota_cost_est']} calls.")
                target_leagues = [LEAGUES[s] for s in slugs if s in LEAGUES]
            else:
                # Full strategy: fetch active/enabled leagues
                enabled_slugs = db.get_enabled_leagues()
                if not enabled_slugs:
                    task["logs"].append("No active leagues enabled. Fetching all available leagues by default.")
                    target_leagues = list(LEAGUES.values())
                else:
                    target_leagues = [LEAGUES[s] for s in enabled_slugs if s in LEAGUES]

        task["total"] = len(target_leagues)
        task["progress"] = f"Starting fetch for {task['total']} league(s)..."
        task["logs"].append(task["progress"])

        total_fixtures = 0
        total_standings = 0

        for i, lg in enumerate(target_leagues, 1):
            task["progress"] = f"Fetching {lg.name} ({i}/{task['total']})..."
            task["logs"].append(task["progress"])

            try:
                # Use FDO for leagues it covers (fixtures + standings)
                if lg.has_fdo and FOOTBALL_DATA_ORG.key:
                    task["logs"].append(f"[{lg.name}] Fetching fixtures & standings from Football-Data.org...")
                    count = await fdo_client.fetch_and_store_fixtures(lg.slug)
                    total_fixtures += count
                    st_count = await fdo_client.fetch_and_store_standings(lg.slug)
                    total_standings += st_count
                elif API_FOOTBALL.key:
                    task["logs"].append(f"[{lg.name}] Fetching fixtures & standings from API-Football...")
                    count = await api_client.fetch_and_store_fixtures(lg.slug)
                    total_fixtures += count
                    st_count = await api_client.fetch_and_store_standings(lg.slug)
                    total_standings += st_count
                else:
                    task["logs"].append(f"[{lg.name}] WARNING: No API keys configured for league fetching.")

                # Team stats from API-Football (for all leagues)
                if API_FOOTBALL.key:
                    task["logs"].append(f"[{lg.name}] Fetching team seasonal statistics...")
                    await api_client.fetch_and_store_team_stats(lg.slug)

            except Exception as e:
                err_msg = f"[{lg.name}] ERROR: {str(e)}"
                logger.error(err_msg, exc_info=True)
                task["logs"].append(err_msg)

            task["completed"] = i

        # Run auto-recorder on completion
        task["progress"] = "Evaluating prediction results for finished matches..."
        task["logs"].append(task["progress"])
        try:
            recorder = ResultRecorder(db)
            record_summary = recorder.auto_record_all()
            task["logs"].append(
                f"Auto-recorded results: {record_summary.get('matches_processed', 0)} matches, "
                f"{record_summary.get('correct', 0)} correct, {record_summary.get('incorrect', 0)} incorrect."
            )
            task["result"] = record_summary
        except Exception as e:
            logger.error("Auto-recording results failed", exc_info=True)
            task["logs"].append(f"WARNING: Auto-recording results failed: {str(e)}")

        task["status"] = "success"
        task["progress"] = f"Fetch complete. Fixtures: {total_fixtures}, Standings: {total_standings}."
        task["logs"].append(task["progress"])

    except Exception as e:
        logger.error(f"Background fetch task {task_id} failed", exc_info=True)
        task["status"] = "failed"
        task["progress"] = f"Fetch failed: {str(e)}"
        task["logs"].append(f"CRITICAL ERROR: {str(e)}")


@router.post("")
def trigger_fetch(
    league: str | None = Query(None, description="League slug filter"),
    db: Database = Depends(get_db),
):
    """Trigger data refresh in a background task."""
    task_id = str(uuid.uuid4())
    FETCH_TASKS[task_id] = {
        "status": "running",
        "progress": "Initializing...",
        "completed": 0,
        "total": 0,
        "logs": [],
        "result": None,
    }

    # Spawn task in the background
    asyncio.create_task(run_background_fetch(task_id, league, db))

    return {"task_id": task_id, "status": "running"}


@router.get("/status/{task_id}")
def get_fetch_status(task_id: str):
    """Get the current progress of a background fetch task."""
    if task_id not in FETCH_TASKS:
        return {"status": "not_found", "progress": "Task not found."}
    return FETCH_TASKS[task_id]


@router.get("/stream/{task_id}")
async def stream_fetch_logs(task_id: str):
    """SSE endpoint streaming log lines of a fetch task as they arrive."""
    async def event_generator():
        if task_id not in FETCH_TASKS:
            yield f"data: {json.dumps({'status': 'not_found', 'progress': 'Task not found.', 'done': True})}\n\n"
            return

        task = FETCH_TASKS[task_id]
        last_idx = 0
        while True:
            logs = task["logs"]
            if last_idx < len(logs):
                for i in range(last_idx, len(logs)):
                    yield f"data: {json.dumps({'status': task['status'], 'log': logs[i], 'progress': task['progress']})}\n\n"
                last_idx = len(logs)

            if task["status"] in ("success", "failed"):
                yield f"data: {json.dumps({'status': task['status'], 'progress': task['progress'], 'done': True})}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/quota", response_model=list[QuotaStatusSchema])
def get_quota_status(db: Database = Depends(get_db)):
    """Retrieve today's API request quotas."""
    api_football_usage = db.get_api_usage_today("api_football")
    fdo_usage = db.get_api_usage_today("football_data_org")

    # Limit configuration fallback
    api_limit = api_football_usage.get("requests_limit") or API_FOOTBALL.daily_limit
    fdo_limit = fdo_usage.get("requests_limit") or 1440  # default FDO daily limit 10/min ~ 14400/day, let's keep FDO open

    api_used = api_football_usage.get("requests_made", 0)
    fdo_used = fdo_usage.get("requests_made", 0)

    return [
        QuotaStatusSchema(
            provider="API-Football",
            used_today=api_used,
            daily_limit=api_limit,
            remaining=max(0, api_limit - api_used),
        ),
        QuotaStatusSchema(
            provider="Football-Data.org",
            used_today=fdo_used,
            daily_limit=fdo_limit,
            remaining=max(0, fdo_limit - fdo_used),
        ),
    ]

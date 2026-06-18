from __future__ import annotations

import asyncio
import uuid
import logging
from typing import Any
from fastapi import APIRouter, Depends, Query, BackgroundTasks
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
            # In Stage 2 we'll filter this by active leagues in SQLite,
            # but for now we fetch all canonical LEAGUES.
            target_leagues = list(LEAGUES.values())

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

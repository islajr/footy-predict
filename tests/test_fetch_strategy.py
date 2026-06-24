import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from src.api.app import app
from src.api.deps import get_db
from src.database import Database
from src.api.routes.fetch import get_smart_fetch_plan, FETCH_TASKS


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test_fetch.db"
    db = Database(str(db_path))
    db.seed_leagues()
    # Seed default enabled leagues
    db.seed_user_defaults()
    return db


@pytest.fixture
def client(temp_db):
    app.state.db = temp_db
    app.dependency_overrides[get_db] = lambda: temp_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.db = None


def test_smart_fetch_plan_empty_db(temp_db):
    # If DB is empty, should trigger bootstrap fetch for all enabled leagues
    enabled_leagues = temp_db.get_enabled_leagues()
    plan = get_smart_fetch_plan(temp_db)
    
    assert plan["strategy"] == "smart"
    assert set(plan["leagues_to_fetch"]) == set(enabled_leagues)
    assert "bootstrap" in plan["rationale"].lower()


def test_smart_fetch_plan_weekend(temp_db):
    # Insert dummy teams and match so count > 0
    with temp_db._connect() as conn:
        conn.execute("INSERT INTO teams (id, name, league_id) VALUES (1, 'Team A', 1)")
        conn.execute("INSERT INTO teams (id, name, league_id) VALUES (2, 'Team B', 1)")
        conn.execute(
            """
            INSERT INTO matches (league_id, season, date, status, home_team_id, away_team_id)
            VALUES (1, '2025', '2025-06-18T12:00:00', 'SCHEDULED', 1, 2)
            """
        )

    # Set mock date to a Saturday (weekday 5)
    mock_saturday = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
    
    with patch("src.api.routes.fetch.datetime") as mock_dt:
        mock_dt.now.return_value = mock_saturday
        plan = get_smart_fetch_plan(temp_db)
        
        assert plan["strategy"] == "smart"
        assert "weekend" in plan["rationale"].lower()


def test_smart_fetch_plan_midweek(temp_db):
    # Insert dummy teams and match so count > 0
    with temp_db._connect() as conn:
        conn.execute("INSERT INTO teams (id, name, league_id) VALUES (1, 'Team A', 1)")
        conn.execute("INSERT INTO teams (id, name, league_id) VALUES (2, 'Team B', 1)")
        conn.execute(
            """
            INSERT INTO matches (league_id, season, date, status, home_team_id, away_team_id)
            VALUES (1, '2025', '2025-06-18T12:00:00', 'SCHEDULED', 1, 2)
            """
        )

    # Set mock date to a Monday (weekday 0)
    mock_monday = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    
    with patch("src.api.routes.fetch.datetime") as mock_dt:
        mock_dt.now.return_value = mock_monday
        plan = get_smart_fetch_plan(temp_db)
        
        assert plan["strategy"] == "smart"
        assert "midweek" in plan["rationale"].lower()


def test_sse_stream_endpoint(client):
    # Populate a fake task in FETCH_TASKS
    task_id = "test-stream-id"
    FETCH_TASKS[task_id] = {
        "status": "success",
        "progress": "Completed testing fetch",
        "completed": 1,
        "total": 1,
        "logs": ["Log line 1", "Log line 2"],
        "result": None,
    }

    # Fetch SSE stream
    response = client.get(f"/api/v1/fetch/stream/{task_id}")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    
    # Read response body lines
    content = response.text
    assert "Log line 1" in content
    assert "Log line 2" in content
    assert "success" in content
    assert "done" in content

    # Clean up fake task
    del FETCH_TASKS[task_id]


def test_sse_stream_not_found(client):
    response = client.get("/api/v1/fetch/stream/nonexistent")
    assert response.status_code == 200
    assert "not_found" in response.text

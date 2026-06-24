import os
import tempfile
import asyncio
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from src.api.app import app
from src.api.deps import get_db
from src.database import Database

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    db.seed_leagues()
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass

@pytest.fixture
def client(temp_db):
    # Override get_db dependency to point to the temporary test database
    app.state.db = temp_db
    app.dependency_overrides[get_db] = lambda: temp_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.db = None

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database_connected"] is True

def test_picks_endpoint_empty(client):
    response = client.get("/api/v1/picks")
    assert response.status_code == 200
    assert response.json() == []

def test_fetch_quota(client):
    response = client.get("/api/v1/fetch/quota")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["provider"] == "API-Football"
    assert data[1]["provider"] == "Football-Data.org"

def test_accuracy_dashboard(client):
    response = client.get("/api/v1/accuracy")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "correct" in data
    assert "accuracy_pct" in data

def test_calibration_readiness(client):
    response = client.get("/api/v1/accuracy/readiness")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is False
    assert data["total_predictions"] == 0

def test_calibrate_insufficient_data(client):
    response = client.post("/api/v1/accuracy/calibrate")
    assert response.status_code == 400
    assert "Insufficient historical data" in response.json()["detail"]

def test_calibrate_reset(client):
    response = client.post("/api/v1/accuracy/calibrate?reset=true")
    assert response.status_code == 200
    data = response.json()
    assert "Weights successfully reset" in data["message"]

def test_settings_retrieval_and_update(client):
    # Retrieve default settings
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["theme"] == "dark"
    assert data["onboarding_complete"] is False

    # Update settings
    payload = {
        "api_football_key": "test-football-key",
        "football_data_org_key": "test-fdo-key",
        "theme": "light",
        "onboarding_complete": True,
        "fetch_strategy": "smart"
    }
    update_resp = client.post("/api/v1/settings", json=payload)
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert updated_data["theme"] == "light"
    assert updated_data["onboarding_complete"] is True
    assert updated_data["api_football_key"] == "test-football-key"

def test_leagues_preferences(client):
    # Retrieve leagues
    response = client.get("/api/v1/settings/leagues")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 12
    assert any(lg["slug"] == "premier-league" for lg in data)

    # Disable a league
    data[0]["enabled"] = False
    update_resp = client.post("/api/v1/settings/leagues", json=data)
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert updated_data[0]["enabled"] is False

def test_markets_preferences(client):
    # Retrieve markets
    response = client.get("/api/v1/settings/markets")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 9

    # Disable double chance and set probability floor
    for m in data:
        if m["market_code"] == "double-chance":
            m["enabled"] = False
            m["min_probability"] = 0.65

    update_resp = client.post("/api/v1/settings/markets", json=data)
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    for m in updated_data:
        if m["market_code"] == "double-chance":
            assert m["enabled"] is False
            assert m["min_probability"] == 0.65

def test_html_views_onboarding_redirects(client):
    # Root (landing page) is accessible even if onboarding is incomplete
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "Footy Predict" in response.text

    # Dashboard redirects too
    response_dash = client.get("/dashboard", follow_redirects=False)
    assert response_dash.status_code == 307
    assert response_dash.headers["location"] == "/onboarding"

    # Onboarding page is accessible directly
    response_onb = client.get("/onboarding")
    assert response_onb.status_code == 200
    assert "Footy Predict" in response_onb.text

def test_html_views_authorized(client, temp_db):
    # Set onboarding as complete
    temp_db.set_setting("onboarding_complete", "1")

    # Accessing dashboard should work now
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Welcome back, Bettor!" in response.text

    # Accessing picks should work
    response_picks = client.get("/picks")
    assert response_picks.status_code == 200
    assert "High-Confidence Recommendations" in response_picks.text

    # Accessing statistics should work
    response_stats = client.get("/statistics")
    assert response_stats.status_code == 200
    assert "Accuracy & Model Calibration" in response_stats.text

    # Accessing settings should work
    response_settings = client.get("/settings")
    assert response_settings.status_code == 200
    assert "App Preferences & Settings" in response_settings.text

    # Accessing parlay builder should work
    response_parlay = client.get("/parlay")
    assert response_parlay.status_code == 200
    assert "Interactive Parlay Builder" in response_parlay.text

    # Accessing parlay history should work
    response_history = client.get("/parlay/history")
    assert response_history.status_code == 200
    assert "Parlay History & Settle" in response_history.text


def test_parlay_api_flow(client):
    # Save a draft parlay
    payload = {
        "name": "Test Accumulator",
        "size": 2,
        "picks": [
            {
                "match_id": 1,
                "home_team": "Team A",
                "away_team": "Team B",
                "league": "Premier League",
                "market_code": "1x2",
                "market_name": "Full Time Result",
                "pick": "Home",
                "probability": 0.70,
                "confidence": "MEDIUM",
                "implied_odds": 1.43
            },
            {
                "match_id": 2,
                "home_team": "Team C",
                "away_team": "Team D",
                "league": "La Liga",
                "market_code": "gg-ng",
                "market_name": "Both Teams To Score",
                "pick": "Yes",
                "probability": 0.65,
                "confidence": "MEDIUM",
                "implied_odds": 1.54
            }
        ],
        "status": "draft",
        "notes": "Testing API flow"
    }
    response = client.post("/api/v1/parlay", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Accumulator"
    assert data["status"] == "draft"
    assert len(data["picks"]) == 2
    parlay_id = data["id"]
    assert parlay_id is not None

    # Retrieve all parlays
    get_all_resp = client.get("/api/v1/parlay")
    assert get_all_resp.status_code == 200
    all_parlays = get_all_resp.json()
    assert len(all_parlays) >= 1
    assert any(p["id"] == parlay_id for p in all_parlays)

    # Lock and Submit
    submit_resp = client.post(f"/api/v1/parlay/{parlay_id}/submit")
    assert submit_resp.status_code == 200
    submit_data = submit_resp.json()
    assert submit_data["status"] == "submitted"
    assert submit_data["submitted_at"] is not None

    # Settle outcome
    settle_resp = client.post(f"/api/v1/parlay/{parlay_id}/settle?status=won&correct_count=2")
    assert settle_resp.status_code == 200
    settle_data = settle_resp.json()
    assert settle_data["status"] == "won"
    assert settle_data["correct_count"] == 2

    # Delete
    del_resp = client.delete(f"/api/v1/parlay/{parlay_id}")
    assert del_resp.status_code == 200
    assert "deleted" in del_resp.json()["message"]

    # Verify deleted
    get_all_resp_after = client.get("/api/v1/parlay")
    assert not any(p["id"] == parlay_id for p in get_all_resp_after.json())


def test_cancel_fetch_endpoint_not_running(client):
    response = client.post("/api/v1/fetch/cancel/some-non-existent-task-id")
    assert response.status_code == 200
    assert response.json()["status"] == "not_found"


def test_cancel_fetch_endpoint_running(client):
    from src.api.routes.fetch import RUNNING_TASKS, FETCH_TASKS
    from unittest.mock import MagicMock

    task_id = "test-running-task-id"
    mock_task = MagicMock()
    RUNNING_TASKS[task_id] = mock_task
    FETCH_TASKS[task_id] = {
        "status": "running",
        "progress": "Running...",
        "logs": []
    }

    # Cancel the running task
    cancel_resp = client.post(f"/api/v1/fetch/cancel/{task_id}")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"
    
    # Assert task.cancel() was called
    mock_task.cancel.assert_called_once()
    
    # Assert status in FETCH_TASKS was updated
    assert FETCH_TASKS[task_id]["status"] == "failed"
    assert "Cancelled" in FETCH_TASKS[task_id]["progress"]

    # Clean up
    if task_id in RUNNING_TASKS:
        del RUNNING_TASKS[task_id]
    if task_id in FETCH_TASKS:
        del FETCH_TASKS[task_id]




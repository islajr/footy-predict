import os
import tempfile
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
    app.dependency_overrides[get_db] = lambda: temp_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()

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

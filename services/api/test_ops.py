import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta

from services.api.main import app
from services.api.models import Job, JobStatus, Base, FenEvalCache, PuzzleStats, PuzzleReview
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Use in-memory SQLite for tests to prevent leakage
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture(scope="function", autouse=True)
def init_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)

@pytest.fixture
def client(db_session):
    from services.api.db import get_db
    def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]

def test_health_endpoint(client):
    response = client.get("/ops/health")
    assert response.status_code == 200
    data = response.json()
    # It might be false if worker is not running in test env
    assert "ok" in data
    assert data["db"] == "ok"
    assert "worker" in data
    assert "stockfish" in data
    assert "version" in data
    assert "sha" in data["version"]

def test_ops_status_basic(client, db_session: Session):
    # Ensure some recent jobs exist
    job1 = Job(
        type="puzzle_generation",
        username="testuser",
        status=JobStatus.SUCCEEDED,
        progress_current=100,
        result_json={"generated": 5, "cache_hits": 10, "cache_misses": 2},
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=9)
    )
    db_session.add(job1)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert "now" in data
    assert "active_job" in data
    assert len(data["recent_jobs"]) >= 1
    assert data["recent_jobs"][0]["username"] == "testuser"
    assert data["metrics"]["last_24h"]["jobs_succeeded"] >= 1
    assert data["metrics"]["last_24h"]["cache_hits"] >= 10

def test_ops_status_active_job(client, db_session: Session):
    # Add a running job
    job = Job(
        type="puzzle_generation",
        username="active_user",
        status=JobStatus.RUNNING,
        progress_current=45,
        message="Analyzing... "
    )
    db_session.add(job)
    db_session.commit()

    response = client.get("/ops/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active_job"] is not None
    assert data["active_job"]["username"] == "active_user"
    assert data["active_job"]["status"] == "running"

@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

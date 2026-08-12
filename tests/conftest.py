from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from piuda import create_app


@pytest.fixture
def fixed_now():
    return datetime(2026, 8, 10, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.fixture
def app(tmp_path, fixed_now):
    application = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path,
            "DATABASE": tmp_path / "test.db",
            "SECRET_KEY": "test-secret",
            "DEMO_MODE": False,
            "NOW_PROVIDER": lambda: fixed_now,
            "OLLAMA_URL": "http://127.0.0.1:1",
            "INACTIVITY_MINUTES": 180,
            "TASK_GRACE_MINUTES": 30,
        }
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def caregiver_token(client):
    response = client.post(
        "/api/v1/auth/setup",
        json={"name": "테스트 보호자", "pin": "2468", "device_name": "pytest"},
    )
    assert response.status_code == 201
    return response.get_json()["token"]


@pytest.fixture
def auth_headers(caregiver_token):
    return {"Authorization": f"Bearer {caregiver_token}"}

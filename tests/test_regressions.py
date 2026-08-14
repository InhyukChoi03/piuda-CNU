from __future__ import annotations

from datetime import timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from piuda.clock import iso
from piuda.config import load_settings
from piuda.db import get_db
from piuda.risk import evaluate_risk


def register_sensor(client, headers, uid="regression-sensor"):
    response = client.post(
        "/api/v1/sensors",
        headers=headers,
        json={"device_uid": uid, "name": "회귀 센서", "location": "거실"},
    )
    assert response.status_code == 201
    return response.get_json()


def send_event(client, sensor, event_type, occurred_at, **extra):
    return client.post(
        "/api/v1/sensor-events",
        headers={"X-Piuda-Sensor-Key": sensor["api_key"]},
        json={
            "device_uid": sensor["device_uid"],
            "event_type": event_type,
            "occurred_at": occurred_at,
            **extra,
        },
    )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("put", "/api/v1/profile", {"birth_year": {}}),
        ("post", "/api/v1/routines", {"title": "일정", "category": "meal", "scheduled_time": "12:00", "days_mask": None}),
        ("post", "/api/v1/routines", {"title": "일정", "category": "meal", "scheduled_time": "12:00", "instructions": {}}),
    ],
)
def test_invalid_caregiver_payloads_return_400(client, auth_headers, method, path, body):
    response = getattr(client, method)(path, headers=auth_headers, json=body)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_invalid_task_body_returns_400(client, auth_headers):
    routine = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "일정", "category": "other", "scheduled_time": "18:00"},
    ).get_json()
    task = client.get("/api/v1/tasks/today").get_json()["items"][0]
    assert task["routine_id"] == routine["id"]
    response = client.post(f"/api/v1/tasks/{task['id']}/complete", json=[1])
    assert response.status_code == 400


@pytest.mark.parametrize("body", [b"null", b"{broken"])
def test_task_completion_rejects_non_object_json_without_completing(client, auth_headers, body):
    client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "일정", "category": "other", "scheduled_time": "18:00"},
    )
    task = client.get("/api/v1/tasks/today").get_json()["items"][0]

    response = client.post(
        f"/api/v1/tasks/{task['id']}/complete",
        data=body,
        content_type="application/json",
    )

    assert response.status_code == 400
    unchanged = client.get("/api/v1/tasks/today").get_json()["items"][0]
    assert unchanged["status"] == "pending"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/auth/setup", {"pin": 2468}),
        ("/api/v1/auth/setup", {"pin": "2468", "name": {}}),
    ],
)
def test_setup_rejects_coerced_auth_fields(client, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


@pytest.mark.parametrize("value", ["9:0", "9:00", "09:0", "09:00 "])
def test_routine_time_requires_canonical_hh_mm(client, auth_headers, value):
    response = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "일정", "category": "other", "scheduled_time": value},
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "extra",
    [
        {"confidence": []},
        {"confidence": float("nan")},
        {"value": {}},
        {"details": []},
    ],
)
def test_invalid_sensor_numbers_return_400(client, auth_headers, fixed_now, extra):
    sensor = register_sensor(client, auth_headers, uid=f"bad-{len(str(extra))}-{id(extra)}")
    response = send_event(client, sensor, "pir_motion", fixed_now.isoformat(), **extra)
    assert response.status_code == 400


def test_sensor_time_is_normalized_across_offsets(client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers, uid="offset-sensor")
    # 같은 순간을 UTC 표기로 보내도 최근 낙상으로 판정해야 합니다.
    utc_text = (fixed_now - timedelta(minutes=5)).astimezone(timezone.utc).isoformat()
    response = send_event(client, sensor, "csi_fall", utc_text, confidence=0.91)
    assert response.status_code == 202
    assert response.get_json()["risk"]["score"] == 50

    with client.application.app_context():
        stored = get_db().execute(
            "SELECT occurred_at FROM sensor_events ORDER BY id DESC LIMIT 1"
        ).fetchone()["occurred_at"]
    assert stored.endswith("+09:00")


def test_far_future_sensor_time_is_rejected(client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers, uid="future-sensor")
    response = send_event(
        client,
        sensor,
        "csi_fall",
        (fixed_now + timedelta(minutes=6)).isoformat(),
        confidence=0.91,
    )
    assert response.status_code == 400


def test_recent_heartbeat_with_no_motion_detects_long_inactivity(app, client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers, uid="heartbeat-only")
    response = send_event(
        client,
        sensor,
        "heartbeat",
        (fixed_now - timedelta(hours=4)).isoformat(),
    )
    assert response.status_code == 202

    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE sensor_devices SET created_at=?, last_seen_at=? WHERE device_uid=?",
            (
                iso(fixed_now - timedelta(hours=4)),
                iso(fixed_now),
                sensor["device_uid"],
            ),
        )
        database.execute(
            "UPDATE sensor_events SET received_at=? WHERE event_type='heartbeat'",
            (iso(fixed_now),),
        )
        database.commit()

    risk = client.get("/api/v1/risk/current").get_json()
    assert "long_pir_inactivity" in {factor["code"] for factor in risk["factors"]}


def test_first_recent_heartbeat_does_not_count_old_registration_as_inactivity(
    app, client, auth_headers, fixed_now
):
    sensor = register_sensor(client, auth_headers, uid="first-heartbeat")
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE sensor_devices SET created_at=?, last_seen_at=? WHERE device_uid=?",
            (
                iso(fixed_now - timedelta(hours=4)),
                iso(fixed_now),
                sensor["device_uid"],
            ),
        )
        database.execute(
            """
            INSERT INTO sensor_events(
              device_id, event_type, value, confidence, occurred_at, received_at, payload_json
            ) SELECT id, 'heartbeat', NULL, NULL, ?, ?, '{}' FROM sensor_devices WHERE device_uid=?
            """,
            (iso(fixed_now), iso(fixed_now), sensor["device_uid"]),
        )
        database.commit()

    risk = client.get("/api/v1/risk/current").get_json()
    assert "long_pir_inactivity" not in {factor["code"] for factor in risk["factors"]}


def test_stale_sensor_status_is_reflected_in_health_score(app, client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers, uid="offline-sensor")
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE sensor_devices SET last_seen_at=? WHERE device_uid=?",
            (iso(fixed_now - timedelta(minutes=31)), sensor["device_uid"]),
        )
        database.commit()

    risk = client.get("/api/v1/risk/current").get_json()
    factors = {factor["code"]: factor for factor in risk["factors"]}
    assert risk["score"] == 75
    assert risk["level"] == "caution"
    assert "offline-sensor" not in factors
    assert "sensor_offline" in factors
    assert "31분 전" in factors["sensor_offline"]["evidence"]


def test_stale_sensor_motion_does_not_make_an_online_sensor_look_inactive(
    app, client, auth_headers, fixed_now
):
    stale = register_sensor(client, auth_headers, uid="stale-motion")
    online = register_sensor(client, auth_headers, uid="online-heartbeat")
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE sensor_devices SET created_at=?, last_seen_at=? WHERE device_uid=?",
            (
                iso(fixed_now - timedelta(hours=4)),
                iso(fixed_now - timedelta(minutes=31)),
                stale["device_uid"],
            ),
        )
        database.execute(
            "UPDATE sensor_devices SET created_at=?, last_seen_at=? WHERE device_uid=?",
            (
                iso(fixed_now - timedelta(hours=4)),
                iso(fixed_now),
                online["device_uid"],
            ),
        )
        database.execute(
            """
            INSERT INTO sensor_events(
              device_id, event_type, occurred_at, received_at, payload_json
            ) SELECT id, 'pir_motion', ?, ?, '{}' FROM sensor_devices WHERE device_uid=?
            """,
            (
                iso(fixed_now - timedelta(hours=4)),
                iso(fixed_now - timedelta(minutes=31)),
                stale["device_uid"],
            ),
        )
        database.execute(
            """
            INSERT INTO sensor_events(
              device_id, event_type, occurred_at, received_at, payload_json
            ) SELECT id, 'heartbeat', ?, ?, '{}' FROM sensor_devices WHERE device_uid=?
            """,
            (iso(fixed_now), iso(fixed_now), online["device_uid"]),
        )
        database.commit()

    risk = client.get("/api/v1/risk/current").get_json()
    codes = {factor["code"] for factor in risk["factors"]}
    assert "sensor_offline" in codes
    assert "long_pir_inactivity" not in codes


def test_concurrent_risk_evaluations_create_one_assessment(app):
    def evaluate_once():
        with app.app_context():
            return evaluate_risk()

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: evaluate_once(), range(12)))

    assert len({item["id"] for item in results}) == 1
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM risk_assessments"
        ).fetchone()["count"]
    assert count == 1


def test_direct_alert_does_not_suppress_real_risk_alert(app, client, auth_headers, fixed_now):
    client.post("/api/v1/caregiver-alert")
    sensor = register_sensor(client, auth_headers, uid="alert-dedupe")
    response = send_event(
        client,
        sensor,
        "csi_fall",
        (fixed_now - timedelta(minutes=1)).isoformat(),
        confidence=0.91,
    )
    assert response.get_json()["risk"]["new_alert"] is True
    with app.app_context():
        risk_alerts = get_db().execute(
            "SELECT COUNT(*) AS count FROM alerts WHERE risk_assessment_id IS NOT NULL"
        ).fetchone()["count"]
    assert risk_alerts == 1


def test_updating_routine_rebuilds_pending_occurrence(client, auth_headers):
    routine = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "시간 변경", "category": "other", "scheduled_time": "09:00"},
    ).get_json()
    before = client.get("/api/v1/tasks/today").get_json()["items"][0]
    assert before["scheduled_time"] == "09:00"

    response = client.put(
        f"/api/v1/routines/{routine['id']}",
        headers=auth_headers,
        json={"scheduled_time": "20:00"},
    )
    assert response.status_code == 200
    after = client.get("/api/v1/tasks/today").get_json()["items"][0]
    assert after["scheduled_time"] == "20:00"
    assert "T20:00:00" in after["due_at"]
    assert after["status"] == "pending"


def test_dotenv_is_loaded_without_overriding_environment(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "PIUDA_INACTIVITY_MINUTES=7\nPIUDA_KAKAO_ACCESS_TOKEN=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PIUDA_INACTIVITY_MINUTES", raising=False)
    monkeypatch.delenv("PIUDA_KAKAO_ACCESS_TOKEN", raising=False)
    settings = load_settings({"DATA_DIR": tmp_path / "data", "DOTENV_PATH": dotenv})
    assert settings.inactivity_minutes == 7
    assert settings.kakao_access_token == "from-file"

    monkeypatch.setenv("PIUDA_INACTIVITY_MINUTES", "11")
    settings = load_settings({"DATA_DIR": tmp_path / "other", "DOTENV_PATH": dotenv})
    assert settings.inactivity_minutes == 11

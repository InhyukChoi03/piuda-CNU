from __future__ import annotations

import sqlite3

from piuda.db import get_db, init_database


def _register(client, headers, uid: str) -> dict:
    response = client.post(
        "/api/v1/sensors",
        headers=headers,
        json={"device_uid": uid, "name": "멱등 센서", "location": "거실"},
    )
    assert response.status_code == 201
    return response.get_json()


def test_sensor_retry_with_same_event_id_is_recorded_once(
    app, client, auth_headers, fixed_now
):
    sensor = _register(client, auth_headers, "idempotent-esp32")
    request = {
        "device_uid": sensor["device_uid"],
        "event_id": "00112233445566778899aabbccddeeff",
        "event_type": "pir_motion",
        "occurred_at": fixed_now.isoformat(),
        "value": 1,
        "confidence": 1,
    }
    headers = {"X-Piuda-Sensor-Key": sensor["api_key"]}

    first = client.post("/api/v1/sensor-events", headers=headers, json=request)
    second = client.post("/api/v1/sensor-events", headers=headers, json=request)

    assert first.status_code == second.status_code == 202
    assert first.get_json()["duplicate"] is False
    assert second.get_json()["duplicate"] is True
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM sensor_events WHERE event_id=?",
            (request["event_id"],),
        ).fetchone()["count"]
    assert count == 1


def test_schema_v3_database_gains_event_id_without_losing_events(tmp_path):
    database_path = tmp_path / "v3.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE sensor_devices (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          device_uid TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          location TEXT NOT NULL,
          api_key_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          last_seen_at TEXT
        );
        CREATE TABLE sensor_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          device_id INTEGER NOT NULL REFERENCES sensor_devices(id) ON DELETE CASCADE,
          event_type TEXT NOT NULL,
          value REAL,
          confidence REAL,
          occurred_at TEXT NOT NULL,
          received_at TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO sensor_devices(device_uid, name, location, api_key_hash, created_at)
        VALUES ('old-device', '기존 센서', '거실', 'hash', '2026-08-12T12:00:00+09:00');
        INSERT INTO sensor_events(
          device_id, event_type, value, confidence, occurred_at, received_at, payload_json
        ) VALUES (
          1, 'heartbeat', 1, 1, '2026-08-12T12:00:00+09:00',
          '2026-08-12T12:00:00+09:00', '{}'
        );
        """
    )
    connection.commit()
    connection.close()

    init_database(database_path)

    migrated = sqlite3.connect(database_path)
    try:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(sensor_events)")}
        assert "event_id" in columns
        assert migrated.execute("SELECT COUNT(*) FROM sensor_events").fetchone()[0] == 1
        assert migrated.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()[0] == "6"
    finally:
        migrated.close()


def test_schema_v5_removes_legacy_call_data(tmp_path):
    database_path = tmp_path / "legacy-call.db"
    init_database(database_path)
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE calls (
          id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE call_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          call_id TEXT NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
          sender TEXT NOT NULL,
          kind TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        INSERT INTO calls(id, status, created_at) VALUES ('old', 'ringing', '2026-08-12T12:00:00+09:00');
        INSERT INTO call_signals(call_id, sender, kind, payload_json, created_at)
        VALUES ('old', 'user', 'offer', '{}', '2026-08-12T12:00:00+09:00');
        INSERT INTO alerts(level, title, message, created_at)
        VALUES ('danger', '보호자 통화 요청', '예전 요청', '2026-08-12T12:00:00+09:00');
        INSERT INTO alerts(level, title, message, created_at)
        VALUES ('danger', '사용자 확인 요청', '유지할 요청', '2026-08-12T12:01:00+09:00');
        """
    )
    connection.commit()
    connection.close()

    init_database(database_path)

    migrated = sqlite3.connect(database_path)
    try:
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "calls" not in tables
        assert "call_signals" not in tables
        titles = {row[0] for row in migrated.execute("SELECT title FROM alerts")}
        assert "보호자 통화 요청" not in titles
        assert "사용자 확인 요청" in titles
    finally:
        migrated.close()

from __future__ import annotations

from piuda import create_app
from piuda.db import get_db


def register_sensor(client, headers, uid="room_1"):
    response = client.post(
        "/api/v1/sensors",
        headers=headers,
        json={"device_uid": uid, "name": "거실 통합 센서", "location": "거실"},
    )
    assert response.status_code == 201
    return response.get_json()


def reading(sensor_id="room_1", *, pir=0, peak_delta=2.0, stddev=0.8, has_ir=True):
    return {
        "sensor_id": sensor_id,
        "has_ir_sensor": has_ir,
        "ambient": 24.2 if has_ir else None,
        "object": 31.7 if has_ir else None,
        "pir": pir,
        "reason": "PERIODIC",
        "csi": {
            "packet_count": 100,
            "packet_rate": 2.0,
            "rssi": -48,
            "length": 128,
            "mean_amplitude": 18.4,
            "amplitude_stddev": stddev,
            "peak_delta": peak_delta,
            "dropped_count": 0,
        },
    }


def post_reading(client, sensor, body):
    return client.post(
        "/api/v1/module-readings",
        headers={"X-Piuda-Sensor-Key": sensor["api_key"]},
        json=body,
    )


def test_module_reading_updates_latest_state_without_unbounded_raw_rows(
    app, client, auth_headers
):
    sensor = register_sensor(client, auth_headers)
    for index in range(40):
        body = reading(peak_delta=2 + index % 2)
        body["csi"]["packet_count"] = 100 + index * 2
        response = post_reading(client, sensor, body)
        assert response.status_code == 202

    with app.app_context():
        database = get_db()
        state_count = database.execute("SELECT COUNT(*) FROM sensor_module_state").fetchone()[0]
        event_count = database.execute("SELECT COUNT(*) FROM sensor_events").fetchone()[0]
        state = database.execute("SELECT * FROM sensor_module_state").fetchone()
    assert state_count == 1
    assert event_count == 2  # 첫 PIR 상태와 1분 단위 heartbeat만 기록
    assert state["csi_status"] == "stable"
    assert state["ambient_c"] == 24.2

    sensors = client.get("/api/v1/sensors", headers=auth_headers).get_json()["items"]
    assert sensors[0]["pir_state"] == 0
    assert sensors[0]["csi_packet_rate"] == 2.0
    assert sensors[0]["has_ir_sensor"] is True


def test_strong_csi_change_requires_pir_and_creates_fall_candidate(
    app, client, auth_headers
):
    sensor = register_sensor(client, auth_headers)
    for _ in range(30):
        assert post_reading(client, sensor, reading(peak_delta=2.0)).status_code == 202

    csi_only = post_reading(client, sensor, reading(pir=0, peak_delta=60, stddev=6))
    assert csi_only.status_code == 202
    assert csi_only.get_json()["csi_status"] == "strong_change"
    assert "csi_fall" not in csi_only.get_json()["events"]

    combined = post_reading(client, sensor, reading(pir=1, peak_delta=60, stddev=6))
    assert combined.status_code == 202
    assert {"pir_motion", "csi_fall"} <= set(combined.get_json()["events"])
    assert combined.get_json()["risk"]["score"] == 50

    with app.app_context():
        falls = get_db().execute(
            "SELECT confidence, payload_json FROM sensor_events WHERE event_type='csi_fall'"
        ).fetchall()
    assert len(falls) == 1
    assert falls[0]["confidence"] >= 0.65
    assert '"pir_correlated":true' in falls[0]["payload_json"]


def test_module_reading_validates_auth_and_payload(client, auth_headers):
    sensor = register_sensor(client, auth_headers)
    denied = client.post(
        "/api/v1/module-readings",
        headers={"X-Piuda-Sensor-Key": "wrong"},
        json=reading(),
    )
    assert denied.status_code == 401

    invalid = reading()
    invalid["csi"]["rssi"] = []
    response = post_reading(client, sensor, invalid)
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_request"


def test_demo_room_two_auto_registers_with_fixed_sensor_key(tmp_path, fixed_now):
    app = create_app(
        {
            "TESTING": True,
            "DATA_DIR": tmp_path,
            "DATABASE": tmp_path / "demo.db",
            "SECRET_KEY": "test-secret",
            "DEMO_MODE": True,
            "NOW_PROVIDER": lambda: fixed_now,
            "OLLAMA_URL": "http://127.0.0.1:1",
        }
    )
    client = app.test_client()
    response = client.post(
        "/api/v1/module-readings",
        headers={"X-Piuda-Sensor-Key": "piuda-demo-3017"},
        json=reading(sensor_id="room_2", has_ir=False),
    )
    assert response.status_code == 202
    with app.app_context():
        device = get_db().execute(
            "SELECT name, location FROM sensor_devices WHERE device_uid='room_2'"
        ).fetchone()
    assert dict(device) == {"name": "침실 통합 센서", "location": "침실"}


def test_health_advertises_fixed_hotspot_without_password(client):
    health = client.get("/api/v1/health").get_json()
    assert health["hotspot"] == {"ssid": "PIUDA-CNU", "gateway": "192.168.4.1"}
    assert "password" not in health["hotspot"]


def test_demo_reset_reuses_room_one_if_it_was_registered_during_reset(app):
    app.config["DEMO_MODE"] = True
    from piuda.auth import token_hash
    from piuda.cli import reset_demo
    from piuda.clock import iso

    with app.app_context():
        database = get_db()
        database.execute(
            """
            INSERT INTO sensor_devices(device_uid, name, location, api_key_hash, created_at)
            VALUES ('room_1', '임시 센서', '임시 위치', ?, ?)
            """,
            (token_hash("temporary"), iso()),
        )
        # 삭제 직후 들어온 센서 요청을 트리거로 재현합니다. 예전 INSERT는
        # 이 행과 충돌했지만 현재 시드는 같은 UID를 안전하게 갱신합니다.
        database.execute(
            """
            CREATE TRIGGER recreate_room_one_after_delete
            AFTER DELETE ON sensor_devices
            WHEN OLD.device_uid='room_1'
            BEGIN
              INSERT INTO sensor_devices(
                device_uid, name, location, api_key_hash, created_at, last_seen_at
              ) VALUES (
                'room_1', '동시 등록 센서', '임시 위치', 'temporary-hash',
                '2026-08-14T16:00:00+09:00', NULL
              );
            END
            """
        )
        database.commit()

    reset_demo(app)

    with app.app_context():
        devices = get_db().execute(
            "SELECT device_uid, name, location FROM sensor_devices"
        ).fetchall()
    assert [dict(row) for row in devices] == [
        {"device_uid": "room_1", "name": "거실 통합 센서", "location": "거실"}
    ]

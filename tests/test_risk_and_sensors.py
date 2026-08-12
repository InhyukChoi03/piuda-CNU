from datetime import timedelta


def register_sensor(client, headers, uid="livingroom-esp32"):
    response = client.post(
        "/api/v1/sensors",
        headers=headers,
        json={"device_uid": uid, "name": "거실 센서", "location": "거실 천장"},
    )
    assert response.status_code == 201
    return response.get_json()


def send_event(client, sensor, event_type, occurred_at, confidence=None, key=None):
    body = {"device_uid": sensor["device_uid"], "event_type": event_type, "occurred_at": occurred_at.isoformat()}
    if confidence is not None:
        body["confidence"] = confidence
    return client.post(
        "/api/v1/sensor-events",
        headers={"X-Piuda-Sensor-Key": key or sensor["api_key"]},
        json=body,
    )


def test_sensor_requires_api_key(client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers)
    denied = send_event(client, sensor, "pir_motion", fixed_now, key="wrong-key")
    assert denied.status_code == 401
    accepted = send_event(client, sensor, "pir_motion", fixed_now)
    assert accepted.status_code == 202
    assert accepted.get_json()["risk"]["score"] == 100


def test_csi_fall_is_danger_with_exact_weight(client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers)
    response = send_event(client, sensor, "csi_fall", fixed_now - timedelta(minutes=2), confidence=.91)
    risk = response.get_json()["risk"]
    assert risk["score"] == 50
    assert risk["level"] == "danger"
    assert [factor["code"] for factor in risk["factors"]] == ["csi_fall"]
    alerts = client.get("/api/v1/alerts", headers=auth_headers).get_json()["items"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "danger"


def test_plan_weights_accumulate_and_cap_at_100(client, auth_headers, fixed_now):
    sensor = register_sensor(client, auth_headers)
    motion = send_event(client, sensor, "pir_motion", fixed_now - timedelta(hours=4))
    assert motion.status_code == 202

    for title, category, scheduled_time in [
        ("아침 약", "medication", "09:00"),
        ("점심 식사", "meal", "12:00"),
    ]:
        response = client.post(
            "/api/v1/routines",
            headers=auth_headers,
            json={"title": title, "category": category, "scheduled_time": scheduled_time, "days_mask": 1},
        )
        assert response.status_code == 201

    risk = client.get("/api/v1/risk/current").get_json()
    codes = {factor["code"] for factor in risk["factors"]}
    assert {"medication_missed", "meal_missed", "scheduled_inactivity", "long_pir_inactivity", "missed_and_inactive"} <= codes
    assert risk["score"] == 0
    assert risk["level"] == "emergency"


def test_declared_risk_weights_match_development_plan(client):
    rules = client.get("/api/v1/risk/rules").get_json()["rules"]
    weights = {item["code"]: item["points"] for item in rules}
    assert weights == {
        "medication_missed": 20,
        "meal_missed": 15,
        "scheduled_inactivity": 25,
        "long_pir_inactivity": 30,
        "csi_fall": 50,
        "night_wandering": 20,
        "missed_and_inactive": 40,
        "sensor_offline": 25,
    }

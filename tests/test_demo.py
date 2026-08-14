from werkzeug.security import check_password_hash

from piuda.auth import setup_required
from piuda.cli import reset_demo
from piuda.db import get_db
from piuda.scheduler import today_tasks


def test_reset_demo_replaces_mutable_data(app):
    app.config["DEMO_MODE"] = True
    reset_demo(app)

    with app.app_context():
        profile = get_db().execute("SELECT * FROM profile WHERE id=1").fetchone()
        tasks = today_tasks()
        messages = get_db().execute("SELECT COUNT(*) AS count FROM feedback_messages").fetchone()
        caregiver = get_db().execute("SELECT pin_hash FROM caregivers").fetchone()

        assert profile["user_name"] == "김피움"
        assert len(tasks) == 5
        assert sum(task["status"] == "completed" for task in tasks) == 2
        assert messages["count"] == 0
        assert check_password_hash(caregiver["pin_hash"], "3017")
        assert setup_required() is False


def test_reset_demo_revokes_old_pin_token_and_browser_session(app, client):
    setup = client.post("/api/v1/auth/setup", json={"name": "기존 보호자", "pin": "2468"})
    old_token = setup.get_json()["token"]
    assert client.get("/api/v1/dashboard").status_code == 200

    app.config["DEMO_MODE"] = True
    reset_demo(app)

    assert client.get("/api/v1/dashboard").status_code == 401
    assert client.get(
        "/api/v1/dashboard",
        headers={"Authorization": f"Bearer {old_token}"},
    ).status_code == 401
    assert client.post("/api/v1/auth/login", json={"pin": "2468"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"pin": "3017"}).status_code == 200
    assert client.get("/api/v1/dashboard").status_code == 200

    reset_demo(app)
    assert client.get("/api/v1/dashboard").status_code == 401


def test_demo_mode_disables_caregiver_pin_setup(app, client):
    app.config["DEMO_MODE"] = True
    response = client.post("/api/v1/auth/setup", json={"name": "보호자", "pin": "9999"})
    assert response.status_code == 409
    assert response.get_json()["error"] == "demo_mode_fixed_pin"


def test_demo_console_is_local_and_demo_only(app, client):
    assert client.get("/demo").status_code == 404

    app.config["DEMO_MODE"] = True
    reset_demo(app)
    assert client.get("/demo").status_code == 200
    denied = client.get("/api/v1/demo/scenarios", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
    assert denied.status_code == 403


def test_demo_catalog_contains_every_presentation_scenario(app, client):
    app.config["DEMO_MODE"] = True
    reset_demo(app)
    result = client.get("/api/v1/demo/scenarios").get_json()
    assert len(result["items"]) == 12
    assert {item["key"] for item in result["items"]} == {
        "normal", "medication_reminder", "medication_done", "all_completed",
        "inactivity_check", "inactivity_ok", "inactivity_no_response",
        "sensor_offline", "fall", "emergency", "recovered", "caregiver_alert",
    }
    for item in result["items"]:
        triggered = client.post(f"/api/v1/demo/scenarios/{item['key']}")
        assert triggered.status_code == 200, item["key"]
        assert triggered.get_json()["active"]["scenario_key"] == item["key"]


def test_demo_scenarios_change_all_three_screens_without_revoking_login(app, client):
    app.config["DEMO_MODE"] = True
    reset_demo(app)
    login = client.post("/api/v1/auth/login", json={"pin": "3017"})
    token = login.get_json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    cases = {
        "medication_reminder": (100, "normal", 0, 0),
        "inactivity_check": (70, "caution", 0, 0),
        "inactivity_no_response": (30, "danger", 0, 1),
        "fall": (50, "danger", 0, 1),
        "emergency": (0, "emergency", 2, 1),
        "all_completed": (100, "normal", 0, 0),
    }
    for key, (score, level, missed, alerts) in cases.items():
        triggered = client.post(f"/api/v1/demo/scenarios/{key}")
        assert triggered.status_code == 200
        snapshot = triggered.get_json()
        assert snapshot["active"]["scenario_key"] == key
        assert snapshot["active"]["risk_score"] == score
        assert snapshot["active"]["risk_level"] == level
        assert snapshot["tasks"]["missed"] == missed
        assert snapshot["open_alerts"] == alerts

        dashboard = client.get("/api/v1/dashboard", headers=headers)
        assert dashboard.status_code == 200
        assert dashboard.get_json()["risk"]["score"] == score

    completed = client.get("/api/v1/tasks/today").get_json()
    assert completed["summary"]["completed"] == 5


def test_user_can_alert_caregiver_without_duplicates(app, client, monkeypatch):
    app.config["DEMO_MODE"] = True
    reset_demo(app)
    sent = []
    monkeypatch.setattr("piuda.api.send_kakao_alert", sent.append)

    first = client.post("/api/v1/caregiver-alert")
    second = client.post("/api/v1/caregiver-alert")
    assert first.status_code == 201
    assert first.get_json()["created"] is True
    assert second.status_code == 200
    assert second.get_json()["created"] is False
    assert len(sent) == 1

    login = client.post("/api/v1/auth/login", json={"pin": "3017"})
    headers = {"Authorization": f"Bearer {login.get_json()['token']}"}
    alerts = client.get("/api/v1/alerts", headers=headers).get_json()["items"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "danger"
    assert alerts[0]["title"] == "사용자 확인 요청"
    assert "김피움님" in alerts[0]["message"]


def test_removed_call_endpoints_and_tables_are_absent(app, client):
    app.config["DEMO_MODE"] = True
    reset_demo(app)
    assert client.post("/api/v1/caregiver-call").status_code == 404
    assert client.get("/api/v1/calls/current").status_code == 404
    with app.app_context():
        tables = {
            row["name"]
            for row in get_db().execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "calls" not in tables
    assert "call_signals" not in tables


def test_wellness_check_escalates_only_after_no_response(app, client):
    app.config["DEMO_MODE"] = True
    reset_demo(app)
    client.post("/api/v1/demo/scenarios/inactivity_check")
    assert client.get("/api/v1/demo/scenarios").get_json()["open_alerts"] == 0
    response = client.post("/api/v1/wellness-check", json={"answer": "timeout"})
    assert response.status_code == 200
    assert response.get_json()["active"]["scenario_key"] == "inactivity_no_response"
    assert response.get_json()["open_alerts"] == 1


def test_unknown_demo_scenario_is_rejected(app, client):
    app.config["DEMO_MODE"] = True
    reset_demo(app)
    response = client.post("/api/v1/demo/scenarios/not-real")
    assert response.status_code == 404

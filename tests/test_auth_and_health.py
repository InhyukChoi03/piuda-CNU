def test_health_and_onboarding(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.get_json()["service"] == "piuda-care"
    assert client.get("/api/v1/onboarding").get_json()["setup_required"] is True


def test_caregiver_setup_login_and_protected_dashboard(client):
    assert client.get("/api/v1/dashboard").status_code == 401

    setup = client.post("/api/v1/auth/setup", json={"name": "보호자", "pin": "2468"})
    assert setup.status_code == 201
    assert setup.get_json()["token"]
    assert client.post("/api/v1/auth/setup", json={"name": "중복", "pin": "1357"}).status_code == 409
    assert client.post("/api/v1/auth/login", json={"pin": "0000"}).status_code == 401

    login = client.post("/api/v1/auth/login", json={"pin": "2468", "device_name": "iPhone"})
    token = login.get_json()["token"]
    dashboard = client.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert dashboard.status_code == 200
    assert dashboard.get_json()["risk"]["level"] == "normal"


def test_pin_must_be_numeric(client):
    response = client.post("/api/v1/auth/setup", json={"name": "보호자", "pin": "abcd"})
    assert response.status_code == 400

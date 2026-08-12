def test_create_materialize_and_complete_missed_task(client, auth_headers):
    created = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "아침 약", "category": "medication", "scheduled_time": "09:00", "days_mask": 1},
    )
    assert created.status_code == 201

    today = client.get("/api/v1/tasks/today").get_json()
    assert today["summary"] == {"total": 1, "completed": 0, "missed": 1}
    assert today["items"][0]["status"] == "missed"

    task_id = today["items"][0]["id"]
    completed = client.post(f"/api/v1/tasks/{task_id}/complete", json={"note": "복용 확인"})
    assert completed.status_code == 200
    today = client.get("/api/v1/tasks/today").get_json()
    assert today["summary"]["completed"] == 1
    assert today["items"][0]["note"] == "복용 확인"


def test_invalid_time_is_rejected(client, auth_headers):
    response = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "잘못된 일정", "category": "meal", "scheduled_time": "25:90"},
    )
    assert response.status_code == 400

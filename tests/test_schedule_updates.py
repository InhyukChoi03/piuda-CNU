from __future__ import annotations


def _create_today_routine(client, auth_headers, *, scheduled_time: str = "09:00") -> dict:
    response = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={
            "title": "수정할 일정",
            "category": "other",
            "scheduled_time": scheduled_time,
            "days_mask": 1,
        },
    )
    assert response.status_code == 201
    return response.get_json()


def test_editing_completed_routine_moves_the_occurrence_without_duplicating_it(
    client, auth_headers
):
    routine = _create_today_routine(client, auth_headers)
    task = client.get("/api/v1/tasks/today").get_json()["items"][0]
    assert client.post(f"/api/v1/tasks/{task['id']}/complete", json={}).status_code == 200

    updated = client.put(
        f"/api/v1/routines/{routine['id']}",
        headers=auth_headers,
        json={"scheduled_time": "20:00"},
    )

    assert updated.status_code == 200
    items = client.get("/api/v1/tasks/today").get_json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "completed"
    assert items[0]["scheduled_time"] == "20:00"
    assert "T20:00:00" in items[0]["due_at"]


def test_disabling_a_pending_routine_removes_its_today_occurrence(client, auth_headers):
    routine = _create_today_routine(client, auth_headers, scheduled_time="20:00")
    assert len(client.get("/api/v1/tasks/today").get_json()["items"]) == 1

    updated = client.put(
        f"/api/v1/routines/{routine['id']}",
        headers=auth_headers,
        json={"active": False},
    )

    assert updated.status_code == 200
    assert client.get("/api/v1/tasks/today").get_json()["items"] == []


def test_schedule_time_requires_zero_padded_24_hour_format(client, auth_headers):
    response = client.post(
        "/api/v1/routines",
        headers=auth_headers,
        json={"title": "형식 검사", "category": "other", "scheduled_time": "9:00"},
    )
    assert response.status_code == 400

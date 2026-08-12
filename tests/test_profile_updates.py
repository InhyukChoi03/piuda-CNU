from __future__ import annotations


def test_partial_profile_update_preserves_unspecified_fields(client, auth_headers):
    first = client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={
            "user_name": "김피움",
            "birth_year": 1952,
            "caregiver_name": "김보호",
            "caregiver_phone": "010-1234-5678",
            "locale": "ko-KR",
        },
    )
    assert first.status_code == 200

    second = client.put(
        "/api/v1/profile",
        headers=auth_headers,
        json={"caregiver_phone": "010-9999-0000"},
    )

    assert second.status_code == 200
    profile = second.get_json()
    assert profile["user_name"] == "김피움"
    assert profile["birth_year"] == 1952
    assert profile["caregiver_name"] == "김보호"
    assert profile["caregiver_phone"] == "010-9999-0000"
    assert profile["locale"] == "ko-KR"

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading

import pytest

from piuda.calls import CallConflictError, update_call_status
from piuda.db import get_db


def start_call(client, *, replace: bool = False) -> dict:
    response = client.post(
        "/api/v1/caregiver-call",
        json={"replace": True} if replace else {},
    )
    assert response.status_code == 201
    return response.get_json()["call"]


def set_call_status(client, call_id: str, status: str, headers=None):
    return client.post(
        f"/api/v1/calls/{call_id}/status",
        json={"status": status},
        headers=headers or {},
    )


def post_signal(client, call_id: str, *, sender: str, kind: str, payload: dict, headers=None):
    return client.post(
        f"/api/v1/calls/{call_id}/signals",
        json={"sender": sender, "kind": kind, "signal": payload},
        headers=headers or {},
    )


def signals_for(client, call_id: str, recipient: str, headers=None) -> dict:
    response = client.get(
        f"/api/v1/calls/{call_id}/signals?recipient={recipient}&after=0",
        headers=headers or {},
    )
    assert response.status_code == 200
    return response.get_json()


def description(kind: str, label: str) -> dict:
    return {
        "type": kind,
        "sdp": (
            "v=0\r\n"
            "o=- 0 0 IN IP4 127.0.0.1\r\n"
            f"s=Piuda {label}\r\n"
            "t=0 0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
        ),
    }


def exchange_descriptions(client, call_id: str, auth_headers) -> tuple[dict, dict]:
    offer = description("offer", f"offer-{call_id}")
    answer = description("answer", f"answer-{call_id}")
    assert post_signal(
        client, call_id, sender="user", kind="offer", payload=offer
    ).status_code == 201
    assert post_signal(
        client,
        call_id,
        sender="caregiver",
        kind="answer",
        payload=answer,
        headers=auth_headers,
    ).status_code == 201
    return offer, answer


def test_call_transitions_from_ringing_to_active_to_ended(client, auth_headers):
    call = start_call(client)
    exchange_descriptions(client, call["id"], auth_headers)

    active = set_call_status(client, call["id"], "active", auth_headers)
    assert active.status_code == 200
    assert active.get_json()["call"]["status"] == "active"
    assert active.get_json()["call"]["answered_at"] is not None

    ended = set_call_status(client, call["id"], "ended")
    assert ended.status_code == 200
    assert ended.get_json()["call"]["status"] == "ended"
    assert ended.get_json()["call"]["ended_at"] is not None
    assert client.get("/api/v1/calls/current").get_json()["call"] is None


@pytest.mark.parametrize("terminal_status", ["declined", "ended", "missed"])
def test_terminal_call_cannot_be_reactivated(client, auth_headers, terminal_status):
    call = start_call(client)
    terminal = set_call_status(client, call["id"], terminal_status, auth_headers)
    assert terminal.status_code == 200
    assert terminal.get_json()["call"]["status"] == terminal_status

    late_answer = set_call_status(client, call["id"], "active", auth_headers)
    assert late_answer.status_code == 409

    stored = signals_for(client, call["id"], "user")["call"]
    assert stored["status"] == terminal_status
    assert stored["answered_at"] is None


def test_first_terminal_status_wins_over_later_terminal_updates(client, auth_headers):
    call = start_call(client)
    declined = set_call_status(client, call["id"], "declined", auth_headers)
    assert declined.status_code == 200

    retried_as_ended = set_call_status(client, call["id"], "ended")
    assert retried_as_ended.status_code == 200
    assert retried_as_ended.get_json()["call"]["status"] == "declined"


def test_signals_are_rejected_after_call_ends(app, client, auth_headers):
    call = start_call(client)
    accepted = post_signal(
        client,
        call["id"],
        sender="user",
        kind="offer",
        payload=description("offer", "before-end"),
    )
    assert accepted.status_code == 201
    assert set_call_status(client, call["id"], "ended").status_code == 200

    rejected = post_signal(
        client,
        call["id"],
        sender="caregiver",
        kind="answer",
        payload=description("answer", "after-end"),
        headers=auth_headers,
    )
    assert rejected.status_code == 409

    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM call_signals WHERE call_id=?",
            (call["id"],),
        ).fetchone()["count"]
    assert count == 1


def test_signal_poll_returns_the_requested_call_not_the_current_call(client, auth_headers):
    first = start_call(client)
    first_offer = description("offer", "first-call")
    assert post_signal(
        client,
        first["id"],
        sender="user",
        kind="offer",
        payload=first_offer,
    ).status_code == 201
    assert set_call_status(client, first["id"], "ended").status_code == 200

    second = start_call(client)
    assert second["id"] != first["id"]
    assert client.get("/api/v1/calls/current").get_json()["call"]["id"] == second["id"]

    first_poll = signals_for(client, first["id"], "caregiver", auth_headers)
    assert first_poll["call"]["id"] == first["id"]
    assert first_poll["call"]["status"] == "ended"
    assert [item["payload"] for item in first_poll["items"]] == [first_offer]


def test_second_call_gets_a_new_id_and_does_not_reuse_signals(client, auth_headers):
    first = start_call(client)
    first_offer = description("offer", "offer-for-first")
    assert post_signal(
        client,
        first["id"],
        sender="user",
        kind="offer",
        payload=first_offer,
    ).status_code == 201
    assert set_call_status(client, first["id"], "ended").status_code == 200

    second = start_call(client)
    second_offer = description("offer", "offer-for-second")
    assert second["id"] != first["id"]
    assert signals_for(client, second["id"], "caregiver", auth_headers)["items"] == []
    assert post_signal(
        client,
        second["id"],
        sender="user",
        kind="offer",
        payload=second_offer,
    ).status_code == 201

    assert [
        item["payload"] for item in signals_for(client, first["id"], "caregiver", auth_headers)["items"]
    ] == [first_offer]
    assert [
        item["payload"] for item in signals_for(client, second["id"], "caregiver", auth_headers)["items"]
    ] == [second_offer]


def test_active_call_is_not_expired_by_the_ringing_timeout(app, client, auth_headers, fixed_now):
    call = start_call(client)
    exchange_descriptions(client, call["id"], auth_headers)
    assert set_call_status(client, call["id"], "active", auth_headers).status_code == 200

    old_created_at = (fixed_now - timedelta(hours=8)).isoformat(timespec="seconds")
    with app.app_context():
        database = get_db()
        database.execute(
            "UPDATE calls SET created_at=? WHERE id=?",
            (old_created_at, call["id"]),
        )
        database.commit()

    current = client.get("/api/v1/calls/current")
    assert current.status_code == 200
    assert current.get_json()["call"]["id"] == call["id"]
    assert current.get_json()["call"]["status"] == "active"


def test_demo_call_scene_does_not_create_an_orphan_call(app, client):
    app.config["DEMO_MODE"] = True

    triggered = client.post("/api/v1/demo/scenarios/caregiver_call")
    assert triggered.status_code == 200
    assert triggered.get_json()["active"]["scenario_key"] == "caregiver_call"
    assert client.get("/api/v1/calls/current").get_json()["call"] is None

    with app.app_context():
        database = get_db()
        calls = database.execute("SELECT COUNT(*) AS count FROM calls").fetchone()["count"]
        signals = database.execute(
            "SELECT COUNT(*) AS count FROM call_signals"
        ).fetchone()["count"]
        call_alerts = database.execute(
            "SELECT COUNT(*) AS count FROM alerts WHERE title='보호자 통화 요청'"
        ).fetchone()["count"]

    assert calls == 0
    assert signals == 0
    assert call_alerts == 0


@pytest.mark.parametrize(
    ("sender", "kind", "headers"),
    [
        ("caregiver", "offer", "auth"),
        ("user", "answer", None),
    ],
)
def test_offer_and_answer_enforce_sender_roles(
    app, client, auth_headers, sender, kind, headers
):
    call = start_call(client)
    rejected = post_signal(
        client,
        call["id"],
        sender=sender,
        kind=kind,
        payload=description(kind, "wrong-role"),
        headers=auth_headers if headers else None,
    )
    assert rejected.status_code == 400

    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM call_signals WHERE call_id=?",
            (call["id"],),
        ).fetchone()["count"]
    assert count == 0
    assert client.get("/api/v1/calls/current").get_json()["call"]["offer_ready"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "offer", "sdp": "not-an-sdp"},
        {"type": "answer", "sdp": description("offer", "wrong-type")["sdp"]},
        {"type": "offer", "sdp": ""},
        {"type": "offer", "sdp": "v=0\r\ns=no-audio\r\n"},
    ],
)
def test_malformed_offer_is_rejected_without_making_call_ready(app, client, payload):
    call = start_call(client)
    rejected = post_signal(
        client,
        call["id"],
        sender="user",
        kind="offer",
        payload=payload,
    )
    assert rejected.status_code == 400
    assert client.get("/api/v1/calls/current").get_json()["call"]["offer_ready"] is False

    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM call_signals WHERE call_id=?",
            (call["id"],),
        ).fetchone()["count"]
    assert count == 0


def test_answer_requires_offer_and_active_requires_answer(client, auth_headers):
    call = start_call(client)
    early_answer = post_signal(
        client,
        call["id"],
        sender="caregiver",
        kind="answer",
        payload=description("answer", "too-early"),
        headers=auth_headers,
    )
    assert early_answer.status_code == 409

    offer = description("offer", "waiting-for-answer")
    assert post_signal(
        client, call["id"], sender="user", kind="offer", payload=offer
    ).status_code == 201
    active_without_answer = set_call_status(client, call["id"], "active", auth_headers)
    assert active_without_answer.status_code == 409
    assert client.get("/api/v1/calls/current").get_json()["call"]["status"] == "ringing"


def test_description_retries_are_idempotent_but_changed_sdp_conflicts(
    app, client
):
    call = start_call(client)
    offer = description("offer", "retry")
    first = post_signal(
        client, call["id"], sender="user", kind="offer", payload=offer
    )
    retried = post_signal(
        client, call["id"], sender="user", kind="offer", payload=dict(offer)
    )
    assert first.status_code == 201
    assert retried.status_code == 201
    assert retried.get_json()["item"]["id"] == first.get_json()["item"]["id"]

    changed = post_signal(
        client,
        call["id"],
        sender="user",
        kind="offer",
        payload=description("offer", "changed"),
    )
    assert changed.status_code == 409
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM call_signals WHERE call_id=?",
            (call["id"],),
        ).fetchone()["count"]
    assert count == 1


def test_invalid_ice_shape_is_rejected(client):
    call = start_call(client)
    rejected = post_signal(
        client,
        call["id"],
        sender="user",
        kind="ice",
        payload={"candidate": 42, "sdpMLineIndex": True},
    )
    assert rejected.status_code == 400


def test_ice_retry_is_idempotent(app, client):
    call = start_call(client)
    candidate = {
        "candidate": "candidate:1 1 UDP 2122260223 192.168.0.10 50000 typ host",
        "sdpMid": "0",
        "sdpMLineIndex": 0,
        "usernameFragment": "piuda",
    }
    first = post_signal(
        client, call["id"], sender="user", kind="ice", payload=candidate
    )
    retried = post_signal(
        client, call["id"], sender="user", kind="ice", payload=dict(candidate)
    )
    assert first.status_code == 201
    assert retried.status_code == 201
    assert retried.get_json()["item"]["id"] == first.get_json()["item"]["id"]
    with app.app_context():
        count = get_db().execute(
            "SELECT COUNT(*) AS count FROM call_signals WHERE call_id=? AND kind='ice'",
            (call["id"],),
        ).fetchone()["count"]
    assert count == 1


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("null", "application/json"),
        ("[]", "application/json"),
        ("{", "application/json"),
        ('{"replace":"true"}', "application/json"),
        ('{"replace":2}', "application/json"),
    ],
)
def test_call_start_rejects_invalid_optional_json_without_replacing_call(
    client, body, content_type
):
    existing = start_call(client)
    rejected = client.post(
        "/api/v1/caregiver-call", data=body, content_type=content_type
    )
    assert rejected.status_code == 400
    current = client.get("/api/v1/calls/current").get_json()["call"]
    assert current["id"] == existing["id"]
    assert current["status"] == "ringing"


def test_concurrent_replace_calls_leave_exactly_one_open_call(app):
    workers = 6
    start_together = threading.Barrier(workers)

    def replace_call(_):
        with app.app_context():
            import piuda.calls as calls_module

            start_together.wait(timeout=5)
            call, created = calls_module.start_call(replace_existing=True)
            return call["id"], created

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(replace_call, range(workers)))

    assert all(created for _, created in outcomes)
    assert len({call_id for call_id, _ in outcomes}) == workers
    with app.app_context():
        database = get_db()
        open_calls = database.execute(
            "SELECT id FROM calls WHERE status IN ('ringing','active')"
        ).fetchall()
        ended_calls = database.execute(
            "SELECT COUNT(*) AS count FROM calls WHERE status='ended'"
        ).fetchone()["count"]
    assert len(open_calls) == 1
    assert ended_calls == workers - 1


def test_concurrent_end_cannot_be_overwritten_by_late_active(
    app, client, auth_headers, monkeypatch
):
    call = start_call(client)
    exchange_descriptions(client, call["id"], auth_headers)

    import piuda.calls as calls_module

    original_call_by_id = calls_module.call_by_id
    start_together = threading.Barrier(2)
    per_thread = threading.local()

    def synchronized_first_read(call_id):
        result = original_call_by_id(call_id)
        if threading.current_thread() is not threading.main_thread() and not getattr(
            per_thread, "waited", False
        ):
            per_thread.waited = True
            start_together.wait(timeout=5)
        return result

    monkeypatch.setattr(calls_module, "call_by_id", synchronized_first_read)

    def transition(status):
        with app.app_context():
            try:
                result = update_call_status(call["id"], status)
                return result["status"]
            except CallConflictError:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(transition, ("active", "ended")))

    assert "ended" in outcomes
    with app.app_context():
        stored = get_db().execute(
            "SELECT status FROM calls WHERE id=?", (call["id"],)
        ).fetchone()
    assert stored["status"] == "ended"

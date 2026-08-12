from __future__ import annotations

import json
import secrets
from datetime import timedelta

from .clock import iso, now, parse_iso
from .db import get_db


OPEN_STATUSES = {"ringing", "active"}
FINAL_STATUSES = {"declined", "ended", "missed"}
SIGNAL_KINDS = {"offer", "answer", "ice"}
ROLES = {"user", "caregiver"}
DESCRIPTION_SENDERS = {"offer": "user", "answer": "caregiver"}
RINGING_TIMEOUT = timedelta(minutes=2)


class CallConflictError(ValueError):
    """Raised when a signaling request conflicts with the call state."""


def _with_signal_state(row) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    result["offer_ready"] = get_db().execute(
        "SELECT 1 FROM call_signals WHERE call_id=? AND sender='user' AND kind='offer' LIMIT 1",
        (result["id"],),
    ).fetchone() is not None
    return result


def call_by_id(call_id: str) -> dict | None:
    row = get_db().execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
    return _with_signal_state(row)


def _expire_ringing_calls(database) -> None:
    # 벨이 울리는 요청만 만료시킵니다. 통화가 연결된 뒤에는 생성 시각을
    # 기준으로 끊으면 긴 통화가 예고 없이 종료될 수 있습니다.
    ringing = database.execute(
        "SELECT id, created_at FROM calls WHERE status='ringing'"
    ).fetchall()
    expired = [row["id"] for row in ringing if now() - parse_iso(row["created_at"]) > RINGING_TIMEOUT]
    if expired:
        placeholders = ",".join("?" for _ in expired)
        database.execute(
            f"""
            UPDATE calls SET status='missed', ended_at=?
            WHERE status='ringing' AND id IN ({placeholders})
            """,
            (iso(), *expired),
        )


def _open_call_row(database):
    return database.execute(
        """
        SELECT * FROM calls WHERE status IN ('ringing','active')
        ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, created_at DESC LIMIT 1
        """
    ).fetchone()


def current_call() -> dict | None:
    database = get_db()
    _expire_ringing_calls(database)
    if database.in_transaction:
        database.commit()
    return _with_signal_state(_open_call_row(database))


def start_call(*, replace_existing: bool = False) -> tuple[dict, bool]:
    database = get_db()
    # 동시에 두 화면이 replace 요청을 보내더라도 만료 처리,
    # 기존 통화 종료, 새 통화 생성을 하나의 쓰기 임계 구역으로
    # 묶어 열려 있는 통화가 둘 이상 남지 않게 합니다.
    database.execute("BEGIN IMMEDIATE")
    try:
        _expire_ringing_calls(database)
        existing_row = _open_call_row(database)
        if existing_row is not None and not replace_existing:
            database.commit()
            return call_by_id(existing_row["id"]), False
        if existing_row is not None:
            # 브라우저가 새로고침되거나 직전 협상이 실패한 경우, 낡은 SDP/ICE를
            # 같은 call id에서 다시 읽지 않도록 명시적으로 새 통화를 만듭니다.
            database.execute(
                "UPDATE calls SET status='ended', ended_at=? WHERE status IN ('ringing','active')",
                (iso(),),
            )
        call_id = secrets.token_urlsafe(12)
        database.execute(
            "INSERT INTO calls(id, status, created_at) VALUES (?, 'ringing', ?)",
            (call_id, iso()),
        )
        database.commit()
    except Exception:
        database.rollback()
        raise
    return call_by_id(call_id), True


def update_call_status(call_id: str, status: str) -> dict | None:
    if status not in {"active", *FINAL_STATUSES}:
        raise ValueError("지원하지 않는 통화 상태입니다.")
    database = get_db()
    # 상태를 읽고 바로 덮어쓰면 `ended` 와 `active` 요청이 겹칠 때
    # 나중에 실행된 active가 종료된 통화를 되살릴 수 있습니다. 읽은
    # 상태를 WHERE 조건에 포함하고, 패배하면 최신 상태로 전이를 다시
    # 판단해 종료 상태가 항상 우선하도록 합니다.
    for _ in range(4):
        existing = call_by_id(call_id)
        if existing is None:
            return None
        current_status = existing["status"]
        if status == current_status:
            return existing
        if current_status in FINAL_STATUSES:
            # 종료 요청은 네트워크 재시도에 안전하도록 멱등 처리합니다.
            if status in FINAL_STATUSES:
                return existing
            raise CallConflictError("이미 종료된 통화입니다.")
        if current_status == "active" and status != "ended":
            raise CallConflictError("연결된 통화는 종료만 할 수 있습니다.")
        if status == "active":
            answer = database.execute(
                """
                SELECT 1 FROM call_signals
                WHERE call_id=? AND sender='caregiver' AND kind='answer' LIMIT 1
                """,
                (call_id,),
            ).fetchone()
            if answer is None:
                raise CallConflictError("보호자 응답 신호가 없어 통화를 연결할 수 없습니다.")

        answered_at = iso() if status == "active" else None
        ended_at = iso() if status in FINAL_STATUSES else None
        cursor = database.execute(
            """
            UPDATE calls SET
              status=?,
              answered_at=CASE WHEN ? IS NOT NULL THEN COALESCE(answered_at, ?) ELSE answered_at END,
              ended_at=CASE WHEN ? IS NOT NULL THEN ? ELSE ended_at END
            WHERE id=? AND status=?
            """,
            (
                status,
                answered_at,
                answered_at,
                ended_at,
                ended_at,
                call_id,
                current_status,
            ),
        )
        database.commit()
        if cursor.rowcount:
            return call_by_id(call_id)
    raise CallConflictError("통화 상태가 동시에 변경되어 요청을 완료하지 못했습니다.")


def _validate_signal_payload(sender: str, kind: str, signal: dict) -> None:
    expected_sender = DESCRIPTION_SENDERS.get(kind)
    if expected_sender is not None and sender != expected_sender:
        role_label = "사용자" if expected_sender == "user" else "보호자"
        raise ValueError(f"{kind} 신호는 {role_label}만 보낼 수 있습니다.")

    if kind in DESCRIPTION_SENDERS:
        if signal.get("type") != kind:
            raise ValueError(f"{kind} 신호의 type이 일치하지 않습니다.")
        sdp = signal.get("sdp")
        if not isinstance(sdp, str) or not sdp.strip():
            raise ValueError(f"{kind} 신호에 SDP 문자열이 필요합니다.")
        lines = sdp.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if lines[0].strip() != "v=0" or not any(line.startswith("m=audio ") for line in lines):
            raise ValueError("올바른 오디오 SDP 형식이 아닙니다.")
        return

    candidate = signal.get("candidate")
    if not isinstance(candidate, str):
        raise ValueError("ICE 신호에 candidate 문자열이 필요합니다.")
    sdp_mid = signal.get("sdpMid")
    if sdp_mid is not None and not isinstance(sdp_mid, str):
        raise ValueError("ICE sdpMid는 문자열 또는 null이어야 합니다.")
    line_index = signal.get("sdpMLineIndex")
    if line_index is not None and (
        not isinstance(line_index, int) or isinstance(line_index, bool) or line_index < 0
    ):
        raise ValueError("ICE sdpMLineIndex는 0 이상의 정수 또는 null이어야 합니다.")
    username_fragment = signal.get("usernameFragment")
    if username_fragment is not None and not isinstance(username_fragment, str):
        raise ValueError("ICE usernameFragment는 문자열 또는 null이어야 합니다.")


def _signal_result(row) -> dict:
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def add_signal(call_id: str, sender: str, kind: str, signal: dict) -> dict | None:
    if sender not in ROLES or kind not in SIGNAL_KINDS:
        raise ValueError("올바르지 않은 통화 신호입니다.")
    if not isinstance(signal, dict):
        raise ValueError("통화 신호 객체가 필요합니다.")
    _validate_signal_payload(sender, kind, signal)
    encoded = json.dumps(signal, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 32 * 1024:
        raise ValueError("통화 신호가 너무 큽니다.")
    database = get_db()
    # 종료와 신호 추가, 동일 SDP 재시도가 서로 겹쳐도 판단과
    # 쓰기가 하나의 임계 구역에서 이루어지도록 합니다.
    database.execute("BEGIN IMMEDIATE")
    try:
        call = database.execute("SELECT status FROM calls WHERE id=?", (call_id,)).fetchone()
        if call is None:
            database.rollback()
            return None
        if call["status"] not in OPEN_STATUSES:
            raise CallConflictError("이미 종료된 통화에는 신호를 보낼 수 없습니다.")

        if kind == "answer":
            offer = database.execute(
                """
                SELECT 1 FROM call_signals
                WHERE call_id=? AND sender='user' AND kind='offer' LIMIT 1
                """,
                (call_id,),
            ).fetchone()
            if offer is None:
                raise CallConflictError("응답할 통화 요청 신호가 없습니다.")

        if kind in DESCRIPTION_SENDERS:
            previous = database.execute(
                """
                SELECT * FROM call_signals
                WHERE call_id=? AND sender=? AND kind=? ORDER BY id LIMIT 1
                """,
                (call_id, sender, kind),
            ).fetchone()
            if previous is not None:
                if json.loads(previous["payload_json"]) == signal:
                    database.commit()
                    return _signal_result(previous)
                raise CallConflictError(f"이미 다른 {kind} 신호가 등록되어 있습니다.")
        else:
            # ICE POST 응답만 유실된 경우 같은 candidate를 재전송해도
            # 수신쪽이 중복 candidate를 적용하지 않도록 멱등 처리합니다.
            previous = database.execute(
                """
                SELECT * FROM call_signals
                WHERE call_id=? AND sender=? AND kind='ice' AND payload_json=?
                ORDER BY id LIMIT 1
                """,
                (call_id, sender, encoded),
            ).fetchone()
            if previous is not None:
                database.commit()
                return _signal_result(previous)

        cursor = database.execute(
            """
            INSERT INTO call_signals(call_id, sender, kind, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (call_id, sender, kind, encoded, iso()),
        )
        row = database.execute(
            "SELECT * FROM call_signals WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        database.commit()
        return _signal_result(row)
    except Exception:
        database.rollback()
        raise


def signals_for(call_id: str, recipient: str, after: int = 0) -> list[dict]:
    if recipient not in ROLES:
        raise ValueError("올바르지 않은 통화 수신자입니다.")
    rows = get_db().execute(
        """
        SELECT * FROM call_signals
        WHERE call_id=? AND sender<>? AND id>?
        ORDER BY id LIMIT 100
        """,
        (call_id, recipient, max(0, after)),
    ).fetchall()
    items = []
    for row in rows:
        items.append(_signal_result(row))
    return items

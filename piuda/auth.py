from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import timedelta
from functools import wraps
from threading import Lock
from time import monotonic

from flask import jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .clock import iso, now, parse_iso
from .db import get_db


_LOGIN_ATTEMPTS: dict[str, tuple[int, float]] = {}
_LOGIN_ATTEMPTS_LOCK = Lock()
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_FAILURES = 8


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def setup_required() -> bool:
    row = get_db().execute("SELECT 1 FROM caregivers LIMIT 1").fetchone()
    return row is None


def is_private_request() -> bool:
    remote = request.remote_addr or ""
    try:
        return ipaddress.ip_address(remote).is_private or ipaddress.ip_address(remote).is_loopback
    except ValueError:
        return False


def create_caregiver(name: str, pin: str) -> None:
    if not 4 <= len(pin) <= 12 or not pin.isdigit():
        raise ValueError("PIN은 숫자 4~12자리여야 합니다.")
    database = get_db()
    database.execute(
        "INSERT INTO caregivers(name, pin_hash, created_at) VALUES (?, ?, ?)",
        (name.strip() or "보호자", generate_password_hash(pin), iso()),
    )
    database.commit()


def login_with_pin(pin: str, device_name: str = "iOS") -> str | None:
    identity = request.remote_addr or "unknown"
    current_tick = monotonic()
    with _LOGIN_ATTEMPTS_LOCK:
        failures, window_started = _LOGIN_ATTEMPTS.get(identity, (0, current_tick))
        if current_tick - window_started >= _LOGIN_WINDOW_SECONDS:
            failures, window_started = 0, current_tick
        if failures >= _LOGIN_MAX_FAILURES:
            raise ValueError("PIN 입력을 너무 많이 시도했습니다. 잠시 후 다시 시도해 주세요.")
    row = get_db().execute(
        "SELECT id, pin_hash FROM caregivers ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None or not check_password_hash(row["pin_hash"], pin):
        with _LOGIN_ATTEMPTS_LOCK:
            _LOGIN_ATTEMPTS[identity] = (failures + 1, window_started)
        return None

    with _LOGIN_ATTEMPTS_LOCK:
        _LOGIN_ATTEMPTS.pop(identity, None)

    session["caregiver_id"] = row["id"]
    # PIN을 다시 생성하면 같은 caregiver id를 재사용하더라도 기존
    # 브라우저 세션이 인증을 통과하지 못하게 합니다.
    session["caregiver_auth_version"] = token_hash(row["pin_hash"])
    token = secrets.token_urlsafe(32)
    database = get_db()
    database.execute(
        "INSERT INTO api_tokens(name, role, token_hash, created_at) VALUES (?, 'caregiver', ?, ?)",
        (device_name[:80], token_hash(token), iso()),
    )
    database.commit()
    return token


def caregiver_authenticated() -> bool:
    caregiver_id = session.get("caregiver_id")
    auth_version = str(session.get("caregiver_auth_version", ""))
    if caregiver_id and auth_version:
        row = get_db().execute(
            "SELECT pin_hash FROM caregivers WHERE id = ?",
            (caregiver_id,),
        ).fetchone()
        if row is not None and secrets.compare_digest(auth_version, token_hash(row["pin_hash"])):
            return True
        session.clear()
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    candidate = header.removeprefix("Bearer ").strip()
    if not candidate:
        return False
    database = get_db()
    row = database.execute(
        "SELECT id, last_used_at FROM api_tokens WHERE token_hash = ? AND role = 'caregiver' AND revoked_at IS NULL",
        (token_hash(candidate),),
    ).fetchone()
    if row is None:
        return False
    # 2초 폴링마다 쓰기를 만들지 않도록 마지막 사용 기록은 분 단위로 제한합니다.
    last_used_at = parse_iso(row["last_used_at"]) if row["last_used_at"] else None
    if last_used_at is None or now() - last_used_at >= timedelta(minutes=1):
        database.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (iso(), row["id"]))
        database.commit()
    return True


def caregiver_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not caregiver_authenticated():
            return jsonify({"error": "caregiver_auth_required"}), 401
        return view(*args, **kwargs)

    return wrapped


def authenticate_sensor(device_uid: str, api_key: str):
    if not device_uid or not api_key:
        return None
    database = get_db()
    row = database.execute(
        "SELECT * FROM sensor_devices WHERE device_uid = ? AND api_key_hash = ?",
        (device_uid, token_hash(api_key)),
    ).fetchone()
    return row

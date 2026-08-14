from __future__ import annotations

import json
from datetime import timedelta
from ipaddress import ip_address
import secrets
import sqlite3
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request, session

from .auth import (
    authenticate_sensor,
    caregiver_required,
    create_caregiver,
    is_private_request,
    login_with_pin,
    setup_required,
    token_hash,
)
from .clock import iso, now, parse_iso
from .db import get_db
from .demo import (
    create_caregiver_request_alert,
    current_demo_state,
    scenario_catalog,
    trigger_demo_scenario,
)
from .integrations import ollama_feedback, send_kakao_alert
from .risk import LEVELS, RISK_RULES, evaluate_risk
from .scheduler import CATEGORIES, create_routine, materialize_day, today_tasks, validate_time
from .sensors import ingest_module_reading
from .stt import (
    LocalSttBusy,
    LocalSttNoSpeech,
    LocalSttUnavailable,
    local_stt_available,
    transcribe_local,
)
from .tts import local_tts_engine, speak_local_async
from .validation import boolean_value, integer_value, number_value, object_value, text_value


api = Blueprint("api", __name__, url_prefix="/api/v1")


def payload() -> dict:
    return object_value(request.get_json(silent=True), message="올바른 JSON 객체가 필요합니다.")


def optional_payload() -> dict:
    value = request.get_json(silent=True)
    if value is None:
        # 본문이 아예 없는 POST는 허용하되, 잘못된 JSON이나 JSON null을
        # 빈 객체처럼 처리하여 상태를 변경하지는 않습니다.
        if request.content_length not in {None, 0}:
            raise ValueError("올바른 JSON 객체가 필요합니다.")
        return {}
    return object_value(value)


def evaluate_and_notify() -> dict:
    risk = evaluate_risk()
    if risk.get("new_alert") and risk["level"] in {"danger", "emergency"}:
        send_kakao_alert(f"[피우다] {risk['level_label']} · 건강 점수 {risk['score']}점")
    return risk


def normalized_event_time(value) -> str:
    current = now()
    if value is None:
        return iso(current)
    raw = text_value(value, "occurred_at", max_length=64)
    parsed = parse_iso(raw)
    if parsed > current + timedelta(minutes=5):
        raise ValueError("occurred_at은 현재보다 5분 넘게 미래일 수 없습니다.")
    if parsed > current:
        parsed = current
    return iso(parsed.astimezone(ZoneInfo(current_app.config["TIMEZONE"])))


def json_row(row) -> dict | None:
    return dict(row) if row is not None else None


@api.errorhandler(ValueError)
def bad_value(error):
    return jsonify({"error": "invalid_request", "message": str(error)}), 400


@api.errorhandler(sqlite3.IntegrityError)
def conflict(error):
    current_app.logger.warning("database integrity error: %s", error)
    return jsonify({"error": "conflict", "message": "이미 등록되었거나 값이 올바르지 않습니다."}), 409


@api.get("/health")
def health():
    database = get_db()
    database.execute("SELECT 1").fetchone()
    return jsonify(
        {
            "status": "ok",
            "service": "piuda-care",
            "version": "2.0.0",
            "time": iso(),
            "ollama_model": current_app.config["OLLAMA_MODEL"],
            "demo_mode": bool(current_app.config.get("DEMO_MODE")),
            "local_stt": local_stt_available(),
            "local_tts": local_tts_engine(),
            "hotspot": {
                "ssid": current_app.config["HOTSPOT_SSID"],
                "gateway": current_app.config["HOTSPOT_GATEWAY"],
            },
        }
    )


def _demo_access_error():
    if not current_app.config.get("DEMO_MODE"):
        return jsonify({"error": "demo_mode_only"}), 403
    if not is_private_request():
        return jsonify({"error": "local_network_only"}), 403
    return None


def _demo_snapshot() -> dict:
    tasks = today_tasks()
    alerts_count = get_db().execute(
        "SELECT COUNT(*) AS count FROM alerts WHERE acknowledged_at IS NULL"
    ).fetchone()["count"]
    return {
        "active": current_demo_state(),
        "tasks": {
            "total": len(tasks),
            "completed": sum(item["status"] == "completed" for item in tasks),
            "missed": sum(item["status"] == "missed" for item in tasks),
        },
        "open_alerts": alerts_count,
    }


@api.get("/demo/scenarios")
def demo_scenarios():
    denied = _demo_access_error()
    if denied:
        return denied
    return jsonify({"items": scenario_catalog(), **_demo_snapshot()})


@api.post("/demo/scenarios/<scenario_key>")
def run_demo_scenario(scenario_key: str):
    denied = _demo_access_error()
    if denied:
        return denied
    result = trigger_demo_scenario(scenario_key)
    if result is None:
        return jsonify({"error": "scenario_not_found"}), 404
    return jsonify(_demo_snapshot())


@api.post("/caregiver-alert")
def caregiver_alert():
    if not is_private_request():
        return jsonify({"error": "local_network_only"}), 403
    alert, created = create_caregiver_request_alert()
    if created:
        send_kakao_alert(f"[피우다] {alert['title']} · {alert['message']}")
    return jsonify({"ok": True, "created": created, "alert": alert}), 201 if created else 200


@api.post("/wellness-check")
def wellness_check():
    denied = _demo_access_error()
    if denied:
        return denied
    answer = str(payload().get("answer", ""))
    if answer == "ok":
        trigger_demo_scenario("inactivity_ok")
    elif answer in {"help", "timeout"}:
        trigger_demo_scenario("inactivity_no_response")
    else:
        raise ValueError("확인 응답은 ok, help, timeout 중 하나여야 합니다.")
    return jsonify({"ok": True, **_demo_snapshot()})


@api.get("/onboarding")
def onboarding():
    demo_mode = bool(current_app.config.get("DEMO_MODE"))
    return jsonify(
        {
            "setup_required": False if demo_mode else setup_required(),
            "private_network": is_private_request(),
            "demo_mode": demo_mode,
        }
    )


@api.post("/auth/setup")
def setup():
    if current_app.config.get("DEMO_MODE"):
        return jsonify({"error": "demo_mode_fixed_pin"}), 409
    if not setup_required():
        return jsonify({"error": "already_configured"}), 409
    if not is_private_request():
        return jsonify({"error": "local_network_only"}), 403
    data = payload()
    name = text_value(data.get("name", "보호자"), "보호자 이름", max_length=80)
    pin = text_value(data.get("pin"), "PIN", max_length=12)
    device_name = text_value(data.get("device_name", "초기 설정"), "기기 이름", max_length=80)
    create_caregiver(name, pin)
    token = login_with_pin(pin, device_name)
    return jsonify({"ok": True, "token": token}), 201


@api.post("/auth/login")
def login():
    if setup_required():
        return jsonify({"error": "setup_required"}), 409
    data = payload()
    pin = text_value(data.get("pin"), "PIN", max_length=12)
    device_name = text_value(data.get("device_name", "보호자 기기"), "기기 이름", max_length=80)
    token = login_with_pin(pin, device_name)
    if token is None:
        return jsonify({"error": "invalid_pin"}), 401
    return jsonify({"ok": True, "token": token})


@api.post("/auth/logout")
def logout():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        get_db().execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE token_hash = ?",
            (iso(), token_hash(header.removeprefix("Bearer ").strip())),
        )
        get_db().commit()
    session.clear()
    return jsonify({"ok": True})


@api.get("/profile")
def get_profile():
    row = get_db().execute("SELECT * FROM profile WHERE id = 1").fetchone()
    return jsonify(json_row(row) or {"user_name": "사용자", "caregiver_name": "보호자", "locale": "ko-KR"})


@api.put("/profile")
@caregiver_required
def put_profile():
    data = payload()
    database = get_db()
    existing = database.execute("SELECT * FROM profile WHERE id=1").fetchone()
    user_name = text_value(
        data.get("user_name", existing["user_name"] if existing else "사용자"),
        "사용자 이름",
        max_length=40,
    )
    caregiver_name = text_value(
        data.get("caregiver_name", existing["caregiver_name"] if existing else "보호자"),
        "보호자 이름",
        max_length=40,
    )
    birth_year = integer_value(
        data.get("birth_year", existing["birth_year"] if existing else None),
        "출생 연도",
        minimum=1900,
        maximum=now().year,
        allow_none=True,
    )
    caregiver_phone = text_value(
        data.get("caregiver_phone", existing["caregiver_phone"] if existing else None),
        "보호자 전화번호",
        required=False,
        allow_none=True,
        max_length=40,
    )
    locale = text_value(
        data.get("locale", existing["locale"] if existing else "ko-KR"),
        "언어",
        max_length=20,
    )
    database.execute(
        """
        INSERT INTO profile(id, user_name, birth_year, caregiver_name, caregiver_phone, locale, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          user_name=excluded.user_name, birth_year=excluded.birth_year,
          caregiver_name=excluded.caregiver_name, caregiver_phone=excluded.caregiver_phone,
          locale=excluded.locale, updated_at=excluded.updated_at
        """,
        (
            user_name,
            birth_year,
            caregiver_name,
            caregiver_phone,
            locale,
            iso(),
        ),
    )
    database.commit()
    return get_profile()


@api.get("/routines")
def routines():
    rows = get_db().execute("SELECT * FROM routines ORDER BY scheduled_time, id").fetchall()
    return jsonify({"items": [dict(row) for row in rows], "categories": sorted(CATEGORIES)})


@api.post("/routines")
@caregiver_required
def add_routine():
    return jsonify(create_routine(payload())), 201


@api.put("/routines/<int:routine_id>")
@caregiver_required
def update_routine(routine_id: int):
    data = payload()
    database = get_db()
    existing = database.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()
    if existing is None:
        return jsonify({"error": "not_found"}), 404
    title = text_value(data.get("title", existing["title"]), "일정 제목", max_length=80)
    category = text_value(data.get("category", existing["category"]), "일정 분류", max_length=20)
    scheduled_time = validate_time(data.get("scheduled_time", existing["scheduled_time"]))
    days_mask = integer_value(data.get("days_mask", existing["days_mask"]), "days_mask", minimum=1, maximum=127)
    active = int(boolean_value(data.get("active", existing["active"]), "active"))
    instructions = text_value(
        data.get("instructions", existing["instructions"]),
        "안내 문구",
        required=False,
        allow_none=True,
        max_length=500,
    )
    if category not in CATEGORIES:
        raise ValueError("일정 값을 확인하세요.")
    current = now()
    completed_today = database.execute(
        """
        SELECT id FROM task_occurrences
        WHERE routine_id=? AND due_date=? AND status='completed'
        ORDER BY completed_at DESC, id DESC LIMIT 1
        """,
        (routine_id, current.date().isoformat()),
    ).fetchone()
    database.execute(
        """
        UPDATE routines SET title=?, category=?, scheduled_time=?, days_mask=?, instructions=?, active=?, updated_at=?
        WHERE id=?
        """,
        (title, category, scheduled_time, days_mask, instructions, active, iso(), routine_id),
    )
    # 완료 기록은 보존하되, 바뀐 시간/요일과 모순되는 미완료 당일 항목은
    # 제거한 뒤 새 루틴 정의로 다시 생성합니다.
    database.execute(
        "DELETE FROM task_occurrences WHERE routine_id=? AND due_date>=? AND status IN ('pending','missed','skipped')",
        (routine_id, current.date().isoformat()),
    )
    # 오늘 이미 완료한 루틴은 시간 수정 뒤 두 번째 pending 항목을 만들지
    # 않고 같은 완료 기록을 새 예정 시각으로 이동합니다.
    applies_today = bool(active and days_mask & (1 << current.weekday()))
    if completed_today is not None and applies_today:
        hour, minute = map(int, scheduled_time.split(":"))
        due_at = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        database.execute(
            "UPDATE task_occurrences SET due_at=?, due_date=? WHERE id=?",
            (iso(due_at), current.date().isoformat(), completed_today["id"]),
        )
    database.commit()
    materialize_day()
    return jsonify(dict(database.execute("SELECT * FROM routines WHERE id = ?", (routine_id,)).fetchone()))


@api.delete("/routines/<int:routine_id>")
@caregiver_required
def delete_routine(routine_id: int):
    cursor = get_db().execute("DELETE FROM routines WHERE id = ?", (routine_id,))
    get_db().commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "not_found"}), 404
    return "", 204


@api.get("/tasks/today")
def tasks_today():
    items = today_tasks()
    return jsonify(
        {
            "date": now().date().isoformat(),
            "items": items,
            "summary": {
                "total": len(items),
                "completed": sum(item["status"] == "completed" for item in items),
                "missed": sum(item["status"] == "missed" for item in items),
            },
        }
    )


@api.post("/tasks/<int:task_id>/complete")
def complete_task(task_id: int):
    data = optional_payload()
    note = text_value(data.get("note"), "메모", required=False, allow_none=True, max_length=500)
    cursor = get_db().execute(
        """
        UPDATE task_occurrences SET status='completed', completed_at=?, note=?
        WHERE id=? AND status IN ('pending','missed')
        """,
        (iso(), note, task_id),
    )
    get_db().commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "not_found_or_already_completed"}), 409
    risk = evaluate_and_notify()
    return jsonify({"ok": True, "risk": risk})


@api.get("/risk/current")
def current_risk():
    return jsonify(evaluate_and_notify())


@api.get("/risk/rules")
def risk_rules():
    return jsonify(
        {
            "levels": LEVELS,
            "rules": [
                {"code": code, "points": points, "label": label}
                for code, (points, label) in RISK_RULES.items()
            ],
        }
    )


@api.get("/risk/history")
@caregiver_required
def risk_history():
    try:
        limit = min(max(int(request.args.get("limit", 50)), 1), 200)
    except (TypeError, ValueError) as error:
        raise ValueError("limit은 정수여야 합니다.") from error
    rows = get_db().execute(
        "SELECT * FROM risk_assessments ORDER BY assessed_at DESC LIMIT ?", (limit,)
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["factors"] = json.loads(item.pop("factors_json"))
        items.append(item)
    return jsonify({"items": items})


@api.get("/alerts")
@caregiver_required
def alerts():
    rows = get_db().execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 100").fetchall()
    return jsonify({"items": [dict(row) for row in rows]})


@api.post("/alerts/<int:alert_id>/ack")
@caregiver_required
def acknowledge_alert(alert_id: int):
    cursor = get_db().execute(
        "UPDATE alerts SET acknowledged_at = COALESCE(acknowledged_at, ?) WHERE id = ?",
        (iso(), alert_id),
    )
    get_db().commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"ok": True})


@api.get("/dashboard")
@caregiver_required
def dashboard():
    database = get_db()
    tasks = today_tasks()
    risk = evaluate_and_notify()
    alerts_rows = database.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 10").fetchall()
    sensor_rows = database.execute(
        """
        SELECT e.id, e.event_type, e.value, e.confidence, e.occurred_at,
               d.name AS device_name, d.location
        FROM sensor_events e JOIN sensor_devices d ON d.id=e.device_id
        ORDER BY e.occurred_at DESC LIMIT 20
        """
    ).fetchall()
    return jsonify(
        {
            "profile": json_row(database.execute("SELECT * FROM profile WHERE id=1").fetchone()),
            "tasks": tasks,
            "risk": risk,
            "alerts": [dict(row) for row in alerts_rows],
            "sensor_events": [dict(row) for row in sensor_rows],
        }
    )


@api.get("/sensors")
@caregiver_required
def sensors():
    rows = get_db().execute(
        """
        SELECT d.id, d.device_uid, d.name, d.location, d.created_at, d.last_seen_at,
               s.has_ir_sensor, s.ambient_c, s.object_c, s.pir_state, s.reason,
               s.csi_packet_count, s.csi_packet_rate, s.csi_rssi, s.csi_length,
               s.csi_mean_amplitude, s.csi_amplitude_stddev, s.csi_peak_delta,
               s.csi_dropped_count, s.csi_score, s.csi_status, s.received_at
        FROM sensor_devices d
        LEFT JOIN sensor_module_state s ON s.device_id=d.id
        ORDER BY d.name
        """
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        if item["has_ir_sensor"] is not None:
            item["has_ir_sensor"] = bool(item["has_ir_sensor"])
    return jsonify({"items": items})


@api.post("/sensors")
@caregiver_required
def register_sensor():
    data = payload()
    device_uid = text_value(data.get("device_uid"), "device_uid", max_length=80)
    name = text_value(data.get("name"), "센서 이름", max_length=80)
    location = text_value(data.get("location"), "설치 위치", max_length=80)
    api_key = secrets.token_urlsafe(32)
    get_db().execute(
        "INSERT INTO sensor_devices(device_uid, name, location, api_key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (device_uid, name, location, token_hash(api_key), iso()),
    )
    get_db().commit()
    return jsonify({"device_uid": device_uid, "api_key": api_key}), 201


def _module_sensor(device_uid: str, api_key: str):
    device = authenticate_sensor(device_uid, api_key)
    if device is not None:
        return device
    allowed_demo_ids = {"room_1", "room_2"}
    demo_key = str(current_app.config.get("DEMO_SENSOR_KEY", ""))
    if (
        not current_app.config.get("DEMO_MODE")
        or device_uid not in allowed_demo_ids
        or not api_key
        or not demo_key
        or not secrets.compare_digest(api_key, demo_key)
    ):
        return None
    names = {
        "room_1": ("거실 통합 센서", "거실"),
        "room_2": ("침실 통합 센서", "침실"),
    }
    name, location = names[device_uid]
    database = get_db()
    database.execute(
        """
        INSERT OR IGNORE INTO sensor_devices(
          device_uid, name, location, api_key_hash, created_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, NULL)
        """,
        (device_uid, name, location, token_hash(demo_key), iso()),
    )
    database.commit()
    return authenticate_sensor(device_uid, api_key)


@api.post("/module-readings")
def module_reading():
    data = payload()
    device_uid = text_value(data.get("sensor_id"), "sensor_id", max_length=80)
    has_ir_sensor = boolean_value(data.get("has_ir_sensor"), "has_ir_sensor")
    api_key = request.headers.get("X-Piuda-Sensor-Key", "")
    device = _module_sensor(device_uid, api_key)
    if device is None:
        return jsonify({"error": "sensor_auth_required"}), 401

    reason = text_value(data.get("reason"), "reason", max_length=24)
    if reason not in {"FIRST_BOOT", "MOTION_START", "MOTION_END", "PERIODIC"}:
        raise ValueError("지원하지 않는 reason입니다.")
    csi = object_value(data.get("csi"), message="csi는 JSON 객체여야 합니다.")
    reading = {
        "has_ir_sensor": has_ir_sensor,
        "ambient": number_value(
            data.get("ambient"), "ambient", minimum=-40, maximum=125, allow_none=True
        ),
        "object": number_value(
            data.get("object"), "object", minimum=-70, maximum=380, allow_none=True
        ),
        "pir": integer_value(data.get("pir"), "pir", minimum=0, maximum=1),
        "reason": reason,
        "csi": {
            "packet_count": integer_value(
                csi.get("packet_count"), "csi.packet_count", minimum=0, maximum=4_294_967_295
            ),
            "packet_rate": number_value(
                csi.get("packet_rate"), "csi.packet_rate", minimum=0, maximum=5_000
            ),
            "rssi": integer_value(csi.get("rssi"), "csi.rssi", minimum=-127, maximum=0),
            "length": integer_value(csi.get("length"), "csi.length", minimum=0, maximum=1_024),
            "mean_amplitude": number_value(
                csi.get("mean_amplitude"), "csi.mean_amplitude", minimum=0, maximum=500
            ),
            "amplitude_stddev": number_value(
                csi.get("amplitude_stddev"), "csi.amplitude_stddev", minimum=0, maximum=500
            ),
            "peak_delta": number_value(
                csi.get("peak_delta"), "csi.peak_delta", minimum=0, maximum=500
            ),
            "dropped_count": integer_value(
                csi.get("dropped_count"), "csi.dropped_count", minimum=0, maximum=4_294_967_295
            ),
        },
    }
    if has_ir_sensor and (reading["ambient"] is None) != (reading["object"] is None):
        raise ValueError("ambient와 object는 함께 보내거나 둘 다 null이어야 합니다.")

    result = ingest_module_reading(device, reading)
    risk = evaluate_and_notify() if result["events"] else None
    return jsonify({"accepted": True, **result, "risk": risk}), 202


@api.post("/sensor-events")
def sensor_event():
    data = payload()
    device_uid = text_value(data.get("device_uid"), "device_uid", max_length=80)
    api_key = request.headers.get("X-Piuda-Sensor-Key", "")
    device = authenticate_sensor(device_uid, api_key)
    if device is None:
        return jsonify({"error": "sensor_auth_required"}), 401

    event_type = text_value(data.get("event_type"), "event_type", max_length=30)
    allowed = {"pir_motion", "pir_idle", "csi_motion", "csi_fall", "heartbeat"}
    if event_type not in allowed:
        raise ValueError("지원하지 않는 event_type입니다.")
    event_id = text_value(
        data.get("event_id"),
        "event_id",
        required=False,
        allow_none=True,
        max_length=80,
    ) or None
    confidence = number_value(data.get("confidence"), "confidence", minimum=0, maximum=1, allow_none=True)
    value = number_value(data.get("value"), "value", allow_none=True)
    details = data.get("details", {})
    if not isinstance(details, dict):
        raise ValueError("details는 JSON 객체여야 합니다.")
    occurred_at = normalized_event_time(data.get("occurred_at"))
    database = get_db()
    cursor = database.execute(
        """
        INSERT OR IGNORE INTO sensor_events(
          device_id, event_id, event_type, value, confidence, occurred_at, received_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device["id"], event_id, event_type, value, confidence,
            occurred_at, iso(), json.dumps(details, ensure_ascii=False),
        ),
    )
    database.execute("UPDATE sensor_devices SET last_seen_at=? WHERE id=?", (iso(), device["id"]))
    database.commit()
    risk = evaluate_and_notify()
    return jsonify({"accepted": True, "duplicate": cursor.rowcount == 0, "risk": risk}), 202


@api.post("/feedback")
def feedback():
    data = payload()
    message = text_value(data.get("message"), "message", max_length=500)
    tasks = today_tasks()
    risk = evaluate_and_notify()
    context = {
        "risk": {"score": risk["score"], "level": risk["level_label"], "factors": risk["factors"]},
        "pending_tasks": [item["title"] for item in tasks if item["status"] != "completed"],
        "tasks": [
            {
                "title": item["title"],
                "scheduled_time": item["scheduled_time"],
                "status": item["status"],
                "category": item["category"],
            }
            for item in tasks
        ],
    }
    database = get_db()
    history_rows = database.execute(
        "SELECT role, content FROM feedback_messages ORDER BY id DESC LIMIT 10"
    ).fetchall()
    history = [dict(row) for row in reversed(history_rows)]
    answer = ollama_feedback(message[:500], context, history)
    database.executemany(
        "INSERT INTO feedback_messages(role, content, created_at) VALUES (?, ?, ?)",
        [("user", message[:500], iso()), ("assistant", answer, iso())],
    )
    database.commit()
    return jsonify({"reply": answer, "speak": True, "risk": risk})


@api.post("/tts")
def local_tts():
    try:
        loopback = ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return jsonify({"error": "local_kiosk_only"}), 403
    text = text_value(payload().get("text"), "text", max_length=500)
    if not speak_local_async(text):
        return jsonify({"error": "local_tts_unavailable"}), 503
    return jsonify({"accepted": True}), 202


@api.post("/voice/listen")
def local_voice_listen():
    try:
        loopback = ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return jsonify({"error": "local_kiosk_only"}), 403

    data = optional_payload()
    duration = integer_value(
        data.get("duration_seconds", current_app.config["STT_DURATION_SECONDS"]),
        "duration_seconds",
        minimum=2,
        maximum=8,
    )
    try:
        transcript = transcribe_local(duration)
    except LocalSttBusy as error:
        return jsonify({"error": "voice_busy", "message": str(error)}), 409
    except LocalSttNoSpeech as error:
        return jsonify({"error": "no_speech", "message": str(error)}), 422
    except LocalSttUnavailable as error:
        return jsonify({"error": "local_stt_unavailable", "message": str(error)}), 503
    return jsonify({"transcript": transcript})

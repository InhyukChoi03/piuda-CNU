from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from threading import Lock

from flask import current_app

from .clock import iso, now, parse_iso
from .db import get_db
from .scheduler import materialize_day, refresh_missed


_EVALUATION_LOCK = Lock()


RISK_RULES = {
    "medication_missed": (20, "복약 일정 미수행"),
    "meal_missed": (15, "식사 일정 미수행"),
    "scheduled_inactivity": (25, "정해진 시간 이후 활동 미감지"),
    "long_pir_inactivity": (30, "장시간 PIR 움직임 미감지"),
    "csi_fall": (50, "Wi-Fi CSI 낙상 의심 패턴"),
    "night_wandering": (20, "야간 반복 움직임 감지"),
    "missed_and_inactive": (40, "일정 미수행과 움직임 없음 동시 발생"),
    "sensor_offline": (25, "센서 연결 끊김"),
}

SENSOR_ONLINE_WINDOW = timedelta(minutes=10)
SENSOR_OFFLINE_WINDOW = timedelta(minutes=30)

LEVELS = {
    "normal": {"label": "안심", "min": 76, "color": "green"},
    "caution": {"label": "살펴보기", "min": 51, "color": "amber"},
    "danger": {"label": "주의", "min": 21, "color": "orange"},
    "emergency": {"label": "긴급", "min": 0, "color": "red"},
}


def level_for_score(score: int) -> str:
    """Return the safety level for a health score where 100 is best."""
    if score <= 20:
        return "emergency"
    if score <= 50:
        return "danger"
    if score <= 75:
        return "caution"
    return "normal"


def _factor(code: str, evidence: str | None = None) -> dict:
    points, label = RISK_RULES[code]
    return {"code": code, "label": label, "points": points, "evidence": evidence}


def _safe_event_time(value: str):
    try:
        return parse_iso(value)
    except (TypeError, ValueError):
        return None


def _recent_event_rows(database, event_types: tuple[str, ...], limit: int = 5000):
    placeholders = ",".join("?" for _ in event_types)
    return database.execute(
        f"""
        SELECT occurred_at, received_at, event_type, value, confidence
        FROM sensor_events
        WHERE event_type IN ({placeholders})
        ORDER BY id DESC LIMIT ?
        """,
        (*event_types, limit),
    ).fetchall()


def _latest_motion(database, current: datetime, device_ids: list[int] | None = None):
    if device_ids:
        placeholders = ",".join("?" for _ in device_ids)
        rows = database.execute(
            f"""
            SELECT occurred_at, received_at, event_type, value, confidence
            FROM sensor_events
            WHERE device_id IN ({placeholders}) AND event_type IN ('pir_motion','csi_motion')
            ORDER BY id DESC LIMIT 5000
            """,
            device_ids,
        ).fetchall()
    else:
        rows = []
    valid = [
        (timestamp, row)
        for row in rows
        if (timestamp := _safe_event_time(row["occurred_at"])) is not None and timestamp <= current
    ]
    if not valid:
        return None
    _, row = max(valid, key=lambda item: item[0])
    return row


def _monitoring_window(database, current: datetime) -> tuple[list[int], datetime | None]:
    devices = database.execute(
        "SELECT id, created_at, last_seen_at FROM sensor_devices"
    ).fetchall()
    recent_cutoff = current - SENSOR_ONLINE_WINDOW
    active_devices = [
        row
        for row in devices
        if (timestamp := _safe_event_time(row["last_seen_at"])) is not None
        and recent_cutoff <= timestamp <= current
    ]
    if not active_devices:
        return [], None

    active_ids = [row["id"] for row in active_devices]
    device_placeholders = ",".join("?" for _ in active_ids)
    observations = database.execute(
        f"""
        SELECT occurred_at FROM sensor_events
        WHERE device_id IN ({device_placeholders})
          AND event_type IN ('pir_motion','csi_motion','heartbeat','pir_idle','csi_fall')
        ORDER BY id DESC LIMIT 5000
        """,
        active_ids,
    )
    times = [
        timestamp
        for row in observations
        if (timestamp := _safe_event_time(row["occurred_at"])) is not None and timestamp <= current
    ]
    created_times = [
        timestamp
        for row in active_devices
        if (timestamp := _safe_event_time(row["created_at"])) is not None and timestamp <= current
    ]
    # 센서가 오래전에 등록됐더라도 첫 신호가 방금 온 경우를
    # 등록 시각부터의 무활동으로 판정하지 않습니다. 관측 이력이
    # 하나도 없는 기기만 created_at을 시작점으로 사용합니다.
    started_at = min(times) if times else min(created_times) if created_times else None
    return active_ids, started_at


def _offline_sensor_evidence(database, current: datetime) -> str | None:
    cutoff = current - SENSOR_OFFLINE_WINDOW
    offline: list[tuple[datetime, str, bool]] = []
    rows = database.execute(
        "SELECT name, location, created_at, last_seen_at FROM sensor_devices"
    ).fetchall()
    for row in rows:
        last_seen = _safe_event_time(row["last_seen_at"])
        created = _safe_event_time(row["created_at"])
        reference = last_seen or created
        if reference is None or reference > current or reference > cutoff:
            continue
        label = str(row["location"] or row["name"] or "센서")
        offline.append((reference, label, last_seen is not None))
    if not offline:
        return None
    reference, label, was_seen = min(offline, key=lambda item: item[0])
    minutes = int((current - reference).total_seconds() // 60)
    if len(offline) > 1:
        return f"{len(offline)}개 센서에서 30분 넘게 상태 신호 없음"
    if was_seen:
        return f"{label} 마지막 신호 {minutes}분 전"
    return f"{label} 등록 후 {minutes}분 동안 신호 없음"


def _latest_fall(database, current: datetime):
    cutoff = current - timedelta(minutes=30)
    valid = []
    for row in _recent_event_rows(database, ("csi_fall",), limit=500):
        timestamp = _safe_event_time(row["occurred_at"])
        confidence = float(row["confidence"] or 0)
        if timestamp is not None and cutoff <= timestamp <= current and confidence >= 0.65:
            valid.append((timestamp, row))
    return max(valid, key=lambda item: item[0])[1] if valid else None


def _night_motion_count(database, start: datetime, end: datetime) -> int:
    count = 0
    for row in _recent_event_rows(database, ("pir_motion", "csi_motion"), limit=2000):
        timestamp = _safe_event_time(row["occurred_at"])
        if timestamp is not None and start <= timestamp <= end:
            count += 1
    return count


def evaluate_risk(current: datetime | None = None, persist: bool = True) -> dict:
    if persist:
        # Flask는 요청마다 다른 SQLite 연결을 사용하므로, 여러 폴러가
        # 동시에 같은 판정을 읽고 알림을 중복 생성하는 일을 프로세스 내에서
        # 직렬화합니다. persist=False는 읽기 전용이므로 잠금이 필요 없습니다.
        with _EVALUATION_LOCK:
            return _evaluate_risk(current, persist=True)
    return _evaluate_risk(current, persist=False)


def _evaluate_risk(current: datetime | None = None, persist: bool = True) -> dict:
    if current is None and current_app.config.get("DEMO_MODE"):
        override = get_db().execute("SELECT * FROM demo_state WHERE id=1").fetchone()
        if override is not None:
            return {
                "score": override["risk_score"],
                "level": override["risk_level"],
                "level_label": LEVELS[override["risk_level"]]["label"],
                "factors": json.loads(override["factors_json"]),
                "assessed_at": override["activated_at"],
                "new_alert": False,
                "scenario_key": override["scenario_key"],
                "scenario_title": override["scenario_title"],
                "user_message": override["user_message"],
            }
    current = current or now()
    database = get_db()
    materialize_day(current)
    refresh_missed(current)

    day = current.date().isoformat()
    missed = database.execute(
        """
        SELECT o.due_at, r.title, r.category
        FROM task_occurrences o JOIN routines r ON r.id = o.routine_id
        WHERE o.due_date = ? AND o.status = 'missed'
        ORDER BY o.due_at
        """,
        (day,),
    ).fetchall()

    factors: list[dict] = []
    medication = next((row for row in missed if row["category"] == "medication"), None)
    meal = next((row for row in missed if row["category"] == "meal"), None)
    if medication:
        factors.append(_factor("medication_missed", medication["title"]))
    if meal:
        factors.append(_factor("meal_missed", meal["title"]))

    offline_evidence = _offline_sensor_evidence(database, current)
    if offline_evidence:
        factors.append(_factor("sensor_offline", offline_evidence))

    device_exists = database.execute("SELECT 1 FROM sensor_devices LIMIT 1").fetchone() is not None
    active_device_ids, observation_started_at = _monitoring_window(database, current)
    monitoring_active = bool(active_device_ids)
    latest_motion = _latest_motion(database, current, active_device_ids)
    latest_motion_at = parse_iso(latest_motion["occurred_at"]) if latest_motion else None

    scheduled_inactivity = False
    if device_exists and monitoring_active and missed:
        last_due = max(parse_iso(row["due_at"]) for row in missed)
        scheduled_inactivity = latest_motion_at is None or latest_motion_at < last_due
        if scheduled_inactivity:
            factors.append(_factor("scheduled_inactivity", f"최근 미수행 일정: {missed[-1]['title']}"))

    long_inactivity = False
    if monitoring_active and time(7, 0) <= current.timetz().replace(tzinfo=None) <= time(22, 0):
        threshold = timedelta(minutes=int(current_app.config["INACTIVITY_MINUTES"]))
        inactivity_since = latest_motion_at or observation_started_at
        if inactivity_since is not None and current - inactivity_since >= threshold:
            long_inactivity = True
            minutes = int((current - inactivity_since).total_seconds() // 60)
            evidence = (
                f"마지막 움직임 {minutes}분 전"
                if latest_motion_at is not None
                else f"센서 관찰 {minutes}분 동안 움직임 기록 없음"
            )
            factors.append(_factor("long_pir_inactivity", evidence))

    fall = _latest_fall(database, current)
    if fall:
        factors.append(_factor("csi_fall", f"신뢰도 {float(fall['confidence']):.0%}"))

    night_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    night_end = current.replace(hour=5, minute=0, second=0, microsecond=0)
    if current < night_end:
        night_count = _night_motion_count(database, night_start, current)
        if night_count >= 3:
            factors.append(_factor("night_wandering", f"자정 이후 {night_count}회"))

    if missed and (scheduled_inactivity or long_inactivity):
        factors.append(_factor("missed_and_inactive", f"미수행 {len(missed)}건"))

    # 화면에는 위험도가 아닌 건강 점수를 표시합니다.
    # 규칙의 points는 100점에서 빼는 감점이다.
    score = max(0, 100 - min(100, sum(item["points"] for item in factors)))
    level = level_for_score(score)
    result = {
        "score": score,
        "level": level,
        "level_label": LEVELS[level]["label"],
        "factors": factors,
        "assessed_at": iso(current),
        "new_alert": False,
    }
    if not persist:
        return result

    latest = database.execute(
        "SELECT * FROM risk_assessments ORDER BY assessed_at DESC, id DESC LIMIT 1"
    ).fetchone()
    factor_json = json.dumps(factors, ensure_ascii=False, separators=(",", ":"))
    if latest and latest["score"] == score and latest["factors_json"] == factor_json:
        if current - parse_iso(latest["assessed_at"]) < timedelta(minutes=5):
            return {
                **result,
                "id": latest["id"],
                "assessed_at": latest["assessed_at"],
            }

    cursor = database.execute(
        "INSERT INTO risk_assessments(score, level, factors_json, assessed_at) VALUES (?, ?, ?, ?)",
        (score, level, factor_json, iso(current)),
    )
    assessment_id = cursor.lastrowid

    if level in {"danger", "emergency"}:
        recent_alert = database.execute(
            """
            SELECT level, created_at FROM alerts
            WHERE risk_assessment_id IS NOT NULL
            ORDER BY created_at DESC, id DESC LIMIT 1
            """
        ).fetchone()
        should_alert = (
            recent_alert is None
            or recent_alert["level"] != level
            or current - parse_iso(recent_alert["created_at"]) >= timedelta(minutes=30)
        )
        if should_alert:
            labels = ", ".join(item["label"] for item in factors[:3])
            database.execute(
                """
                INSERT INTO alerts(risk_assessment_id, level, title, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    assessment_id,
                    level,
                    "즉시 확인이 필요합니다" if level == "emergency" else "위험 상태가 감지되었습니다",
                    f"건강 점수 {score}점 · {labels}",
                    iso(current),
                ),
            )
            result["new_alert"] = True
    database.commit()
    result["id"] = assessment_id
    return result


def latest_risk() -> dict:
    return evaluate_risk()

from __future__ import annotations

import json
from datetime import timedelta

from flask import current_app

from .clock import iso, now, parse_iso
from .db import get_db
from .risk import level_for_score


DEMO_GROUPS = (
    ("daily", "일정"),
    ("check", "상태 확인"),
    ("response", "보호자 대응"),
)

DEMO_SCENARIOS = (
    {"key": "normal", "group": "daily", "icon": "01", "title": "기본 상태", "summary": "오늘 일정과 최근 움직임이 등록된 초기 장면입니다.", "expected": "건강 점수 100 · 보호자 알림 없음"},
    {"key": "medication_reminder", "group": "daily", "icon": "02", "title": "약 먹을 시간", "summary": "복약 일정을 사용자 화면에 먼저 표시합니다.", "expected": "복약 일정 강조 · 보호자 알림 없음"},
    {"key": "medication_done", "group": "daily", "icon": "03", "title": "복약 완료", "summary": "사용자가 완료를 누르면 복약 일정이 완료됩니다.", "expected": "복약 완료 · 건강 점수 100"},
    {"key": "all_completed", "group": "daily", "icon": "04", "title": "오늘 일정 모두 완료", "summary": "오늘 일정을 모두 완료한 상태입니다.", "expected": "5/5 완료 · 건강 점수 100"},
    {"key": "inactivity_check", "group": "check", "icon": "05", "title": "낮 시간 3시간 무활동", "summary": "낮 시간 3시간 동안 움직임이 없어 사용자 상태를 먼저 확인합니다.", "expected": "사용자 확인 팝업 · 보호자 알림은 아직 없음"},
    {"key": "inactivity_ok", "group": "check", "icon": "06", "title": "‘괜찮아요’ 응답", "summary": "사용자가 응답하면 보호자 알림 없이 확인을 종료합니다.", "expected": "팝업 종료 · 건강 점수 100"},
    {"key": "inactivity_no_response", "group": "check", "icon": "07", "title": "확인에 응답 없음", "summary": "30초 동안 응답이 없으면 보호자 확인 알림을 보냅니다.", "expected": "건강 점수 30 · 보호자 확인 알림"},
    {"key": "sensor_offline", "group": "check", "icon": "08", "title": "센서 연결 끊김", "summary": "센서 신호가 30분 동안 없으면 보호자에게 점검 알림을 보냅니다.", "expected": "사용자 일상 화면 유지 · 보호자 점검 알림"},
    {"key": "fall", "group": "response", "icon": "09", "title": "넘어짐 의심", "summary": "Wi-Fi CSI의 넘어짐 의심 신호를 사용자와 보호자 화면에 표시합니다.", "expected": "건강 점수 50 · 보호자 즉시 확인"},
    {"key": "emergency", "group": "response", "icon": "10", "title": "복합 긴급 상황", "summary": "일정 미수행·장시간 무활동·넘어짐 신호가 함께 감지됩니다.", "expected": "건강 점수 0 · 긴급 확인"},
    {"key": "recovered", "group": "response", "icon": "11", "title": "활동 재확인", "summary": "움직임이 다시 감지되면 건강 점수가 100으로 돌아갑니다.", "expected": "건강 점수 100 · 회복 안내"},
    {"key": "caregiver_alert", "group": "response", "icon": "12", "title": "보호자 알림 요청", "summary": "사용자가 버튼을 눌러 보호자에게 직접 확인 알림을 보냅니다.", "expected": "보호자 화면에 확인 팝업 · 소리·진동 알림"},
)


def scenario_catalog() -> list[dict]:
    return [dict(item) for item in DEMO_SCENARIOS]


def _scenario(key: str) -> dict | None:
    return next((dict(item) for item in DEMO_SCENARIOS if item["key"] == key), None)


def current_demo_state() -> dict:
    row = get_db().execute("SELECT * FROM demo_state WHERE id=1").fetchone()
    if row is None:
        return {
            "scenario_key": "normal",
            "scenario_title": "기본 상태",
            "description": "오늘 일정과 최근 움직임이 등록된 초기 장면입니다.",
            "risk_score": 100,
            "risk_level": "normal",
            "factors": [],
            "user_message": "현재 확인된 위험 신호가 없습니다.",
            "activated_at": iso(),
        }
    result = dict(row)
    result["factors"] = json.loads(result.pop("factors_json"))
    return result


def _task(title: str):
    return get_db().execute(
        """
        SELECT o.id FROM task_occurrences o
        JOIN routines r ON r.id=o.routine_id
        WHERE o.due_date=? AND r.title=?
        ORDER BY o.id LIMIT 1
        """,
        (now().date().isoformat(), title),
    ).fetchone()


def _set_task(title: str, status: str) -> None:
    row = _task(title)
    if row is None:
        return
    completed_at = iso() if status == "completed" else None
    get_db().execute(
        "UPDATE task_occurrences SET status=?, completed_at=? WHERE id=?",
        (status, completed_at, row["id"]),
    )


def _sensor_id() -> int:
    row = get_db().execute("SELECT id FROM sensor_devices ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("데모 센서가 없습니다.")
    return int(row["id"])


def _clear_sensor_events() -> None:
    get_db().execute("DELETE FROM sensor_events")


def _sensor_event(event_type: str, minutes_ago: int = 0, confidence: float = 1.0, occurred_at=None) -> None:
    event_time = iso(occurred_at or (now() - timedelta(minutes=minutes_ago)))
    get_db().execute(
        """
        INSERT INTO sensor_events(device_id, event_type, value, confidence, occurred_at, received_at, payload_json)
        VALUES (?, ?, 1, ?, ?, ?, '{}')
        """,
        (_sensor_id(), event_type, confidence, event_time, iso()),
    )


def _activate(
    scenario: dict,
    score: int,
    factors: list[dict],
    user_message: str,
    alert: tuple[str, str, str] | None = None,
) -> None:
    database = get_db()
    level = level_for_score(score)
    timestamp = iso()
    factor_json = json.dumps(factors, ensure_ascii=False, separators=(",", ":"))
    database.execute(
        """
        INSERT INTO demo_state(
          id, scenario_key, scenario_title, description, risk_score,
          risk_level, factors_json, user_message, activated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          scenario_key=excluded.scenario_key,
          scenario_title=excluded.scenario_title,
          description=excluded.description,
          risk_score=excluded.risk_score,
          risk_level=excluded.risk_level,
          factors_json=excluded.factors_json,
          user_message=excluded.user_message,
          activated_at=excluded.activated_at
        """,
        (scenario["key"], scenario["title"], scenario["summary"], score, level, factor_json, user_message, timestamp),
    )
    cursor = database.execute(
        "INSERT INTO risk_assessments(score, level, factors_json, assessed_at) VALUES (?, ?, ?, ?)",
        (score, level, factor_json, timestamp),
    )
    if alert:
        alert_level, title, message = alert
        database.execute(
            "INSERT INTO alerts(risk_assessment_id, level, title, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (cursor.lastrowid, alert_level, title, message, timestamp),
        )
    database.commit()


def create_caregiver_request_alert() -> tuple[dict, bool]:
    database = get_db()
    recent = database.execute(
        "SELECT * FROM alerts WHERE title='사용자 확인 요청' AND acknowledged_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if recent and now() - parse_iso(recent["created_at"]) < timedelta(seconds=30):
        return dict(recent), False
    profile = database.execute("SELECT user_name FROM profile WHERE id=1").fetchone()
    user_name = profile["user_name"] if profile else "사용자"
    cursor = database.execute(
        "INSERT INTO alerts(risk_assessment_id, level, title, message, created_at) VALUES (NULL, 'danger', '사용자 확인 요청', ?, ?)",
        (f"{user_name}님이 보호자의 확인을 요청했습니다.", iso()),
    )
    database.commit()
    row = database.execute("SELECT * FROM alerts WHERE id=?", (cursor.lastrowid,)).fetchone()
    return dict(row), True


def trigger_demo_scenario(key: str) -> dict | None:
    scenario = _scenario(key)
    if scenario is None:
        return None

    # 장면을 바꾸어도 보호자 브라우저 로그인은 유지합니다.
    from .cli import reset_demo

    reset_demo(current_app._get_current_object(), preserve_auth=True)
    if key == "normal":
        return current_demo_state()

    if key == "medication_reminder":
        _set_task("아침 약 복용", "pending")
        _activate(scenario, 100, [], "약 드실 시간이에요. 약 봉투를 확인하고 물과 함께 드세요.")
    elif key == "medication_done":
        _set_task("아침 약 복용", "completed")
        _activate(scenario, 100, [], "복약 완료를 기록했어요.")
    elif key == "all_completed":
        get_db().execute(
            "UPDATE task_occurrences SET status='completed', completed_at=? WHERE due_date=?",
            (iso(), now().date().isoformat()),
        )
        _activate(scenario, 100, [], "오늘 일정을 모두 완료했어요.")
    elif key == "inactivity_check":
        _clear_sensor_events()
        _sensor_event("pir_motion", 190, 0.98)
        factors = [{"code": "long_pir_inactivity", "label": "낮 시간 장시간 움직임 없음", "points": 30, "evidence": "마지막 움직임 3시간 10분 전"}]
        _activate(scenario, 70, factors, "잠깐 쉬고 계신가요? 화면에서 현재 상태를 알려 주세요.")
    elif key == "inactivity_ok":
        _clear_sensor_events()
        _sensor_event("pir_motion", 0, 0.99)
        _activate(scenario, 100, [], "‘괜찮아요’ 응답을 확인했어요.")
    elif key == "inactivity_no_response":
        _clear_sensor_events()
        _sensor_event("pir_motion", 210, 0.98)
        factors = [
            {"code": "long_pir_inactivity", "label": "장시간 움직임 없음", "points": 30, "evidence": "마지막 움직임 3시간 30분 전"},
            {"code": "missed_and_inactive", "label": "사용자 확인에 응답 없음", "points": 40, "evidence": "30초 확인 팝업 미응답"},
        ]
        _activate(
            scenario, 30, factors, "보호자에게 확인을 요청했어요. 안전한 곳에서 잠시 기다려 주세요.",
            ("danger", "사용자 확인이 필요합니다", "낮 시간 3시간 30분 무활동 후 화면 확인에도 응답이 없습니다."),
        )
    elif key == "sensor_offline":
        _clear_sensor_events()
        _sensor_event("heartbeat", 30, 1.0)
        get_db().execute(
            "UPDATE sensor_devices SET last_seen_at=? WHERE id=?",
            (iso(now() - timedelta(minutes=30)), _sensor_id()),
        )
        factors = [{"code": "sensor_offline", "label": "거실 센서 연결 끊김", "points": 25, "evidence": "마지막 신호 30분 전"}]
        _activate(
            scenario, 75, factors, "오늘 일정은 계속 확인할 수 있어요.",
            ("caution", "거실 센서 점검이 필요합니다", "센서 상태 신호가 30분 동안 수신되지 않았습니다."),
        )
    elif key == "fall":
        _sensor_event("csi_fall", 2, 0.91)
        factors = [{"code": "csi_fall", "label": "Wi-Fi CSI 넘어짐 의심", "points": 50, "evidence": "신뢰도 91%"}]
        _activate(
            scenario, 50, factors, "혹시 넘어지셨나요? 움직이기 힘들면 그대로 계세요. 보호자에게 알렸어요.",
            ("danger", "넘어짐 의심 신호가 감지되었습니다", "Wi-Fi CSI 신뢰도 91% · 즉시 확인해 주세요."),
        )
    elif key == "emergency":
        _set_task("아침 약 복용", "missed")
        _set_task("점심 식사", "missed")
        _clear_sensor_events()
        _sensor_event("pir_motion", 240, 0.98)
        _sensor_event("csi_fall", 3, 0.94)
        factors = [
            {"code": "medication_missed", "label": "복약 일정 미수행", "points": 20, "evidence": "09:00 아침 약"},
            {"code": "meal_missed", "label": "식사 일정 미수행", "points": 15, "evidence": "12:30 점심 식사"},
            {"code": "long_pir_inactivity", "label": "장시간 움직임 없음", "points": 30, "evidence": "마지막 움직임 4시간 전"},
            {"code": "csi_fall", "label": "넘어짐 의심", "points": 50, "evidence": "신뢰도 94%"},
        ]
        _activate(
            scenario, 0, factors, "움직이지 말고 안전한 곳에서 보호자를 기다려 주세요.",
            ("emergency", "즉시 확인이 필요합니다", "일정 미수행·장시간 무활동·넘어짐 신호가 함께 감지되었습니다."),
        )
    elif key == "recovered":
        _sensor_event("pir_motion", 0, 0.99)
        _activate(
            scenario, 100, [], "움직임이 다시 확인됐어요. 현재 건강 점수는 100점이에요.",
            ("info", "활동이 다시 확인되었습니다", "거실 움직임이 감지되어 안심 상태로 돌아왔습니다."),
        )
    elif key == "caregiver_alert":
        _activate(
            scenario,
            100,
            [],
            "보호자에게 확인 알림을 보냈어요.",
            ("danger", "사용자 확인 요청", "김피움님이 보호자의 확인을 요청했습니다."),
        )

    return current_demo_state()

from __future__ import annotations

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from flask import current_app

from .clock import iso, now, parse_iso
from .db import get_db
from .validation import integer_value, text_value


CATEGORIES = {"meal", "medication", "cleaning", "sleep", "outing", "hospital", "other"}
TIME_PATTERN = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def _weekday_bit(value: datetime) -> int:
    return 1 << value.weekday()


def validate_time(value: str) -> str:
    if not isinstance(value, str) or TIME_PATTERN.fullmatch(value) is None:
        raise ValueError("scheduled_time은 HH:MM 형식이어야 합니다.")
    return value


def materialize_day(day: datetime | None = None) -> None:
    current = day or now()
    database = get_db()
    routines = database.execute(
        "SELECT * FROM routines WHERE active = 1 AND (days_mask & ?) != 0 ORDER BY scheduled_time",
        (_weekday_bit(current),),
    ).fetchall()
    tz = ZoneInfo(current_app.config["TIMEZONE"])
    for routine in routines:
        hour, minute = map(int, routine["scheduled_time"].split(":"))
        due = datetime(current.year, current.month, current.day, hour, minute, tzinfo=tz)
        database.execute(
            """
            INSERT INTO task_occurrences(routine_id, due_at, due_date)
            VALUES (?, ?, ?)
            ON CONFLICT(routine_id, due_at) DO NOTHING
            """,
            (routine["id"], iso(due), due.date().isoformat()),
        )
    database.commit()


def refresh_missed(current: datetime | None = None) -> int:
    if current_app.config.get("DEMO_MODE"):
        return 0
    current = current or now()
    grace = timedelta(minutes=current_app.config["TASK_GRACE_MINUTES"])
    database = get_db()
    pending = database.execute(
        "SELECT id, due_at FROM task_occurrences WHERE status = 'pending' AND due_date <= ?",
        (current.date().isoformat(),),
    ).fetchall()
    missed = [row["id"] for row in pending if parse_iso(row["due_at"]) + grace < current]
    if missed:
        placeholders = ",".join("?" for _ in missed)
        database.execute(
            f"UPDATE task_occurrences SET status = 'missed' WHERE id IN ({placeholders})",
            missed,
        )
        database.commit()
    return len(missed)


def today_tasks(current: datetime | None = None) -> list[dict]:
    current = current or now()
    materialize_day(current)
    refresh_missed(current)
    rows = get_db().execute(
        """
        SELECT o.id, o.due_at, o.status, o.completed_at, o.note,
               r.id AS routine_id, r.title, r.category, r.instructions, r.scheduled_time
        FROM task_occurrences o
        JOIN routines r ON r.id = o.routine_id
        WHERE o.due_date = ?
        ORDER BY o.due_at, o.id
        """,
        (current.date().isoformat(),),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        # 완료 뒤 루틴 시간이 바뀌더라도 이미 수행한 항목에는 발생 당시
        # due_at 시각을 보여 주어 현재 정의와 과거 기록을 섞지 않습니다.
        item["scheduled_time"] = parse_iso(item["due_at"]).strftime("%H:%M")
        items.append(item)
    return items


def create_routine(payload: dict) -> dict:
    title = text_value(payload.get("title"), "일정 제목", max_length=80)
    category = text_value(payload.get("category", "other"), "일정 분류", max_length=20)
    scheduled_time = validate_time(payload.get("scheduled_time"))
    days_mask = integer_value(payload.get("days_mask", 127), "days_mask", minimum=1, maximum=127)
    instructions = text_value(
        payload.get("instructions"),
        "안내 문구",
        required=False,
        allow_none=True,
        max_length=500,
    )
    if category not in CATEGORIES:
        raise ValueError("지원하지 않는 일정 분류입니다.")
    timestamp = iso()
    database = get_db()
    cursor = database.execute(
        """
        INSERT INTO routines(title, category, scheduled_time, days_mask, instructions, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (title, category, scheduled_time, days_mask, instructions, timestamp, timestamp),
    )
    database.commit()
    return dict(database.execute("SELECT * FROM routines WHERE id = ?", (cursor.lastrowid,)).fetchone())

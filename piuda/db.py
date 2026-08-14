from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g


def connect_database(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def init_database(path: str | Path) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    connection = connect_database(path)
    try:
        # v3 데이터베이스를 그대로 여는 경우 CREATE TABLE IF NOT EXISTS만으로는
        # 새 열이 생기지 않습니다. 인덱스가 이 열을 참조하기 전에 작은 호환
        # 마이그레이션을 먼저 적용합니다.
        sensor_events_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sensor_events'"
        ).fetchone()
        if sensor_events_exists:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(sensor_events)")
            }
            if "event_id" not in columns:
                connection.execute("ALTER TABLE sensor_events ADD COLUMN event_id TEXT")
        alerts_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alerts'"
        ).fetchone()
        if alerts_exists:
            connection.execute("DELETE FROM alerts WHERE title='보호자 통화 요청'")
        # v5에서는 음성 통화 기능과 WebRTC 신호 저장소를 제거했습니다.
        # 이전 설치본의 불필요한 통화 데이터도 마이그레이션 시 함께 삭제합니다.
        connection.execute("DROP TABLE IF EXISTS call_signals")
        connection.execute("DROP TABLE IF EXISTS calls")
        connection.executescript(schema)
        connection.commit()
    finally:
        connection.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_database(current_app.config["DATABASE"])
    return g.db


def close_db(_: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()

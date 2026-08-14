from __future__ import annotations

import argparse
import os
from datetime import timedelta
from pathlib import Path
from threading import Thread

from waitress import serve

from . import create_app
from .config import load_dotenv_file
from .scheduler import create_routine


DEMO_ROUTINES = [
    {"title": "아침 식사", "category": "meal", "scheduled_time": "08:00", "instructions": "식사 후 완료를 눌러 주세요."},
    {"title": "아침 약 복용", "category": "medication", "scheduled_time": "09:00", "instructions": "약 봉투를 확인하고 물과 함께 드세요."},
    {"title": "점심 식사", "category": "meal", "scheduled_time": "12:30", "instructions": "천천히 식사하세요."},
    {"title": "가벼운 실내 걷기", "category": "other", "scheduled_time": "15:00", "instructions": "불편하면 바로 쉬세요."},
    {"title": "저녁 약 복용", "category": "medication", "scheduled_time": "20:00", "instructions": "중복 복용하지 않도록 확인하세요."},
]
DEMO_PIN = "3017"


def seed_demo(app) -> None:
    with app.app_context():
        from .db import get_db

        if get_db().execute("SELECT 1 FROM routines LIMIT 1").fetchone() is None:
            for item in DEMO_ROUTINES:
                create_routine(item)


def reset_demo(app, preserve_auth: bool = False) -> None:
    """Replace mutable data with a predictable contest demonstration scene."""
    with app.app_context():
        from .auth import create_caregiver, token_hash
        from .clock import iso, now
        from .db import get_db
        from .risk import evaluate_risk
        from .scheduler import materialize_day

        database = get_db()
        scene_tables = (
            "alerts",
            "risk_assessments",
            "sensor_events",
            "sensor_module_state",
            "sensor_devices",
            "task_occurrences",
            "routines",
            "feedback_messages",
            "profile",
            "demo_state",
        )
        auth_tables = () if preserve_auth else ("api_tokens", "caregivers")
        for table in scene_tables + auth_tables:
            database.execute(f"DELETE FROM {table}")
        if not preserve_auth:
            database.execute(
                "DELETE FROM sqlite_sequence WHERE name IN "
                "('alerts','risk_assessments','sensor_events','sensor_devices','task_occurrences',"
                "'routines','api_tokens','caregivers','feedback_messages')"
            )
        timestamp = iso()
        database.execute(
            """
            INSERT INTO profile(id, user_name, birth_year, caregiver_name, caregiver_phone, locale, updated_at)
            VALUES (1, '김피움', 1952, '보호자', NULL, 'ko-KR', ?)
            """,
            (timestamp,),
        )
        database.commit()

        for item in DEMO_ROUTINES:
            create_routine(item)
        if not preserve_auth:
            create_caregiver("보호자", DEMO_PIN)
        materialize_day()
        completed = database.execute(
            "SELECT id FROM task_occurrences ORDER BY due_at, id LIMIT 2"
        ).fetchall()
        for row in completed:
            database.execute(
                "UPDATE task_occurrences SET status='completed', completed_at=? WHERE id=?",
                (timestamp, row["id"]),
            )

        current = now()
        database.execute(
            """
            INSERT INTO sensor_devices(device_uid, name, location, api_key_hash, created_at, last_seen_at)
            VALUES ('room_1', '거실 통합 센서', '거실', ?, ?, ?)
            ON CONFLICT(device_uid) DO UPDATE SET
              name=excluded.name,
              location=excluded.location,
              api_key_hash=excluded.api_key_hash,
              created_at=excluded.created_at,
              last_seen_at=excluded.last_seen_at
            """,
            (token_hash(app.config["DEMO_SENSOR_KEY"]), timestamp, timestamp),
        )
        sensor_id = database.execute(
            "SELECT id FROM sensor_devices WHERE device_uid='room_1'"
        ).fetchone()["id"]
        database.executemany(
            """
            INSERT INTO sensor_events(device_id, event_type, value, confidence, occurred_at, received_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, '{}')
            """,
            [
                (sensor_id, "pir_motion", 1, 0.98, iso(current - timedelta(minutes=3)), timestamp),
                (sensor_id, "heartbeat", 1, 1.0, timestamp, timestamp),
            ],
        )
        database.execute(
            """
            INSERT INTO demo_state(
              id, scenario_key, scenario_title, description, risk_score,
              risk_level, factors_json, user_message, activated_at
            ) VALUES (
              1, 'normal', '기본 상태', '오늘 일정과 최근 움직임이 등록된 초기 장면입니다.', 100,
              'normal', '[]', '현재 확인된 위험 신호가 없습니다.', ?
            )
            """,
            (timestamp,),
        )
        database.commit()
        evaluate_risk()


def main() -> None:
    # argparse의 기본 포트/TLS 값도 .env를 사용해야 하므로 앱 생성보다
    # 먼저 읽습니다. 실제 OS 환경 변수는 dotenv 로더가 덮어쓰지 않습니다.
    load_dotenv_file()
    parser = argparse.ArgumentParser(prog="piuda")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init", help="데이터베이스 초기화")
    init_parser.add_argument("--demo", action="store_true", help="시연용 일정을 추가")
    run_parser = subparsers.add_parser("run", help="웹 서버 실행")
    run_parser.add_argument("--host", default=os.getenv("PIUDA_HOST", "0.0.0.0"))
    run_parser.add_argument("--port", type=int, default=int(os.getenv("PIUDA_PORT", "8080")))
    run_parser.add_argument("--tls-port", type=int, default=int(os.getenv("PIUDA_TLS_PORT", "0")))
    run_parser.add_argument("--tls-cert", default=os.getenv("PIUDA_TLS_CERT", ""))
    run_parser.add_argument("--tls-key", default=os.getenv("PIUDA_TLS_KEY", ""))
    subparsers.add_parser("warm-ai", help="Ollama 모델을 메모리에 미리 적재")
    subparsers.add_parser("reload-ai", help="Ollama 모델을 내린 뒤 다시 적재")
    subparsers.add_parser("unload-ai", help="Ollama 모델을 메모리에서 해제")
    subparsers.add_parser("reset-demo", help="데이터를 지우고 시연 장면으로 초기화")
    args = parser.parse_args()

    if args.command == "run" and args.tls_port:
        missing = [
            label
            for label, value in (("TLS 인증서", args.tls_cert), ("TLS 개인키", args.tls_key))
            if not value or not Path(value).is_file()
        ]
        if missing:
            parser.error(f"{', '.join(missing)} 파일을 찾을 수 없습니다.")

    app = create_app()
    if args.command == "init":
        if args.demo:
            seed_demo(app)
        print(f"초기화 완료: {app.config['DATABASE']}")
        return
    if args.command == "warm-ai":
        from .integrations import warm_ollama

        with app.app_context():
            ready = warm_ollama()
        print("AI 예열 완료" if ready else "AI 예열 건너뜀")
        return
    if args.command == "reload-ai":
        from .integrations import reload_ollama

        with app.app_context():
            ready = reload_ollama()
        print("AI 재적재 완료" if ready else "AI 재적재 실패")
        return
    if args.command == "unload-ai":
        from .integrations import unload_ollama

        with app.app_context():
            unloaded = unload_ollama()
        print("AI 메모리 해제 완료" if unloaded else "AI 메모리 해제 건너뜀")
        return
    if args.command == "reset-demo":
        reset_demo(app)
        print(f"데모 초기화 완료: {app.config['DATABASE']}")
        return
    # 피우다는 대회 시연 전용입니다. 서버가 켜질 때마다 이전 기록과
    # 보호자 인증 정보를 지우고 동일한 데모 장면에서 시작합니다.
    if app.config.get("DEMO_MODE"):
        reset_demo(app)
        print(f"데모 자동 초기화 완료: {app.config['DATABASE']}")
    tls_server = None
    if args.tls_port and Path(args.tls_cert).is_file() and Path(args.tls_key).is_file():
        from werkzeug.serving import make_server

        tls_server = make_server(
            args.host,
            args.tls_port,
            app,
            threaded=True,
            ssl_context=(args.tls_cert, args.tls_key),
        )
        Thread(target=tls_server.serve_forever, name="piuda-https", daemon=True).start()
        print(f"HTTPS 준비 완료: {args.tls_port}번 포트")
    try:
        serve(app, host=args.host, port=args.port, threads=8)
    finally:
        if tls_server is not None:
            tls_server.shutdown()


if __name__ == "__main__":
    main()

from __future__ import annotations

from flask import Flask, jsonify

from .config import load_settings
from .db import close_db, init_database


def create_app(overrides: dict | None = None) -> Flask:
    settings = load_settings(overrides)
    app = Flask(__name__, instance_relative_config=False)
    app.json.ensure_ascii = False
    app.config.update(
        SECRET_KEY=settings.secret_key,
        DATABASE=str(settings.database_path),
        DATA_DIR=str(settings.data_dir),
        TIMEZONE=settings.timezone,
        OLLAMA_URL=settings.ollama_url,
        OLLAMA_MODEL=settings.ollama_model,
        OLLAMA_KEEP_ALIVE=settings.ollama_keep_alive,
        OLLAMA_TIMEOUT=settings.ollama_timeout,
        DEMO_MODE=settings.demo_mode,
        INACTIVITY_MINUTES=settings.inactivity_minutes,
        TASK_GRACE_MINUTES=settings.task_grace_minutes,
        KAKAO_ACCESS_TOKEN=settings.kakao_access_token,
        HOTSPOT_SSID=settings.hotspot_ssid,
        HOTSPOT_PASSWORD=settings.hotspot_password,
        HOTSPOT_GATEWAY=settings.hotspot_gateway,
        DEMO_SENSOR_KEY=settings.demo_sensor_key,
        CSI_MOTION_THRESHOLD=settings.csi_motion_threshold,
        CSI_STRONG_THRESHOLD=settings.csi_strong_threshold,
        STT_BINARY=str(settings.stt_binary),
        STT_MODEL=str(settings.stt_model),
        STT_VAD_MODEL=str(settings.stt_vad_model),
        STT_MIC_DEVICE=settings.stt_mic_device,
        STT_DURATION_SECONDS=settings.stt_duration_seconds,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=256 * 1024,
        JSON_AS_ASCII=False,
    )
    if overrides:
        app.config.update(overrides)

    init_database(app.config["DATABASE"])
    app.teardown_appcontext(close_db)

    from .api import api
    from .web import web

    app.register_blueprint(api)
    app.register_blueprint(web)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        return response

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "not_found"}), 404

    return app

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    secret_key: str
    timezone: str
    ollama_url: str
    ollama_model: str
    ollama_keep_alive: str
    ollama_timeout: float
    demo_mode: bool
    inactivity_minutes: int
    task_grace_minutes: int
    kakao_access_token: str | None
    hotspot_ssid: str
    hotspot_password: str
    hotspot_gateway: str
    demo_sensor_key: str
    csi_motion_threshold: float
    csi_strong_threshold: float


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE settings without overriding the real environment."""
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum() or not key[0].isalpha():
            raise ValueError(f".env {line_number}행 형식이 올바르지 않습니다.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_dotenv_file(path: str | Path | None = None) -> None:
    """Load the project .env early enough for CLI argument defaults to use it."""
    dotenv_path = Path(path or Path.cwd() / ".env").expanduser().resolve()
    _load_dotenv(dotenv_path)


def _load_or_create_secret(data_dir: Path) -> str:
    configured = os.getenv("PIUDA_SECRET_KEY")
    if configured:
        return configured

    secret_file = data_dir / "secret-key"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()

    secret = secrets.token_urlsafe(48)
    secret_file.write_text(secret, encoding="utf-8")
    secret_file.chmod(0o600)
    return secret


def load_settings(overrides: dict | None = None) -> Settings:
    overrides = overrides or {}
    dotenv_path = Path(overrides.get("DOTENV_PATH") or Path.cwd() / ".env").expanduser().resolve()
    load_dotenv_file(dotenv_path)
    data_dir = Path(
        overrides.get("DATA_DIR")
        or os.getenv("PIUDA_DATA_DIR")
        or Path.cwd() / "data"
    ).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    database_path = Path(
        overrides.get("DATABASE") or data_dir / "piuda.db"
    ).expanduser().resolve()

    return Settings(
        data_dir=data_dir,
        database_path=database_path,
        secret_key=str(overrides.get("SECRET_KEY") or _load_or_create_secret(data_dir)),
        timezone=str(overrides.get("TIMEZONE") or os.getenv("PIUDA_TIMEZONE", "Asia/Seoul")),
        ollama_url=str(overrides.get("OLLAMA_URL") or os.getenv("PIUDA_OLLAMA_URL", "http://127.0.0.1:11434")),
        ollama_model=str(overrides.get("OLLAMA_MODEL") or os.getenv("PIUDA_OLLAMA_MODEL", "hf.co/naver-ellm/HyperCLOVAX-SEED-Text-Instruct-1.5B-GGUF:Q4_K_M")),
        ollama_keep_alive=str(overrides.get("OLLAMA_KEEP_ALIVE") or os.getenv("PIUDA_OLLAMA_KEEP_ALIVE", "24h")),
        ollama_timeout=float(overrides.get("OLLAMA_TIMEOUT") or os.getenv("PIUDA_OLLAMA_TIMEOUT", "20")),
        demo_mode=str(overrides.get("DEMO_MODE", os.getenv("PIUDA_DEMO_MODE", "1"))).lower() in {"1", "true", "yes", "on"},
        inactivity_minutes=int(overrides.get("INACTIVITY_MINUTES") or os.getenv("PIUDA_INACTIVITY_MINUTES", "180")),
        task_grace_minutes=int(overrides.get("TASK_GRACE_MINUTES") or os.getenv("PIUDA_TASK_GRACE_MINUTES", "30")),
        kakao_access_token=overrides.get("KAKAO_ACCESS_TOKEN") or os.getenv("PIUDA_KAKAO_ACCESS_TOKEN") or None,
        hotspot_ssid=str(overrides.get("HOTSPOT_SSID") or os.getenv("PIUDA_HOTSPOT_SSID", "PIUDA-CNU")),
        hotspot_password=str(
            overrides.get("HOTSPOT_PASSWORD") or os.getenv("PIUDA_HOTSPOT_PASSWORD", "piuda3017")
        ),
        hotspot_gateway=str(
            overrides.get("HOTSPOT_GATEWAY") or os.getenv("PIUDA_HOTSPOT_GATEWAY", "192.168.4.1")
        ),
        demo_sensor_key=str(
            overrides.get("DEMO_SENSOR_KEY") or os.getenv("PIUDA_DEMO_SENSOR_KEY", "piuda-demo-3017")
        ),
        csi_motion_threshold=float(
            overrides.get("CSI_MOTION_THRESHOLD") or os.getenv("PIUDA_CSI_MOTION_THRESHOLD", "12")
        ),
        csi_strong_threshold=float(
            overrides.get("CSI_STRONG_THRESHOLD") or os.getenv("PIUDA_CSI_STRONG_THRESHOLD", "45")
        ),
    )

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unicodedata


LOGGER = logging.getLogger(__name__)
_SPEAK_LOCK = threading.Lock()
_SUPERTONIC_MODEL_FILES = (
    "duration_predictor.int8.onnx",
    "text_encoder.int8.onnx",
    "vector_estimator.int8.onnx",
    "vocoder.int8.onnx",
    "tts.json",
    "unicode_indexer.bin",
    "voice.bin",
)


def _supertonic_root() -> Path:
    return Path(
        os.getenv(
            "PIUDA_TTS_ROOT",
            Path.home() / ".local/lib/piuda-supertonic",
        )
    ).expanduser()


def _supertonic_paths() -> tuple[Path, Path, Path]:
    root = _supertonic_root()
    return root / "runtime/bin/sherpa-onnx-offline-tts", root / "runtime/lib", root / "model"


def _wave_player() -> str | None:
    return shutil.which("pw-play") or shutil.which("aplay")


def _mp3_player() -> str | None:
    return shutil.which("ffplay") or shutil.which("mpg123")


def local_neural_tts_available() -> bool:
    engine, _, model_dir = _supertonic_paths()
    return bool(
        engine.is_file()
        and os.access(engine, os.X_OK)
        and all((model_dir / name).is_file() for name in _SUPERTONIC_MODEL_FILES)
        and _wave_player()
    )


def local_tts_engine() -> str:
    if local_neural_tts_available():
        return "supertonic3"
    if importlib.util.find_spec("gtts") and _mp3_player():
        return "google"
    if shutil.which("espeak-ng") and _wave_player():
        return "espeak"
    return "unavailable"


def local_tts_available() -> bool:
    return local_tts_engine() != "unavailable"


def _clean_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "".join(character for character in text if character.isprintable())[:240]


def _audio_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return environment


def _cache_root() -> Path:
    root = Path(os.getenv("PIUDA_DATA_DIR", Path.cwd() / "data")) / "tts-cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cloud_cache_path(clean: str) -> Path:
    # Keep the original key so natural gTTS audio cached by earlier versions is reused.
    digest = hashlib.sha256(f"ko:{clean}".encode("utf-8")).hexdigest()[:24]
    return _cache_root() / f"{digest}.mp3"


def _play_audio(audio_path: Path) -> bool:
    if audio_path.suffix == ".mp3":
        player = _mp3_player()
        if not player:
            return False
        command = (
            [player, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", str(audio_path)]
            if Path(player).name == "ffplay"
            else [player, "-q", str(audio_path)]
        )
    else:
        player = _wave_player()
        if not player:
            return False
        command = (
            [player, str(audio_path)]
            if Path(player).name == "pw-play"
            else [player, "-q", str(audio_path)]
        )

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_audio_environment(),
        timeout=45,
        check=False,
    )
    return result.returncode == 0


def _play_cached_cloud_voice(clean: str) -> bool:
    audio_path = _cloud_cache_path(clean)
    return audio_path.is_file() and audio_path.stat().st_size > 0 and _play_audio(audio_path)


def _offline_neural_tts(clean: str) -> bool:
    if not local_neural_tts_available():
        return False

    engine, library_dir, model_dir = _supertonic_paths()
    speaker_id = max(0, min(int(os.getenv("PIUDA_TTS_SPEAKER_ID", "0")), 9))
    num_steps = max(2, min(int(os.getenv("PIUDA_TTS_NUM_STEPS", "8")), 12))
    speed = max(0.75, min(float(os.getenv("PIUDA_TTS_SPEED", "1.03")), 1.4))
    cache_key = f"supertonic3-ko:{speaker_id}:{num_steps}:{speed:.2f}:{clean}"
    digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:24]
    audio_path = _cache_root() / f"{digest}.wav"
    if audio_path.is_file() and audio_path.stat().st_size > 44:
        return _play_audio(audio_path)

    temporary = audio_path.with_suffix(".wav.part")
    environment = _audio_environment()
    previous_library_path = environment.get("LD_LIBRARY_PATH")
    environment["LD_LIBRARY_PATH"] = (
        f"{library_dir}:{previous_library_path}" if previous_library_path else str(library_dir)
    )
    command = [
        str(engine),
        f"--supertonic-duration-predictor={model_dir / 'duration_predictor.int8.onnx'}",
        f"--supertonic-text-encoder={model_dir / 'text_encoder.int8.onnx'}",
        f"--supertonic-vector-estimator={model_dir / 'vector_estimator.int8.onnx'}",
        f"--supertonic-vocoder={model_dir / 'vocoder.int8.onnx'}",
        f"--supertonic-tts-json={model_dir / 'tts.json'}",
        f"--supertonic-unicode-indexer={model_dir / 'unicode_indexer.bin'}",
        f"--supertonic-voice-style={model_dir / 'voice.bin'}",
        "--lang=ko",
        f"--sid={speaker_id}",
        f"--num-steps={num_steps}",
        "--num-threads=4",
        f"--speed={speed:.2f}",
        f"--output-filename={temporary}",
        clean,
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            timeout=90,
            check=False,
        )
        if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 44:
            LOGGER.warning("Offline neural Korean TTS failed: %s", result.stderr[-500:])
            return False
        temporary.replace(audio_path)
    finally:
        temporary.unlink(missing_ok=True)
    return _play_audio(audio_path)


def _cloud_tts(clean: str) -> bool:
    if not importlib.util.find_spec("gtts") or not _mp3_player():
        return False

    from gtts import gTTS

    audio_path = _cloud_cache_path(clean)
    if not audio_path.exists():
        temporary = audio_path.with_suffix(".mp3.part")
        try:
            gTTS(text=clean, lang="ko", slow=False, timeout=8).save(str(temporary))
            if temporary.stat().st_size <= 0:
                return False
            temporary.replace(audio_path)
        finally:
            temporary.unlink(missing_ok=True)
    return _play_audio(audio_path)


def _robotic_fallback(clean: str) -> None:
    engine = shutil.which("espeak-ng")
    if not engine:
        return
    subprocess.run(
        [engine, "-v", "ko", "-s", "145", "-a", "170", clean],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_audio_environment(),
        timeout=30,
        check=False,
    )


def _speak(text: str) -> None:
    clean = _clean_text(text)
    if not clean:
        return
    with _SPEAK_LOCK:
        try:
            if _play_cached_cloud_voice(clean):
                return
            if _offline_neural_tts(clean):
                return
            if _cloud_tts(clean):
                return
        except Exception:
            LOGGER.exception("Natural Korean TTS failed; using last-resort local fallback")
        _robotic_fallback(clean)


def speak_local_async(text: str) -> bool:
    if not local_tts_available() or not _clean_text(text):
        return False
    threading.Thread(target=_speak, args=(text,), daemon=True, name="piuda-tts").start()
    return True

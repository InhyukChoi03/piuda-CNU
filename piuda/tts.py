from __future__ import annotations

import logging
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unicodedata


LOGGER = logging.getLogger(__name__)
_SPEAK_LOCK = threading.Lock()


def local_tts_available() -> bool:
    natural = importlib.util.find_spec("gtts") and (shutil.which("ffplay") or shutil.which("mpg123"))
    fallback = shutil.which("espeak-ng") and (shutil.which("pw-play") or shutil.which("aplay"))
    return bool(natural or fallback)


def _clean_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "".join(character for character in text if character.isprintable())[:240]


def _audio_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return environment


def _natural_tts(clean: str) -> bool:
    if not importlib.util.find_spec("gtts"):
        return False
    player = shutil.which("ffplay") or shutil.which("mpg123")
    if not player:
        return False

    from gtts import gTTS

    cache_root = Path(os.getenv("PIUDA_DATA_DIR", Path.cwd() / "data")) / "tts-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"ko:{clean}".encode("utf-8")).hexdigest()[:24]
    audio_path = cache_root / f"{digest}.mp3"
    if not audio_path.exists():
        temporary = cache_root / f"{digest}.part"
        gTTS(text=clean, lang="ko", slow=False).save(str(temporary))
        temporary.replace(audio_path)

    command = (
        [player, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "quiet", str(audio_path)]
        if Path(player).name == "ffplay"
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
            if _natural_tts(clean):
                return
        except Exception:
            LOGGER.exception("Natural Korean TTS failed; using local fallback")
        _robotic_fallback(clean)


def speak_local_async(text: str) -> bool:
    if not local_tts_available() or not _clean_text(text):
        return False
    threading.Thread(target=_speak, args=(text,), daemon=True, name="piuda-tts").start()
    return True

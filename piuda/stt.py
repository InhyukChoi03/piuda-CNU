from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unicodedata

from flask import current_app


LOGGER = logging.getLogger(__name__)
_STT_LOCK = threading.Lock()


class LocalSttError(RuntimeError):
    pass


class LocalSttUnavailable(LocalSttError):
    pass


class LocalSttBusy(LocalSttError):
    pass


class LocalSttNoSpeech(LocalSttError):
    pass


def _required_paths() -> tuple[Path, Path, Path]:
    return (
        Path(current_app.config["STT_BINARY"]),
        Path(current_app.config["STT_MODEL"]),
        Path(current_app.config["STT_VAD_MODEL"]),
    )


def local_stt_available() -> bool:
    binary, model, vad_model = _required_paths()
    return bool(
        shutil.which("arecord")
        and binary.is_file()
        and os.access(binary, os.X_OK)
        and model.is_file()
        and vad_model.is_file()
    )


def _clean_transcript(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "".join(character for character in text if character.isprintable())[:500]


def transcribe_local(duration_seconds: int | None = None) -> str:
    if not _STT_LOCK.acquire(blocking=False):
        raise LocalSttBusy("이미 음성을 듣고 있습니다.")

    try:
        binary, model, vad_model = _required_paths()
        recorder = shutil.which("arecord")
        if (
            not recorder
            or not binary.is_file()
            or not os.access(binary, os.X_OK)
            or not model.is_file()
            or not vad_model.is_file()
        ):
            raise LocalSttUnavailable("로컬 음성인식 엔진 또는 모델을 찾을 수 없습니다.")

        duration = max(2, min(duration_seconds or int(current_app.config["STT_DURATION_SECONDS"]), 8))
        data_dir = Path(current_app.config["DATA_DIR"])
        temporary_root = data_dir / "stt-temp"
        temporary_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="listen-", dir=temporary_root) as directory:
            work = Path(directory)
            audio_path = work / "voice.wav"
            output_prefix = work / "transcript"
            try:
                capture = subprocess.run(
                    [
                        recorder,
                        "-q",
                        "-D",
                        str(current_app.config["STT_MIC_DEVICE"]),
                        "-f",
                        "S16_LE",
                        "-r",
                        "16000",
                        "-c",
                        "1",
                        "-d",
                        str(duration),
                        str(audio_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=duration + 5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise LocalSttUnavailable("USB 마이크로 녹음하지 못했습니다.") from error
            if capture.returncode != 0 or not audio_path.is_file():
                LOGGER.warning("USB microphone capture failed: %s", capture.stderr.strip())
                raise LocalSttUnavailable("USB 마이크로 녹음하지 못했습니다. 연결 상태를 확인해 주세요.")

            environment = os.environ.copy()
            library_path = str(binary.parent)
            if environment.get("LD_LIBRARY_PATH"):
                library_path += os.pathsep + environment["LD_LIBRARY_PATH"]
            environment["LD_LIBRARY_PATH"] = library_path
            try:
                inference = subprocess.run(
                    [
                        str(binary),
                        "-m",
                        str(model),
                        "-f",
                        str(audio_path),
                        "-l",
                        "ko",
                        "-t",
                        str(min(os.cpu_count() or 4, 4)),
                        "-nt",
                        "-np",
                        "-sns",
                        "--vad",
                        "-vm",
                        str(vad_model),
                        "-vt",
                        "0.5",
                        "-vspd",
                        "250",
                        "-otxt",
                        "-of",
                        str(output_prefix),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=environment,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise LocalSttUnavailable("음성을 글로 바꾸지 못했습니다.") from error
            if inference.returncode != 0:
                LOGGER.warning("Local whisper inference failed: %s", inference.stderr.strip())
                raise LocalSttUnavailable("음성을 글로 바꾸지 못했습니다. 다시 말씀해 주세요.")

            transcript_path = output_prefix.with_suffix(".txt")
            transcript = _clean_transcript(
                transcript_path.read_text(encoding="utf-8") if transcript_path.is_file() else inference.stdout
            )
            filler = transcript.replace(" ", "").rstrip(".?!~")
            if not transcript or filler in {"아", "어", "음", "으", "아우", "어어", "으음"}:
                raise LocalSttNoSpeech("목소리를 듣지 못했습니다. 마이크 가까이에서 다시 말씀해 주세요.")
            return transcript
    finally:
        _STT_LOCK.release()

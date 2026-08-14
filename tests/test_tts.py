from pathlib import Path
from types import SimpleNamespace

from piuda import tts


def prepare_supertonic(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "supertonic"
    engine = root / "runtime/bin/sherpa-onnx-offline-tts"
    engine.parent.mkdir(parents=True)
    engine.write_text("engine", encoding="utf-8")
    engine.chmod(0o755)
    model_dir = root / "model"
    model_dir.mkdir()
    for name in tts._SUPERTONIC_MODEL_FILES:
        (model_dir / name).write_text(name, encoding="utf-8")
    monkeypatch.setenv("PIUDA_TTS_ROOT", str(root))
    monkeypatch.setattr(tts, "_wave_player", lambda: "/usr/bin/pw-play")
    return root


def test_local_neural_tts_requires_runtime_model_and_player(tmp_path, monkeypatch):
    prepare_supertonic(tmp_path, monkeypatch)

    assert tts.local_neural_tts_available() is True
    assert tts.local_tts_engine() == "supertonic3"

    (tmp_path / "supertonic/model/voice.bin").unlink()
    assert tts.local_neural_tts_available() is False


def test_speak_uses_only_offline_neural_voice(tmp_path, monkeypatch):
    monkeypatch.setenv("PIUDA_DATA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(tts, "_offline_neural_tts", lambda clean: calls.append("offline") or True)

    tts._speak("안녕하세요.")

    assert calls == ["offline"]


def test_offline_neural_tts_generates_atomic_cache_with_korean_voice(tmp_path, monkeypatch):
    root = prepare_supertonic(tmp_path, monkeypatch)
    monkeypatch.setenv("PIUDA_DATA_DIR", str(tmp_path / "data"))
    commands = []

    def fake_run(command, **kwargs):
        commands.append((command, kwargs))
        output_option = next((item for item in command if item.startswith("--output-filename=")), None)
        if output_option:
            Path(output_option.split("=", 1)[1]).write_bytes(b"RIFF" + bytes(100))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    assert tts._offline_neural_tts("오늘 기분은 어떠세요?") is True

    synthesis = commands[0][0]
    assert synthesis[0] == str(root / "runtime/bin/sherpa-onnx-offline-tts")
    assert "--lang=ko" in synthesis
    assert "--sid=0" in synthesis
    assert "--num-steps=4" in synthesis
    assert "--speed=1.10" in synthesis
    assert synthesis[-1] == "오늘 기분은 어떠세요?"
    assert len(list((tmp_path / "data/tts-cache").glob("*.wav"))) == 1
    assert not list((tmp_path / "data/tts-cache").glob("*.part"))

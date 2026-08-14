from piuda.stt import LocalSttBusy, LocalSttNoSpeech, LocalSttUnavailable


def test_local_voice_endpoint_transcribes_only_for_pi_loopback(client, monkeypatch):
    monkeypatch.setattr("piuda.api.transcribe_local", lambda duration: f"{duration}초 음성 질문")

    response = client.post("/api/v1/voice/listen", json={"duration_seconds": 5})
    assert response.status_code == 200
    assert response.get_json()["transcript"] == "5초 음성 질문"

    remote = client.post(
        "/api/v1/voice/listen",
        json={"duration_seconds": 5},
        environ_base={"REMOTE_ADDR": "192.168.4.22"},
    )
    assert remote.status_code == 403


def test_local_voice_endpoint_validates_duration(client, monkeypatch):
    monkeypatch.setattr("piuda.api.transcribe_local", lambda duration: "질문")
    assert client.post("/api/v1/voice/listen", json={"duration_seconds": 1}).status_code == 400
    assert client.post("/api/v1/voice/listen", json={"duration_seconds": 9}).status_code == 400


def test_local_voice_endpoint_maps_runtime_errors(client, monkeypatch):
    cases = [
        (LocalSttBusy("이미 듣는 중"), 409, "voice_busy"),
        (LocalSttNoSpeech("목소리 없음"), 422, "no_speech"),
        (LocalSttUnavailable("엔진 없음"), 503, "local_stt_unavailable"),
    ]
    for error, status, code in cases:
        def fail(_duration, current_error=error):
            raise current_error

        monkeypatch.setattr("piuda.api.transcribe_local", fail)
        response = client.post("/api/v1/voice/listen", json={"duration_seconds": 5})
        assert response.status_code == status
        assert response.get_json()["error"] == code

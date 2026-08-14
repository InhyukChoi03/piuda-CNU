def test_web_pages_render(client):
    user = client.get("/")
    caregiver = client.get("/caregiver")
    install = client.get("/install")
    assert user.status_code == 200
    user_html = user.get_data(as_text=True)
    assert "오늘 일정" in user_html
    assert "나의 하루 동반자" not in user_html
    assert "카메라 없이 생활 신호만 확인합니다." not in user_html
    assert caregiver.status_code == 200
    caregiver_html = caregiver.get_data(as_text=True)
    assert "보호자 확인" in caregiver_html
    assert "안전한 로컬 연결" not in caregiver_html
    assert "같은 Wi-Fi · 실시간 알림" not in caregiver_html
    assert 'href="/demo"' not in caregiver_html
    assert install.status_code == 200
    install_html = install.get_data(as_text=True)
    assert "홈 화면에 추가" in install_html
    assert "무료 로컬 웹앱 · 7일 만료 없음" not in install_html
    assert "Apple 계정 결제 없이" not in install_html


def test_pwa_assets_have_install_metadata(client):
    manifest = client.get("/manifest.webmanifest")
    caregiver_manifest = client.get("/caregiver-manifest.webmanifest")
    service_worker = client.get("/service-worker.js")

    assert manifest.status_code == 200
    assert manifest.content_type.startswith("application/manifest+json")
    assert manifest.get_json()["display"] == "standalone"
    assert manifest.get_json()["icons"][1]["purpose"] == "any maskable"
    assert caregiver_manifest.status_code == 200
    assert caregiver_manifest.get_json()["start_url"] == "/caregiver"
    assert service_worker.status_code == 200
    assert service_worker.headers["Service-Worker-Allowed"] == "/"
    assert service_worker.headers["Cache-Control"] == "no-cache"
    assert "api/" in service_worker.get_data(as_text=True)
    worker_script = service_worker.get_data(as_text=True)
    assert 'const CACHE = "piuda-v23"' in worker_script
    assert 'url.pathname === "/caregiver"' in worker_script
    assert 'fetch(event.request, { cache: "no-store" })' in worker_script
    assert '"/caregiver",' not in worker_script


def test_dynamic_ui_copy_uses_actual_state_and_risk_threshold(client):
    script = client.get("/static/app.js").get_data(as_text=True)

    assert 'element.lastChild.textContent = "연결됨"' in script
    assert "로컬 연결됨" not in script
    assert "통화 중" not in script
    assert "RTCPeerConnection" not in script
    assert 'api("/caregiver-alert", { method: "POST" })' in script
    assert "30 * 60 * 1000" in script
    assert 'needsCheck ? "점검 필요"' in script
    assert "현재 확인된 위험 요인이 없습니다." in script


def test_caregiver_shows_live_peak_delta_instead_of_wifi_strength(client):
    caregiver = client.get("/caregiver").get_data(as_text=True)
    script = client.get("/static/app.js").get_data(as_text=True)

    assert "Peak Delta" in caregiver
    assert "Peak Delta · LIVE" in script
    assert "Wi-Fi 세기" not in script
    assert "data-sensor-peak-delta" in script
    assert "refreshSensors" in script
    assert "}, 1000);" in script


def test_browser_media_security_policy_is_sent(client):
    response = client.get("/caregiver")
    assert response.headers["Permissions-Policy"] == "microphone=(self), camera=()"
    assert response.headers["Cache-Control"] == "no-store"


def test_caregiver_install_uses_single_http_origin(client):
    caregiver = client.get("/caregiver").get_data(as_text=True)
    install = client.get("/install").get_data(as_text=True)
    script = client.get("/static/app.js").get_data(as_text=True)

    assert "인증서" not in caregiver
    assert "음성 통화" not in caregiver
    assert "보호자 음성 통화 준비" not in install
    assert "`${origin}/caregiver`" in script
    assert "8443" not in script
    assert 'updateViaCache: "none"' in script


def test_feedback_has_safe_local_fallback(client):
    response = client.post("/api/v1/feedback", json={"message": "지금 뭘 해야 해?"})
    assert response.status_code == 200
    result = response.get_json()
    assert result["reply"]
    assert result["speak"] is True


def test_user_script_periodically_refreshes_without_http_cache(client):
    script = client.get("/static/app.js").get_data(as_text=True)
    assert 'cache: "no-store"' in script
    assert "refreshUserSnapshot" in script
    assert "}, 2000);" in script


def test_pi_kiosk_uses_local_usb_microphone_endpoint(client):
    user_page = client.get("/").get_data(as_text=True)
    script = client.get("/static/app.js").get_data(as_text=True)

    assert "마이크 가까이에서 5초 안에 질문" in user_page
    assert "recordLocalVoice" in script
    assert 'api("/voice/listen", { method: "POST"' in script
    assert '$("#assistantForm").requestSubmit()' in script


def test_same_wifi_demo_console_and_caregiver_alert_ui(app, client):
    app.config["DEMO_MODE"] = True
    from piuda.cli import reset_demo

    reset_demo(app)
    page = client.get("/demo").get_data(as_text=True)
    user_page = client.get("/").get_data(as_text=True)
    script = client.get("/static/app.js").get_data(as_text=True)
    assert "발표 시나리오 제어실" in page
    assert "장면을 선택하면 사용자·보호자 화면이 2초 안에 갱신됩니다." in page
    assert page.count("data-trigger-scenario=") == 12
    assert 'id="caregiverAlertButton"' in user_page
    assert 'api("/caregiver-alert", { method: "POST" })' in script
    assert "triggerScenario" in script
    assert "notifyNewCaregiverAlert" in script
    assert "RTCPeerConnection" not in script
    assert "data-wellness-response" in user_page


def test_feedback_passes_recent_conversation_to_model(client, monkeypatch):
    captured = []

    def fake_feedback(message, context, history):
        captured.append((message, history))
        return "기억했어요."

    monkeypatch.setattr("piuda.api.ollama_feedback", fake_feedback)
    client.post("/api/v1/feedback", json={"message": "보리차를 마셨어요."})
    client.post("/api/v1/feedback", json={"message": "제가 뭘 마셨죠?"})

    assert captured[0][1] == []
    assert captured[1][1][-2:] == [
        {"role": "user", "content": "보리차를 마셨어요."},
        {"role": "assistant", "content": "기억했어요."},
    ]


def test_local_tts_only_accepts_loopback(client, monkeypatch):
    monkeypatch.setattr("piuda.api.speak_local_async", lambda text: bool(text))
    accepted = client.post("/api/v1/tts", json={"text": "안녕하세요."})
    denied = client.post(
        "/api/v1/tts",
        json={"text": "안녕하세요."},
        environ_overrides={"REMOTE_ADDR": "192.168.50.10"},
    )
    assert accepted.status_code == 202
    assert denied.status_code == 403

from piuda import integrations


SCHEDULE_CONTEXT = {
    "risk": {"score": 100, "level": "안심"},
    "pending_tasks": ["점심 식사", "가벼운 실내 걷기", "저녁 약 복용"],
    "tasks": [
        {"title": "아침 식사", "scheduled_time": "08:00", "status": "completed", "category": "meal"},
        {"title": "아침 약 복용", "scheduled_time": "09:00", "status": "completed", "category": "medication"},
        {"title": "점심 식사", "scheduled_time": "12:30", "status": "pending", "category": "meal"},
        {"title": "가벼운 실내 걷기", "scheduled_time": "15:00", "status": "pending", "category": "other"},
        {"title": "저녁 약 복용", "scheduled_time": "20:00", "status": "pending", "category": "medication"},
    ],
}


def test_common_schedule_question_bypasses_model(app, monkeypatch):
    monkeypatch.setattr(
        integrations,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    context = {"risk": {"score": 100, "level": "안심"}, "pending_tasks": ["아침 약 복용"]}
    with app.app_context():
        reply = integrations.ollama_feedback("지금 뭘 해야 해?", context)
    assert "아침 약 복용" in reply


def test_schedule_question_matches_requested_time(app, monkeypatch):
    monkeypatch.setattr(
        integrations,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    with app.app_context():
        at_eight = integrations.ollama_feedback("8시에 뭐 해야 해?", SCHEDULE_CONTEXT)
        at_three = integrations.ollama_feedback("오후 3시에는 무슨 일정이 있어?", SCHEDULE_CONTEXT)
        empty = integrations.ollama_feedback("오후 6시에 뭐가 있어?", SCHEDULE_CONTEXT)

    assert "아침 식사" in at_eight and "완료" in at_eight
    assert "가벼운 실내 걷기" in at_three
    assert "등록된 일정이 없어요" in empty


def test_all_pending_question_lists_every_incomplete_task(app, monkeypatch):
    monkeypatch.setattr(
        integrations,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    with app.app_context():
        reply = integrations.ollama_feedback("내가 완료 안 한 일정 다 말해봐", SCHEDULE_CONTEXT)

    assert "12:30 점심 식사" in reply
    assert "15:00 가벼운 실내 걷기" in reply
    assert "20:00 저녁 약 복용" in reply
    assert "아침 식사" not in reply


def test_model_request_disables_thinking_and_cleans_reply(app, monkeypatch):
    captured = {}

    def fake_post(url, payload, headers=None, timeout=30):
        captured.update({"url": url, "payload": payload, "timeout": timeout})
        return {"message": {"content": "<think>길게 생각함</think> **괜찮아요. 물을 한 잔 드셔 보세요.**"}}

    monkeypatch.setattr(integrations, "_post_json", fake_post)
    context = {"risk": {"score": 100, "level": "안심"}, "pending_tasks": []}
    history = [
        {"role": "user", "content": "아까 보리차를 마셨어요."},
        {"role": "assistant", "content": "수분을 잘 챙기셨네요."},
    ]
    with app.app_context():
        reply = integrations.ollama_feedback("보리차는 건강에 좋아요?", context, history)

    assert reply == "괜찮아요. 물을 한 잔 드셔 보세요."
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["think"] is False
    assert captured["payload"]["keep_alive"] == "24h"
    assert captured["payload"]["options"]["num_predict"] == 64
    assert captured["payload"]["options"]["num_ctx"] == 1024
    assert "사용자: 아까 보리차를 마셨어요." in captured["payload"]["messages"][0]["content"]
    assert captured["payload"]["messages"][1]["content"] == "보리차는 건강에 좋아요?"
    assert captured["timeout"] == 20


def test_explicit_recall_uses_saved_user_message_without_model(app, monkeypatch):
    monkeypatch.setattr(
        integrations,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    context = {"risk": {"score": 100, "level": "안심"}, "pending_tasks": []}
    history = [
        {"role": "user", "content": "오늘 물 대신 보리차를 마셨어요."},
        {"role": "assistant", "content": "잘 기억해 둘게요."},
    ]
    with app.app_context():
        reply = integrations.ollama_feedback("제가 아까 무엇을 마셨다고 했죠?", context, history)

    assert "보리차" in reply


def test_urgent_words_use_immediate_safety_reply(app, monkeypatch):
    monkeypatch.setattr(
        integrations,
        "_post_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    context = {"risk": {"score": 100, "level": "안심"}, "pending_tasks": []}
    with app.app_context():
        reply = integrations.ollama_feedback("가슴이 아프고 숨쉬기 힘들어요", context)
    assert "119" in reply


def test_unload_model_requests_immediate_release(app, monkeypatch):
    captured = {}

    def fake_post(url, payload, headers=None, timeout=30):
        captured.update({"url": url, "payload": payload, "timeout": timeout})
        return {"done": True}

    monkeypatch.setattr(integrations, "_post_json", fake_post)
    with app.app_context():
        unloaded = integrations.unload_ollama()

    assert unloaded is True
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["keep_alive"] == 0
    assert captured["payload"]["messages"] == []
    assert captured["timeout"] == 30

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app


TIME_QUERY = re.compile(r"(?:(오전|오후)\s*)?(\d{1,2})(?:\s*시(?:\s*(\d{1,2})\s*분)?|:(\d{2}))")


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: float = 30) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _task_items(context: dict) -> list[dict]:
    tasks = context.get("tasks") or []
    if tasks:
        return [dict(item) for item in tasks]
    return [
        {"title": title, "scheduled_time": "", "status": "pending"}
        for title in (context.get("pending_tasks") or [])
    ]


def _pending_items(context: dict) -> list[dict]:
    return [item for item in _task_items(context) if item.get("status") != "completed"]


def _time_question(message: str, tasks: list[dict]) -> str | None:
    compact = re.sub(r"\s+", "", message).lower()
    match = TIME_QUERY.search(message)
    if match is None or not any(keyword in compact for keyword in ("뭐", "무엇", "할", "해야", "일정", "있")):
        return None
    period, raw_hour, minute_with_si, minute_with_colon = match.groups()
    hour = int(raw_hour)
    minute_text = minute_with_si if minute_with_si is not None else minute_with_colon
    minute = int(minute_text) if minute_text is not None else None
    if period and not 1 <= hour <= 12:
        return None
    if period == "오전" and hour == 12:
        hour = 0
    elif period == "오후" and hour < 12:
        hour += 12
    if not 0 <= hour <= 23 or minute is not None and not 0 <= minute <= 59:
        return None

    matches = []
    for item in tasks:
        scheduled_time = str(item.get("scheduled_time", ""))
        try:
            task_hour, task_minute = map(int, scheduled_time.split(":"))
        except (TypeError, ValueError):
            continue
        if task_hour == hour and (minute is None or task_minute == minute):
            matches.append(item)

    label = f"{hour:02d}시" if minute is None else f"{hour:02d}:{minute:02d}"
    if not matches:
        return f"{label}에는 등록된 일정이 없어요."
    details = ", ".join(
        f"‘{item.get('title', '일정')}’({'완료' if item.get('status') == 'completed' else '미완료'})"
        for item in matches
    )
    return f"{label} 일정은 {details}입니다."


def _all_pending_reply(context: dict) -> str:
    pending = _pending_items(context)
    if not pending:
        return "완료하지 않은 일정은 없어요."
    details = ", ".join(
        f"{item.get('scheduled_time', '')} {item.get('title', '일정')}".strip()
        for item in pending
    )
    return f"미완료 일정: {details}."


def _next_task_reply(context: dict) -> str:
    pending = _pending_items(context)
    if not pending:
        return "현재 예정된 할 일은 모두 완료했어요."
    first = pending[0]
    scheduled_time = str(first.get("scheduled_time", "")).strip()
    prefix = f"{scheduled_time} " if scheduled_time else ""
    return f"다음 할 일은 {prefix}‘{first.get('title', '일정')}’입니다. 완료한 뒤 버튼을 눌러 주세요."


def _safe_fallback(context: dict) -> str:
    score = int(context.get("risk", {}).get("score", 100))
    level = str(context.get("risk", {}).get("level", ""))
    if score <= 20 or level in {"emergency", "긴급"}:
        return "지금은 움직이지 말고 안전한 곳에 계세요. 보호자에게 확인을 요청했습니다. 위급하면 119에 연락하세요."
    return _next_task_reply(context)


def _fast_feedback(message: str, context: dict) -> str | None:
    compact = re.sub(r"\s+", "", message).lower()
    score = int(context.get("risk", {}).get("score", 100))
    pending = context.get("pending_tasks") or []
    tasks = _task_items(context)

    if any(keyword in compact for keyword in ("숨쉬기힘들", "숨이안", "가슴이아", "쓰러", "넘어졌", "심하게다쳤")):
        return "지금은 119에 전화하고, 움직이지 말고 가까운 보호자에게 바로 알려 주세요."
    level = str(context.get("risk", {}).get("level", ""))
    if score <= 20 or level in {"emergency", "긴급"}:
        return _safe_fallback(context)
    time_reply = _time_question(message, tasks)
    if time_reply:
        return time_reply
    if any(keyword in compact for keyword in ("다음", "지금뭐", "지금뭘", "지금무엇", "뭐해야", "뭘해야", "무엇을해야")):
        return _next_task_reply(context)
    asks_all_pending = any(
        keyword in compact
        for keyword in ("완료안한", "완료하지않은", "미완료", "남은일정", "안끝낸")
    )
    if asks_all_pending:
        return _all_pending_reply(context)
    if any(keyword in compact for keyword in ("약", "복약")):
        medication = next((title for title in pending if "약" in title or "복용" in title), None)
        if medication:
            return f"‘{medication}’을 먼저 확인해 주세요. 드신 뒤 완료 버튼을 눌러 주세요."
    if any(keyword in compact for keyword in ("할일", "일정")):
        return _next_task_reply(context)
    if any(keyword in compact for keyword in ("상태", "괜찮", "위험")):
        level = context.get("risk", {}).get("level", "정상")
        return f"현재 생활 상태는 {level}, 건강 점수는 {score}점이에요. 화면의 안내를 천천히 따라 주세요."
    if any(keyword in compact for keyword in ("우울", "슬퍼", "힘들", "외로")):
        return "그런 마음이 드셨군요. 잠시 앉아 천천히 숨을 쉬고, 가까운 보호자에게 마음을 알려 주세요."
    return None


def _recall_recent_user_message(message: str, history: list[dict] | None) -> str | None:
    """Answer explicit recall questions from stored chat instead of guessing."""
    compact = re.sub(r"\s+", "", message).lower()
    asks_about_past = any(keyword in compact for keyword in ("아까", "방금", "전에", "기억"))
    asks_for_recall = any(keyword in compact for keyword in ("뭐", "무엇", "말했", "했지", "했죠", "기억"))
    if not history or not (asks_about_past and asks_for_recall):
        return None
    for item in reversed(history):
        if str(item.get("role", "")) != "user":
            continue
        content = _clean_reply(str(item.get("content", "")))[:70]
        if content:
            return f"아까 ‘{content}’라고 말씀하셨어요."
    return None


def _clean_reply(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[#*_`]+", "", text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"^[\s>\-]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \"'`")
    text = "".join(character for character in text if character.isprintable())
    if len(text) > 140:
        text = text[:137].rstrip() + "…"
    return text


def ollama_feedback(message: str, context: dict, history: list[dict] | None = None) -> str:
    immediate = _fast_feedback(message, context)
    if immediate:
        return immediate
    recalled = _recall_recent_user_message(message, history)
    if recalled:
        return recalled

    settings_url = current_app.config["OLLAMA_URL"].rstrip("/")
    risk = context.get("risk", {})
    pending = context.get("pending_tasks") or []
    pending_with_time = [
        f"{item.get('scheduled_time', '')} {item.get('title', '')}".strip()
        for item in _pending_items(context)
    ]
    memory_lines: list[str] = []
    for item in (history or [])[-6:]:
        role = str(item.get("role", ""))
        content = _clean_reply(str(item.get("content", "")))[:180]
        if role in {"user", "assistant"} and content:
            speaker = "사용자" if role == "user" else "피우다"
            memory_lines.append(f"{speaker}: {content}")
    memory_text = "\n최근 대화 기억:\n" + "\n".join(memory_lines) if memory_lines else ""
    messages = [{
        "role": "system",
        "content": (
            "당신은 다정하고 간결한 한국어 생활 도우미입니다. 이전 대화를 기억해 답하세요. "
            "쉬운 한국어 한 문장, 60자 이내로 끝까지 완성해 답하고, 생각 과정·마크다운·이모지는 쓰지 마세요. "
            f"현재 상태는 {risk.get('level', '안심')} · 건강 점수 {risk.get('score', 100)}점이고, "
            f"남은 일정은 {', '.join(pending_with_time) or ', '.join(pending) or '없음'}입니다."
            f"{memory_text}"
        ),
    }]
    messages.append({"role": "user", "content": message})
    try:
        result = _post_json(
            f"{settings_url}/api/chat",
            {
                "model": current_app.config["OLLAMA_MODEL"],
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": current_app.config["OLLAMA_KEEP_ALIVE"],
                "options": {
                    "temperature": 0,
                    "num_ctx": 1024,
                    "num_predict": 64,
                    "top_p": 0.8,
                    "repeat_penalty": 1.1,
                },
            },
            timeout=current_app.config["OLLAMA_TIMEOUT"],
        )
        text = _clean_reply(str(result.get("message", {}).get("content", "")))
        if text:
            return text
    except (OSError, ValueError, urllib.error.URLError):
        pass

    return _safe_fallback(context)


def warm_ollama() -> bool:
    settings_url = current_app.config["OLLAMA_URL"].rstrip("/")
    try:
        _post_json(
            f"{settings_url}/api/chat",
            {
                "model": current_app.config["OLLAMA_MODEL"],
                "messages": [],
                "stream": False,
                "think": False,
                "keep_alive": current_app.config["OLLAMA_KEEP_ALIVE"],
                "options": {"num_ctx": 1024},
            },
            timeout=90,
        )
        return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def unload_ollama() -> bool:
    """Unload Piuda's model so closing the kiosk releases its memory."""
    settings_url = current_app.config["OLLAMA_URL"].rstrip("/")
    try:
        _post_json(
            f"{settings_url}/api/chat",
            {
                "model": current_app.config["OLLAMA_MODEL"],
                "messages": [],
                "stream": False,
                "keep_alive": 0,
            },
            timeout=30,
        )
        return True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def reload_ollama() -> bool:
    unload_ollama()
    return warm_ollama()


def send_kakao_alert(text: str) -> bool:
    token = current_app.config.get("KAKAO_ACCESS_TOKEN")
    if not token:
        return False
    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": "http://CNU.local:8080/caregiver", "mobile_web_url": "http://CNU.local:8080/caregiver"},
        "button_title": "피우다 확인",
    }
    body = urllib.parse.urlencode({"template_object": json.dumps(template, ensure_ascii=False)}).encode()
    request = urllib.request.Request(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False

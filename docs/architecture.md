# 아키텍처 및 요구사항 대응

## 데이터 흐름

```text
ESP32 PIR / Wi-Fi CSI
        │  센서 API 키 + JSON
        ▼
Raspberry Pi 5 · Flask REST API
        ├─ Scheduler: 반복 일정 → 오늘 체크리스트
        ├─ Health Engine: 일정 + 센서 이벤트 → 0~100점 건강 점수
        ├─ Wellness Check: 낮 시간 무활동 → 사용자 확인 → 필요할 때만 보호자 알림
        ├─ AI Feedback: HyperCLOVA X SEED + DB 대화 기억 + 규칙 기반 폴백
        └─ SQLite WAL: 프로필·일정·센서·건강 상태·알림·대화
        │
        ├──────── 사용자 웹/PWA
        ├──────── 보호자 웹 대시보드 + 확인 팝업·알림음·진동
        ├──────── 발표 제어실 (/demo)
        └──────── SwiftUI iOS 앱 (CNU.local)
```

## 개발계획서 대응표

| 계획서 요구 | 구현 위치 |
| --- | --- |
| 보호자 일정 등록·반복 루틴 | `piuda/scheduler.py`, `/api/v1/routines` |
| 사용자 체크리스트·완료 저장 | `task_occurrences`, `/api/v1/tasks/today` |
| PIR/CSI 센서 수집 | `/api/v1/sensor-events`, `firmware/esp32_sensor` |
| 건강 점수화·단계 분류 | `piuda/risk.py` |
| 보호자 현황·미수행·알림 | `/caregiver`, `/api/v1/dashboard` |
| 로컬 LLM 피드백 | `piuda/integrations.py`, HyperCLOVA X SEED |
| STT/TTS | 웹 Speech API, iOS Speech/AVSpeech |
| 사용자 선확인·보호자 직접 알림 | `piuda/demo.py`, `/api/v1/caregiver-alert`, 보호자 PWA |
| WebView/스마트폰 확장 | 반응형 PWA + 네이티브 SwiftUI 앱 |
| Raspberry Pi 5 통합 | `deploy/piuda-kiosk.sh`, 바탕화면 실행 아이콘 |
| 사용자 인증·개인정보 최소화 | 보호자 PIN, bearer token, 센서별 키, 카메라 미사용 |

## REST API 요약

- `GET /api/v1/health`
- `POST /api/v1/auth/setup`, `POST /api/v1/auth/login`
- `GET|POST /api/v1/routines`
- `GET /api/v1/tasks/today`, `POST /api/v1/tasks/{id}/complete`
- `GET /api/v1/risk/current`, `GET /api/v1/risk/history`
- `POST /api/v1/sensors`, `POST /api/v1/sensor-events`
- `GET /api/v1/dashboard`, `GET /api/v1/alerts`
- `POST /api/v1/wellness-check`
- `POST /api/v1/caregiver-alert`
- `POST /api/v1/feedback`

보호자 변경·조회 API는 세션 또는 bearer token이 필요하며 센서 수집 API는 센서별 `X-Piuda-Sensor-Key`를 사용합니다.

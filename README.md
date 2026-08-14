# 피우다 2.0

초기 치매 환자와 경증 장애인의 일상 수행을 돕고 보호자의 확인 부담을 줄이는 Raspberry Pi 5 기반 자립생활지원 시스템입니다. 카메라 대신 ESP32 PIR·Wi-Fi CSI 신호와 일정 수행 정보로 0~100점 건강 점수를 계산합니다.

> 발표용 프로토타입입니다. 의료 진단이나 응급 대응 용도로 사용하지 마세요. 기본 보호자 PIN `3017`은 공개된 시연 값입니다.

## 현재 구현 범위

- 반복 일정 등록, 오늘 체크리스트 생성, 완료·미수행 기록
- 개발계획서 가중치를 100점에서 감점하는 건강 점수 엔진
- Raspberry Pi 고정 2.4GHz 핫스팟과 ESP32 자동 연결
- ESP32 통합 모듈의 PIR·Wi-Fi CSI·MLX90614 비접촉 온도 수집
- 안심/살펴보기/주의/긴급 4단계 상태 및 보호자 알림 기록
- 큰 글씨 사용자 화면과 PIN 보호 보호자 대시보드
- iPhone 홈 화면 설치를 지원하는 PWA와 QR 설치 안내
- 1024×600 Raspberry Pi 화면에 맞춘 무스크롤 키오스크 UI와 바탕화면 실행 아이콘
- 사용자 화면 2초 자동 동기화와 시간별·미완료 전체 일정 질의
- 보호자 화면 2초 자동 동기화, 큰 확인 알림과 알림음
- 낮 시간 장시간 무활동 시 사용자에게 먼저 묻고, 30초 미응답 시에만 보호자에게 알림
- `/demo` 발표 제어실의 3단계 생활 스토리 12개 장면
- 사용자 버튼·위험 감지로 생성되는 보호자 확인 알림과 30초 중복 방지
- Raspberry Pi에서 실측해 선택한 한국어 특화 HyperCLOVA X SEED 1.5B Q4, 최근 대화 기억과 즉시 안전 폴백
- 브라우저 STT, Raspberry Pi `gTTS` 한국어 음성 출력 및 iOS 온디바이스 음성 입출력
- SwiftUI iOS 앱, Keychain 토큰 저장, `192.168.4.1` 고정 로컬 연결
- Arduino-ESP32 3.3.11 통합 센서 펌웨어와 room_1/room_2 빌드 프로필
- 키오스크 창과 서버·AI가 함께 켜지고 닫히는 실행 구조와 SQLite WAL 저장

## 건강 점수 규칙

| 판단 항목 | 감점 |
| --- | ---: |
| 복약 체크 미수행 | -20 |
| 식사 체크 미수행 | -15 |
| 정해진 시간 이후에도 활동 미감지 | -25 |
| 장시간 PIR 미감지 | -30 |
| Wi-Fi CSI 기반 낙상 의심 패턴 | -50 |
| 야간 시간대 반복 움직임 | -20 |
| 일정 미수행 + 움직임 없음 | -40 |
| 센서 연결 끊김 | -25 |

평소에는 100점에서 시작하고 감지된 신호만큼 감점합니다. 76~100 안심, 51~75 살펴보기, 21~50 주의, 0~20 긴급으로 분류합니다. 이 판정은 의료 진단이 아니라 보호자의 확인 우선순위를 정하는 보조 정보입니다.

## 로컬 실행

Python 3.11 이상이 필요합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
piuda init --demo
piuda run
```

- 사용자 화면: `http://localhost:8080/`
- 보호자 화면: `http://localhost:8080/caregiver`
- 상태 확인: `http://localhost:8080/api/v1/health`
- 설치 안내: `http://localhost:8080/install`
- 발표 제어실: `http://localhost:8080/demo`

보호자 화면은 발표용 고정 PIN `3017`로 로그인합니다. 사용자 PIN 설정 단계는 사용하지 않습니다.

### 3인 발표 시연

Pi가 만든 `PIUDA-CNU` Wi-Fi에 ESP32와 발표 기기를 연결한 뒤 트리거 담당자는 `http://192.168.4.1:8080/demo`, 사용자 담당자는 Raspberry Pi 화면, 보호자 담당자는 `http://192.168.4.1:8080/caregiver`를 엽니다. 제어실의 12개 장면은 2초 안에 자동 반영됩니다. 사용자가 **보호자에게 알림 보내기**를 누르면 보호자 화면에 확인 팝업과 알림음·진동이 표시됩니다.

## Raspberry Pi 5

배포 대상 경로는 `/home/cnu/piuda`, 데이터 경로는 `/home/cnu/.local/share/piuda`입니다.

```bash
cd /home/cnu/piuda
./deploy/install.sh --demo
tail -f /home/cnu/.local/state/piuda/launcher.log
```

설치 스크립트는 NetworkManager에 `PIUDA-CNU`(비밀번호 `piuda3017`, 채널 6, Pi `192.168.4.1`) 프로필을 만들고 다음 부팅부터 자동 연결합니다. 현재 연결을 즉시 전환하려면 `./deploy/setup-hotspot.sh --activate`를 실행합니다. 이때 기존 Wi-Fi와 SSH는 끊깁니다.

환경값은 `/home/cnu/piuda/.env`에 설정할 수 있습니다. 비밀키와 카카오 토큰은 Git에 넣지 마세요. 발표용 핫스팟·센서 키는 장비의 무설정 연결을 위한 공개 시연 값이므로 실제 운영 전에 반드시 바꾸세요.

음성 질문을 사용하려면 Raspberry Pi에 USB 마이크 또는 마이크가 있는 USB 헤드셋이 필요합니다. Raspberry Pi 5 본체에는 내장 마이크가 없습니다. 텍스트 질문과 보호자 알림은 마이크 없이도 동작합니다.

피우다는 시연 전용으로 동작합니다. 바탕화면의 **피우다 실행** 아이콘을 누를 때마다 기존 DB의 일정 완료·센서·AI 대화·보호자 PIN·로그인 토큰을 지우고 같은 시연 장면을 다시 만듭니다. 이어서 HyperCLOVA X SEED 모델을 적재하고 Chromium 도구 모음과 작업표시줄을 가린 전체 화면으로 실행합니다. 직전 시연 기록은 보존되지 않습니다. `Alt+F4`로 Chromium 창을 닫으면 웹 서버와 AI 모델도 함께 종료되어 같은 네트워크의 `/demo`·보호자 화면도 더 이상 접속되지 않습니다. 보호자 PIN은 항상 `3017`입니다. 키오스크에서는 AI 답변과 일정 완료 메시지를 `gTTS` 계열의 자연스러운 한국어 음성으로 읽어 줍니다. 한/영 전환은 `Ctrl+Space` 또는 키보드의 한/영 키를 사용합니다.

### iPhone 홈 화면 설치

1. iPhone을 `PIUDA-CNU` Wi-Fi에 연결합니다. 비밀번호는 `piuda3017`입니다.
2. Safari에서 `http://192.168.4.1:8080/install`을 엽니다.
3. 사용자 또는 보호자 QR을 선택하거나 주소를 엽니다.
4. Safari 아래쪽의 **⋯ → 공유** 또는 공유 버튼에서 **홈 화면에 추가** → **웹 앱으로 열기** → **추가**를 누릅니다.

웹앱 설치 방식이므로 Apple Developer 계정이나 TestFlight는 사용하지 않습니다. 로컬 HTTP에서도 홈 화면 바로가기로 실행되지만, Service Worker 기반 오프라인 셸은 브라우저의 보안 정책상 HTTPS에서만 보장됩니다. 이 시스템의 일정·센서 데이터는 Raspberry Pi에 있으므로 Pi가 켜져 있고 같은 로컬 네트워크에 연결되어 있어야 합니다.

## ESP32

발표 구성은 통합 ESP32 두 대입니다. 별도의 CSI 송신기는 필요하지 않으며 Pi 핫스팟이 고정된 CSI 기준 신호가 됩니다.

- `room_1`: PIR + Wi-Fi CSI + MLX90614 주변/비접촉 표면 온도
- `room_2`: PIR + Wi-Fi CSI, 온도 센서 미장착

```bash
./deploy/flash-esp32.sh room_1 /dev/cu.usbserial-110
./deploy/flash-esp32.sh room_2 /dev/cu.usbserial-110
```

센서는 `PIUDA-CNU`와 `192.168.4.1`에 자동 연결합니다. 서버는 초당 원시값을 무한히 저장하지 않고 기기별 최신 상태를 갱신하며 PIR 전환·CSI 변화·1분 상태 신호만 이벤트로 남깁니다. CSI 강한 변화와 PIR 움직임이 동시에 확인된 경우에만 낙상 의심 후보를 생성합니다. 첫 30초 교정과 실제 발표 공간 임계값 점검이 필요하며 의료 판정값이 아닙니다. 자세한 내용은 `firmware/esp32_pir_ir_csi/README.md`에 있습니다.

## iOS

Xcode에서 `ios/Piuda/Piuda.xcodeproj`를 열고 Signing Team을 고른 뒤 실제 iPhone에 실행합니다. 앱은 기본적으로 Pi 핫스팟의 `http://192.168.4.1:8080`에 접속하며 설정 탭에서 바꿀 수 있습니다.

## 테스트

```bash
pytest --cov=piuda --cov-report=term-missing

DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer \
xcodebuild -project ios/Piuda/Piuda.xcodeproj -scheme Piuda \
  -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath /tmp/piuda-derived CODE_SIGNING_ALLOWED=NO build
```

## 저장소 보안

실제 토큰·센서 키·인증서·운영 데이터베이스는 저장소에 커밋하지 마세요. 로컬 설정은 `.env.example`을 복사해 `.env`에서 관리하며, 관련 파일은 `.gitignore`에서 제외합니다.

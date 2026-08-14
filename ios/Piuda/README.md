# 피우다 iOS

SwiftUI 네이티브 앱입니다. 기본 로컬 서버 주소는 Pi 핫스팟의 고정 주소 `http://192.168.4.1:8080`이며 설정 탭에서 바꿀 수 있습니다.

- 오늘 일정과 완료 처리
- 실시간 위험도와 판단 근거
- 보호자 PIN 로그인 및 알림 확인
- Keychain 기반 보호자 토큰 저장
- 온디바이스 한국어 음성 인식 우선 사용
- AVSpeechSynthesizer 한국어 읽어주기

Xcode에서 `Piuda.xcodeproj`를 열고 Signing Team을 선택한 뒤 실제 iPhone에 실행하세요. iPhone과 Pi는 같은 Wi-Fi에 있어야 합니다.

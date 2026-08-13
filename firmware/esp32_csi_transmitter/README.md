# Piuda Wi-Fi CSI 송신기

두 ESP32 사이에 일정한 CSI 측정용 패킷을 만들기 위한 ESP-IDF 5.2+ 프로젝트입니다. 이 보드는 센서 이벤트를 서버로 보내지 않고, 같은 Wi-Fi 채널에서 초당 일정한 ESP-NOW 브로드캐스트를 송신합니다. `../esp32_sensor` 수신기가 송신 MAC의 CSI만 골라 PIR과 함께 처리합니다.

## 설정과 업로드

```bash
cd firmware/esp32_csi_transmitter
idf.py set-target esp32
idf.py menuconfig
idf.py build flash monitor
```

`Piuda CSI transmitter configuration`에서 다음 값을 설정합니다.

- 수신기와 같은 2.4GHz Wi-Fi SSID와 비밀번호
- 송신기 station MAC: 기본 `1a:50:49:55:44:41`
- 초당 패킷 수: 기본 50

송신기 MAC은 실제 하드웨어 MAC을 덮어쓰는 로컬 관리 주소입니다. 수신기의 `CSI transmitter station MAC`과 반드시 같아야 합니다. 정상 동작하면 5초마다 `CSI probes` 로그가 증가합니다.

## 배치

송신기와 수신기를 1~3m 간격으로 고정하고 사람이 두 보드 사이를 지나가도록 배치합니다. 두 보드는 공유기를 통해 인터넷 데이터를 주고받는 것이 아니라, 공유기가 선택한 같은 채널에서 ESP-NOW 측정 패킷을 직접 송수신합니다. 측정 중에는 보드와 안테나를 움직이지 마세요.

이 코드는 CSI 측정 트래픽을 안정화하기 위한 송신기이며 낙상을 판별하지 않습니다. `esp32_sensor`의 임계값 판정 역시 의료 진단이 아닌 발표용 낙상 의심 신호입니다.

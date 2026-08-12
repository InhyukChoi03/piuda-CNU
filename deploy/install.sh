#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -un)" != "cnu" ]]; then
  echo "이 설치 스크립트는 Pi의 cnu 사용자로 실행하세요." >&2
  exit 1
fi

project_dir="/home/cnu/piuda"
if [[ "$(pwd)" != "$project_dir" ]]; then
  echo "프로젝트를 $project_dir 에 배치한 뒤 실행하세요." >&2
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3-flask python3-waitress python3-gtts mpg123 espeak-ng alsa-utils chromium curl avahi-daemon fcitx5 fcitx5-hangul fcitx5-frontend-gtk3 openssl
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama가 설치되어 있지 않습니다. https://ollama.com/download/linux 에서 설치한 뒤 다시 실행하세요." >&2
  exit 1
fi
sudo systemctl enable --now avahi-daemon
current_piuda_hostname="$(/usr/bin/hostname -s)"
if [[ "${current_piuda_hostname,,}" != "cnu" ]]; then
  echo "경고: 현재 호스트 이름은 '$current_piuda_hostname'입니다. CNU.local 주소를 쓰려면 'sudo hostnamectl set-hostname CNU' 후 재부팅하세요." >&2
fi
ollama pull hf.co/naver-ellm/HyperCLOVAX-SEED-Text-Instruct-1.5B-GGUF:Q4_K_M
mkdir -p /home/cnu/.local/share/piuda
mkdir -p /home/cnu/.config/fcitx5 /home/cnu/.config/autostart
install -m 0644 deploy/fcitx5-profile /home/cnu/.config/fcitx5/profile
install -m 0644 deploy/fcitx5-config /home/cnu/.config/fcitx5/config
install -m 0644 /usr/share/applications/org.fcitx.Fcitx5.desktop /home/cnu/.config/autostart/org.fcitx.Fcitx5.desktop

if [[ "${1:-}" == "--demo" ]]; then
  PIUDA_DATA_DIR=/home/cnu/.local/share/piuda PIUDA_DEMO_MODE=1 /usr/bin/python3 -m piuda.cli reset-demo
else
  PIUDA_DATA_DIR=/home/cnu/.local/share/piuda /usr/bin/python3 -m piuda.cli init
fi

chmod 0755 deploy/piuda-kiosk.sh deploy/ensure-tls.sh
PIUDA_DATA_DIR=/home/cnu/.local/share/piuda deploy/ensure-tls.sh
if ! arecord -l >/dev/null 2>&1; then
  echo "경고: 오디오 입력 장치를 찾지 못했습니다. 음성 통화 전 USB 마이크를 연결하세요." >&2
fi
install -m 0755 deploy/piuda.desktop /home/cnu/Desktop/Piuda.desktop
gio set /home/cnu/Desktop/Piuda.desktop metadata::trusted true || true

# 예전 버전의 자동 실행 서비스를 제거합니다. 이제 서버는 바탕화면 아이콘과 생명 주기를 같이합니다.
sudo systemctl disable --now piuda.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/piuda.service
sudo systemctl daemon-reload
sudo systemctl reset-failed piuda.service 2>/dev/null || true
echo "설치 완료: 바탕화면의 '피우다 실행'을 누르면 서버와 화면이 함께 켜집니다."

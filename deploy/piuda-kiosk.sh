#!/usr/bin/env bash
set -euo pipefail

health_url="http://127.0.0.1:8080/api/v1/health"
kiosk_url="http://127.0.0.1:8080/?kiosk=1"
profile_dir="/home/cnu/.config/piuda-chromium"
project_dir="/home/cnu/piuda"
data_dir="/home/cnu/.local/share/piuda"
model="hf.co/naver-ellm/HyperCLOVAX-SEED-Text-Instruct-1.5B-GGUF:Q4_K_M"
runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
state_dir="/home/cnu/.local/state/piuda"
log_file="$state_dir/launcher.log"
server_pid=""
browser_pid=""

mkdir -p "$runtime_dir" "$state_dir"

# 연속으로 누르더라도 서버와 Chromium은 하나만 실행합니다.
exec 9>"$runtime_dir/piuda-kiosk.lock"
if ! /usr/bin/flock -n 9; then
  exit 0
fi

piuda_env=(
  PIUDA_DATA_DIR="$data_dir"
  PIUDA_DEMO_MODE=1
  PIUDA_OLLAMA_MODEL="$model"
)

if [[ -f "$project_dir/.env" ]]; then
  # 애플리케이션이 직접 .env를 읽으므로 여기서는 값을 shell로 실행하지 않습니다.
  chmod go-rwx "$project_dir/.env" 2>/dev/null || true
fi

cleanup() {
  exit_code=$?
  trap - EXIT INT TERM HUP

  if [[ -n "$browser_pid" ]] && kill -0 "$browser_pid" 2>/dev/null; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$server_pid" 2>/dev/null || break
      /usr/bin/sleep 0.1
    done
    if kill -0 "$server_pid" 2>/dev/null; then
      kill -KILL "$server_pid" 2>/dev/null || true
    fi
    wait "$server_pid" 2>/dev/null || true
  fi

  /usr/bin/env "${piuda_env[@]}" \
    /usr/bin/python3 -m piuda.cli unload-ai >>"$log_file" 2>&1 || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM HUP

cd "$project_dir"

# Pi의 labwc에서는 Chromium의 native Wayland 입력 포커스가 끊기므로
# Fcitx GTK 입력기가 검증된 XWayland 경로를 사용하도록 고정합니다.
unset LC_ALL
export LANG=ko_KR.UTF-8
export LC_CTYPE=ko_KR.UTF-8
export XMODIFIERS=@im=fcitx
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export SDL_IM_MODULE=fcitx
pgrep -x fcitx5 >/dev/null || /usr/bin/fcitx5 -d

# 예전 배포의 백그라운드 서비스가 남아 있으면 동시 실행을 막습니다.
if /usr/bin/systemctl --quiet is-active piuda.service 2>/dev/null; then
  echo "기존 piuda.service가 실행 중입니다. deploy/install.sh를 한 번 실행해 주세요." >>"$log_file"
  exit 1
fi

# 서버는 이 창의 자식 프로세스입니다. 창이 닫히면 cleanup이 함께 종료합니다.
/usr/bin/env "${piuda_env[@]}" \
  /usr/bin/python3 -m piuda.cli run --host 0.0.0.0 --port 8080 \
    >>"$log_file" 2>&1 &
server_pid=$!

server_ready=0
for _ in $(seq 1 60); do
  if /usr/bin/curl --fail --silent --show-error "$health_url" >/dev/null; then
    server_ready=1
    break
  fi
  kill -0 "$server_pid" 2>/dev/null || break
  /usr/bin/sleep 0.5
done
if [[ "$server_ready" != "1" ]]; then
  echo "피우다 서버를 시작하지 못했습니다." >>"$log_file"
  exit 1
fi

if /usr/bin/arecord -l >>"$log_file" 2>&1; then
  # Keep the demo microphone below clipping; unsupported controls are skipped.
  /usr/bin/amixer -c Microphone cset name='Auto Gain Control' 0 >>"$log_file" 2>&1 || true
  /usr/bin/amixer -c Microphone cset name='Mic Capture Volume' 18 >>"$log_file" 2>&1 || true
else
  echo "안내: 음성 질문을 사용하려면 USB 마이크 또는 헤드셋을 연결하세요." >>"$log_file"
fi
if [[ ! -x /home/cnu/.local/lib/piuda-whisper/whisper-cli ]] \
  || [[ ! -f /home/cnu/.local/share/piuda/models/ggml-base.bin ]] \
  || [[ ! -f /home/cnu/.local/share/piuda/models/ggml-silero-v6.2.0.bin ]]; then
  echo "안내: deploy/install-local-stt.sh를 실행해 로컬 음성인식을 설치하세요." >>"$log_file"
fi
if [[ ! -x /home/cnu/.local/lib/piuda-supertonic/runtime/bin/sherpa-onnx-offline-tts ]] \
  || [[ ! -f /home/cnu/.local/lib/piuda-supertonic/model/voice.bin ]]; then
  echo "안내: deploy/install-local-tts.sh를 실행해 로컬 신경망 음성을 설치하세요." >>"$log_file"
fi

# 데모 DB는 서버 시작 시 초기화되며, AI는 이전 캐시를 비우고 다시 적재합니다.
/usr/bin/env "${piuda_env[@]}" \
  /usr/bin/python3 -m piuda.cli reload-ai >>"$log_file" 2>&1 || true

/usr/bin/chromium \
  --kiosk \
  --app="$kiosk_url" \
  --start-fullscreen \
  --enable-features=UseOzonePlatform \
  --ozone-platform=x11 \
  --no-first-run \
  --no-default-browser-check \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-component-update \
  --disable-features=Translate,MediaRouter \
  --use-fake-ui-for-media-stream \
  --force-device-scale-factor=1 \
  --user-data-dir="$profile_dir" &
browser_pid=$!

browser_exit=0
wait "$browser_pid" || browser_exit=$?
browser_pid=""
exit "$browser_exit"

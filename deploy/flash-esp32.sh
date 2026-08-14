#!/usr/bin/env bash
set -euo pipefail

profile="${1:-}"
serial_port="${2:-}"
sketch_dir="$(cd "$(dirname "$0")/../firmware/esp32_pir_ir_csi" && pwd)"

if [[ "$profile" != "room_1" && "$profile" != "room_2" ]]; then
  echo "사용법: $0 room_1|room_2 [/dev/cu.usbserial-...]" >&2
  exit 2
fi
if ! command -v arduino-cli >/dev/null 2>&1; then
  echo "arduino-cli가 필요합니다." >&2
  exit 1
fi
if [[ -z "$serial_port" ]]; then
  serial_port="$(ls -t /dev/cu.usbserial-* 2>/dev/null | head -1 || true)"
fi
if [[ -z "$serial_port" || ! -e "$serial_port" ]]; then
  echo "연결된 ESP32 직렬 포트를 찾지 못했습니다." >&2
  exit 1
fi

if [[ "$profile" == "room_1" ]]; then
  build_flags='-DPIUDA_SENSOR_ID="room_1" -DPIUDA_HAS_IR_SENSOR=1'
else
  build_flags='-DPIUDA_SENSOR_ID="room_2" -DPIUDA_HAS_IR_SENSOR=0'
fi

arduino-cli compile \
  --fqbn esp32:esp32:esp32 \
  --board-options UploadSpeed=115200 \
  --upload \
  --port "$serial_port" \
  --build-property "compiler.cpp.extra_flags=$build_flags" \
  "$sketch_dir"

echo "$profile 펌웨어 업로드 완료: $serial_port"

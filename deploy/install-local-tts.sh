#!/usr/bin/env bash
set -euo pipefail

runtime_version="v1.13.5"
runtime_archive="sherpa-onnx-${runtime_version}-linux-aarch64-shared-cpu.tar.bz2"
runtime_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/${runtime_version}/${runtime_archive}"
runtime_sha256="f38b97f478c4196d2f3279f847a3de62672d0d64b3845df9bae83bb5f48d0d34"
runtime_binary_sha256="b5e76fbe6c483897d7945752d332287412426a4a07a441d7cbb940d732b634db"

model_name="sherpa-onnx-supertonic-3-tts-int8-2026-05-11"
model_archive="${model_name}.tar.bz2"
model_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/${model_archive}"
model_sha256="82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427"
voice_sha256="67d5209b0ee8ce6c74105ffbe12fe6a7628aea3b4ba2fcb308a4a67938a93ce8"

install_root="${PIUDA_TTS_ROOT:-/home/cnu/.local/lib/piuda-supertonic}"
runtime_root="$install_root/runtime"
model_root="$install_root/model"
binary_path="$runtime_root/bin/sherpa-onnx-offline-tts"
voice_path="$model_root/voice.bin"

matches_sha256() {
  local expected="$1"
  local path="$2"
  [[ -f "$path" ]] && [[ "$(sha256sum "$path" | cut -d' ' -f1)" == "$expected" ]]
}

model_is_complete() {
  local filename
  for filename in \
    duration_predictor.int8.onnx \
    text_encoder.int8.onnx \
    vector_estimator.int8.onnx \
    vocoder.int8.onnx \
    tts.json \
    unicode_indexer.bin \
    voice.bin; do
    [[ -f "$model_root/$filename" ]] || return 1
  done
}

if matches_sha256 "$runtime_binary_sha256" "$binary_path" \
  && matches_sha256 "$voice_sha256" "$voice_path" \
  && model_is_complete; then
  echo "로컬 한국어 신경망 음성이 이미 준비되어 있습니다."
  exit 0
fi

temporary_dir="$(mktemp -d /tmp/piuda-tts.XXXXXX)"
trap 'rm -rf "$temporary_dir"' EXIT

copy_or_download_verified() {
  local source_path="$1"
  local url="$2"
  local destination="$3"
  local expected="$4"
  if [[ -n "$source_path" ]]; then
    cp "$source_path" "$destination"
  else
    curl --fail --location --retry 3 --output "$destination" "$url"
  fi
  printf '%s  %s\n' "$expected" "$destination" | sha256sum --check --status
}

# 인터넷이 없는 Pi에는 두 환경값으로 Mac에서 옮긴 아카이브를 지정할 수 있습니다.
copy_or_download_verified \
  "${PIUDA_TTS_RUNTIME_ARCHIVE:-}" \
  "$runtime_url" \
  "$temporary_dir/$runtime_archive" \
  "$runtime_sha256"
copy_or_download_verified \
  "${PIUDA_TTS_MODEL_ARCHIVE:-}" \
  "$model_url" \
  "$temporary_dir/$model_archive" \
  "$model_sha256"

mkdir -p "$temporary_dir/runtime" "$temporary_dir/model" "$runtime_root" "$model_root"
tar -xjf "$temporary_dir/$runtime_archive" -C "$temporary_dir/runtime" --strip-components=1
tar -xjf "$temporary_dir/$model_archive" -C "$temporary_dir/model" --strip-components=1
test -x "$temporary_dir/runtime/bin/sherpa-onnx-offline-tts"
matches_sha256 "$runtime_binary_sha256" "$temporary_dir/runtime/bin/sherpa-onnx-offline-tts"
matches_sha256 "$voice_sha256" "$temporary_dir/model/voice.bin"
cp -a "$temporary_dir/runtime/." "$runtime_root/"
cp -a "$temporary_dir/model/." "$model_root/"

echo "로컬 한국어 신경망 음성 설치 완료: sherpa-onnx $runtime_version + Supertonic 3 INT8"

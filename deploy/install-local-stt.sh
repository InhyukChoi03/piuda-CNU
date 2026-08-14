#!/usr/bin/env bash
set -euo pipefail

whisper_version="v1.9.2"
archive_name="whisper-bin-ubuntu-arm64.tar.gz"
archive_url="https://github.com/ggml-org/whisper.cpp/releases/download/${whisper_version}/${archive_name}"
archive_sha256="7e26fa6a36d9174d5c0bf033ccbc026c3b5e569e2ee787058241346ef5392719"
binary_sha256="00cf54e258e9c7560666e5ae7d16e01ee02210b9ee5e943172e7df5f2ece4c80"
model_url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
model_sha256="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"
vad_url="https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin"
vad_sha256="2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987"

install_root="/home/cnu/.local/lib/piuda-whisper"
model_root="/home/cnu/.local/share/piuda/models"
binary_path="$install_root/whisper-cli"
model_path="$model_root/ggml-base.bin"
vad_path="$model_root/ggml-silero-v6.2.0.bin"

matches_sha256() {
  local expected="$1"
  local path="$2"
  [[ -f "$path" ]] && [[ "$(sha256sum "$path" | cut -d' ' -f1)" == "$expected" ]]
}

if matches_sha256 "$binary_sha256" "$binary_path" \
  && matches_sha256 "$model_sha256" "$model_path" \
  && matches_sha256 "$vad_sha256" "$vad_path"; then
  echo "로컬 한국어 음성인식이 이미 준비되어 있습니다."
  exit 0
fi

temporary_dir="$(mktemp -d /tmp/piuda-stt.XXXXXX)"
trap 'rm -rf "$temporary_dir"' EXIT

download_verified() {
  local url="$1"
  local destination="$2"
  local expected="$3"
  curl --fail --location --retry 3 --output "$destination" "$url"
  printf '%s  %s\n' "$expected" "$destination" | sha256sum --check --status
}

download_verified "$archive_url" "$temporary_dir/$archive_name" "$archive_sha256"
download_verified "$model_url" "$temporary_dir/ggml-base.bin" "$model_sha256"
download_verified "$vad_url" "$temporary_dir/ggml-silero-v6.2.0.bin" "$vad_sha256"

mkdir -p "$temporary_dir/extracted" "$install_root" "$model_root"
tar -xzf "$temporary_dir/$archive_name" -C "$temporary_dir/extracted" --strip-components=1
test -x "$temporary_dir/extracted/whisper-cli"
matches_sha256 "$binary_sha256" "$temporary_dir/extracted/whisper-cli"
cp -a "$temporary_dir/extracted/." "$install_root/"
install -m 0644 "$temporary_dir/ggml-base.bin" "$model_path"
install -m 0644 "$temporary_dir/ggml-silero-v6.2.0.bin" "$vad_path"

echo "로컬 한국어 음성인식 설치 완료: whisper.cpp $whisper_version + base + Silero VAD"

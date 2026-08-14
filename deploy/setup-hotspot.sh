#!/usr/bin/env bash
set -euo pipefail

profile_name="piuda-hotspot"
wifi_interface="${PIUDA_WIFI_INTERFACE:-wlan0}"
hotspot_ssid="${PIUDA_HOTSPOT_SSID:-PIUDA-CNU}"
hotspot_password="${PIUDA_HOTSPOT_PASSWORD:-piuda3017}"
hotspot_gateway="${PIUDA_HOTSPOT_GATEWAY:-192.168.4.1}"
activate="${1:-}"

if ! command -v nmcli >/dev/null 2>&1; then
  echo "NetworkManager(nmcli)가 필요합니다." >&2
  exit 1
fi
if (( ${#hotspot_password} < 8 || ${#hotspot_password} > 63 )); then
  echo "핫스팟 비밀번호는 8~63자여야 합니다." >&2
  exit 1
fi
if [[ ! "$hotspot_gateway" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "PIUDA_HOTSPOT_GATEWAY가 IPv4 주소 형식이 아닙니다." >&2
  exit 1
fi

if ! sudo nmcli -t -f NAME connection show | grep -Fxq "$profile_name"; then
  sudo nmcli connection add \
    type wifi \
    ifname "$wifi_interface" \
    con-name "$profile_name" \
    ssid "$hotspot_ssid"
fi

sudo nmcli connection modify "$profile_name" \
  connection.interface-name "$wifi_interface" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100 \
  connection.autoconnect-retries 0 \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  802-11-wireless.channel 6 \
  802-11-wireless.hidden no \
  802-11-wireless.powersave 2 \
  802-11-wireless-security.key-mgmt wpa-psk \
  802-11-wireless-security.proto rsn \
  802-11-wireless-security.pairwise ccmp \
  802-11-wireless-security.group ccmp \
  802-11-wireless-security.psk "$hotspot_password" \
  ipv4.method shared \
  ipv4.addresses "$hotspot_gateway/24" \
  ipv6.method disabled

sudo nmcli connection reload
echo "핫스팟 설정 완료: $hotspot_ssid · Pi $hotspot_gateway · 채널 6"

if [[ "$activate" == "--activate" ]]; then
  echo "핫스팟을 전환합니다. 기존 Wi-Fi와 SSH 연결이 끊길 수 있습니다."
  sudo nmcli connection up "$profile_name"
elif [[ -n "$activate" ]]; then
  echo "사용법: $0 [--activate]" >&2
  exit 2
fi

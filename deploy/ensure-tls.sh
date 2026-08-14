#!/usr/bin/env bash
set -euo pipefail

tls_dir="${PIUDA_DATA_DIR:-/home/cnu/.local/share/piuda}/tls"
ca_key="$tls_dir/piuda-ca-key.pem"
ca_cert="$tls_dir/piuda-ca.crt"
ca_ext="$tls_dir/piuda-ca-ext.cnf"
server_key="$tls_dir/piuda-server-key.pem"
server_cert="$tls_dir/piuda-server.crt"
server_csr="$tls_dir/piuda-server.csr"
server_ext="$tls_dir/piuda-server-ext.cnf"

mkdir -p "$tls_dir"
chmod 0700 "$tls_dir"

if [[ ! -s "$ca_key" || ! -s "$ca_cert" ]]; then
  /usr/bin/printf '%s\n' \
    '[req]' \
    'distinguished_name=dn' \
    '[dn]' \
    '[v3_ca]' \
    'basicConstraints=critical,CA:TRUE' \
    'keyUsage=critical,keyCertSign,cRLSign' \
    'subjectKeyIdentifier=hash' >"$ca_ext"
  /usr/bin/openssl genrsa -out "$ca_key" 2048
  /usr/bin/openssl req -x509 -new -sha256 -days 3650 \
    -key "$ca_key" -out "$ca_cert" \
    -subj "/CN=Piuda Local Demo CA/O=Piuda" \
    -config "$ca_ext" -extensions v3_ca
  rm -f "$ca_ext"
fi

if [[ ! -s "$server_key" ]]; then
  /usr/bin/openssl genrsa -out "$server_key" 2048
fi

# CNU.local뿐 아니라 현재 Pi IP로도 HTTPS 통화를 열 수 있게 SAN을 구성합니다.
# 이미 설치된 인증서가 유효하면 그대로 유지합니다. 부팅할 때마다 서버
# 인증서가 바뀌면 서버 인증서 자체를 설치한 기존 iPhone에서 통화 출처를
# 다시 신뢰해야 하고, 서비스워커가 캐시 화면만 보여 줄 수 있기 때문입니다.
subject_alt="DNS:CNU.local,DNS:cnu.local"
required_sans=("DNS:CNU.local" "DNS:cnu.local")
for address in $(/usr/bin/hostname -I 2>/dev/null || true); do
  if [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$address" != 127.* ]]; then
    subject_alt="$subject_alt,IP:$address"
    required_sans+=("IP Address:$address")
  fi
done

renew_server_cert=0
if [[ ! -s "$server_cert" ]]; then
  renew_server_cert=1
elif [[ "$(/usr/bin/openssl x509 -noout -modulus -in "$server_cert")" != \
        "$(/usr/bin/openssl rsa -noout -modulus -in "$server_key" 2>/dev/null)" ]]; then
  renew_server_cert=1
elif ! /usr/bin/openssl verify -CAfile "$ca_cert" "$server_cert" >/dev/null 2>&1; then
  renew_server_cert=1
elif ! /usr/bin/openssl x509 -checkend 2592000 -noout -in "$server_cert" >/dev/null 2>&1; then
  renew_server_cert=1
else
  certificate_text=$(/usr/bin/openssl x509 -noout -text -in "$server_cert")
  for required_san in "${required_sans[@]}"; do
    if [[ "$certificate_text" != *"$required_san"* ]]; then
      renew_server_cert=1
      break
    fi
  done
fi

if [[ "$renew_server_cert" == "1" ]]; then
  /usr/bin/openssl req -new -sha256 \
    -key "$server_key" -out "$server_csr" \
    -subj "/CN=CNU.local/O=Piuda"
  /usr/bin/printf '%s\n' \
    'basicConstraints=critical,CA:FALSE' \
    'keyUsage=critical,digitalSignature,keyEncipherment' \
    'extendedKeyUsage=serverAuth' \
    "subjectAltName=$subject_alt" >"$server_ext"
  /usr/bin/openssl x509 -req -sha256 -days 365 \
    -in "$server_csr" -CA "$ca_cert" -CAkey "$ca_key" -CAcreateserial \
    -extfile "$server_ext" -out "$server_cert"
  rm -f "$server_csr" "$server_ext"
fi

chmod 0600 "$ca_key" "$server_key"
chmod 0644 "$ca_cert" "$server_cert"

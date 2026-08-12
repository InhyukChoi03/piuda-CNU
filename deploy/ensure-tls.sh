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

# 핫스팟이 바뀌어도 현재 Pi IP로 HTTPS 통화를 열 수 있도록 매 실행 시
# 동일한 로컬 CA로 짧은 서버 인증서만 다시 서명합니다.
subject_alt="DNS:CNU.local,DNS:cnu.local"
for address in $(/usr/bin/hostname -I 2>/dev/null || true); do
  if [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$address" != 127.* ]]; then
    subject_alt="$subject_alt,IP:$address"
  fi
done

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

chmod 0600 "$ca_key" "$server_key"
chmod 0644 "$ca_cert" "$server_cert"

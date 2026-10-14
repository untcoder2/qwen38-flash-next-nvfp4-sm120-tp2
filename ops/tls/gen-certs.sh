#!/usr/bin/env bash
# Генерация CA + серверного сертификата для TLS инфера (SAN на 127.0.0.1 для
# health-чеков вотчдога с того же хоста). Повторный запуск без --force не
# перетирает существующий CA. Ключи никогда не покидают хост; клиентам
# раздаётся только ca.pem.
set -euo pipefail
D=/home/molochko/penny/tls
mkdir -p "$D"; cd "$D"
if [[ -f ca.pem && "${1:-}" != "--force" ]]; then
  echo "ca.pem уже есть — пропускаю (пересоздать: $0 --force)"; ls -l; exit 0
fi
umask 077
[[ -f ca.key ]] || openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem \
  -days 3650 -subj "/CN=QwenHome-CA/O=llm-d-llm" -addext "basicConstraints=critical,CA:TRUE"
openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr \
  -subj "/CN=qwen-infer" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  -addext "subjectAltName=IP:192.168.2.127,IP:127.0.0.1,DNS:localhost"
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -out server.pem \
  -days 825 -copy_extensions copy -sha256
chmod 600 ca.key server.key
rm -f server.csr
echo OK; ls -l
openssl x509 -in server.pem -noout -text | grep -A1 "Subject Alternative Name"

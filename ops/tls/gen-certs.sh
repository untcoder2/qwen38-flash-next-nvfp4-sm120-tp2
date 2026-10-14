#!/usr/bin/env bash
# Генерация CA + серверного сертификата для TLS инфера (SAN на 127.0.0.1 для
# health-чеков вотчдога с того же хоста). Повторный запуск без --force не
# перетирает существующий CA. Ключи никогда не покидают хост; клиентам
# раздаётся только ca.pem.
set -euo pipefail
D=/home/molochko/penny/tls
mkdir -p "$D"; cd "$D"

CA_SUBJ="/CN=QwenHome-CA/O=llm-d-llm"
CA_DAYS=3650
# keyUsage на корне обязателен: без него Python 3.14 / OpenSSL 3.5 отбраковывают CA
# при строгой проверке (VERIFY_X509_STRICT по умолчанию), curl — нет.
CA_EXT=(-addext "basicConstraints=critical,CA:TRUE"\
        -addext "keyUsage=critical,keyCertSign,cRLSign"\
        -addext "subjectKeyIdentifier=hash")

# --reissue-ca: перевыпустить КОРЕНЬ с тем же ключом (долечить расширения).
# SKI = hash от публичного ключа, не меняется => ранее выданный server.pem остаётся
# валидным против нового корня, рестарт инфера НЕ нужен. Старым клиентам новый
# ca.pem доступ не ломает; раздаём его как обновление доверия.
if [[ "${1:-}" == "--reissue-ca" ]]; then
  umask 077
  [[ -f ca.key ]] || { echo "нет ca.key - перевыпуск невозможен"; exit 1; }
  [[ -f server.pem ]] || { echo "нет server.pem - нечего проверять"; exit 1; }
  cp -a ca.pem "ca.pem.old-$(date +%Y%m%d-%H%M%S)"
  openssl req -new -x509 -key ca.key -days "$CA_DAYS" -subj "$CA_SUBJ" "${CA_EXT[@]}" -out ca.pem.new
  if openssl verify -CAfile ca.pem.new server.pem >/dev/null 2>&1; then
    mv ca.pem.new ca.pem
    echo "OK: корень перевыпущен с прежним ключом"
    openssl x509 -in ca.pem -noout -text | grep -A1 -E "Key Usage|Basic Constraints|Subject Key Identifier"
  else
    rm -f ca.pem.new; echo "ЦЕПОЧКА ПОРВАНА - прежний ca.pem не тронут"; exit 1
  fi
  exit 0
fi

if [[ -f ca.pem && "${1:-}" != "--force" ]]; then
  echo "ca.pem уже есть — пропускаю (пересоздать: $0 --force)"; ls -l; exit 0
fi
umask 077
[[ -f ca.key ]] || openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days "$CA_DAYS" -subj "$CA_SUBJ" "${CA_EXT[@]}"
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

#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Generates a self-signed TLS cert on first start (outside /data, so it never
# touches the AOF volume) and always launches redis-server in TLS-only mode.
# Clients connect with rediss:// and ssl_cert_reqs=none, which encrypts the
# channel without validating the cert against a CA - a cert that regenerates
# across restarts is fine.
set -eu

: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"

CERT_DIR=/etc/redis-certs
mkdir -p "$CERT_DIR"

if [ ! -f "$CERT_DIR/redis.crt" ]; then
  openssl req -new -x509 -days 3650 -nodes -text \
    -out "$CERT_DIR/redis.crt" -keyout "$CERT_DIR/redis.key" \
    -subj "/CN=redis.railway.internal"
fi

exec redis-server \
  --port 0 \
  --tls-port 6379 \
  --tls-cert-file "$CERT_DIR/redis.crt" \
  --tls-key-file "$CERT_DIR/redis.key" \
  --tls-ca-cert-file "$CERT_DIR/redis.crt" \
  --tls-auth-clients no \
  --requirepass "$REDIS_PASSWORD" \
  --appendonly yes \
  --dir /data

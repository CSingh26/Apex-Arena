#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# Wraps the official postgres entrypoint to always serve TLS. Certs live
# outside PGDATA (regenerated on every container start) so this never touches
# the mounted data volume: production connects with sslmode=require, which
# only asks for an encrypted channel and never validates the certificate
# against a CA, so a self-signed cert that changes across restarts is fine.
set -eu

CERT_DIR=/etc/postgresql-certs
mkdir -p "$CERT_DIR"

if [ ! -f "$CERT_DIR/server.crt" ]; then
  openssl req -new -x509 -days 3650 -nodes -text \
    -out "$CERT_DIR/server.crt" -keyout "$CERT_DIR/server.key" \
    -subj "/CN=postgres.railway.internal"
fi

chmod 600 "$CERT_DIR/server.key"
chown postgres:postgres "$CERT_DIR/server.crt" "$CERT_DIR/server.key"

exec docker-entrypoint.sh "$@" \
  -c ssl=on \
  -c ssl_cert_file="$CERT_DIR/server.crt" \
  -c ssl_key_file="$CERT_DIR/server.key"

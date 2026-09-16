#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only
#
# The TLS cert is baked into the image at build time (see Dockerfile), not
# generated here at container start: a from-scratch openssl req was observed
# hanging silently for minutes under this container's runtime constraints
# (likely musl's fully-buffered stderr masking a slow/stalled RNG read, with
# no incremental progress output the way glibc's openssl showed on the
# Postgres image). Clients connect with rediss:// and ssl_cert_reqs=none,
# which encrypts the channel without validating the cert against a CA, so a
# cert that is identical across image builds rather than unique per
# container is fine for this use.
set -eu

: "${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"

exec redis-server \
  --port 0 \
  --tls-port 6379 \
  --tls-cert-file /etc/redis-certs/redis.crt \
  --tls-key-file /etc/redis-certs/redis.key \
  --tls-ca-cert-file /etc/redis-certs/redis.crt \
  --tls-auth-clients no \
  --requirepass "$REDIS_PASSWORD" \
  --appendonly yes \
  --dir /data

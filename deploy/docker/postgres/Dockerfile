# SPDX-License-Identifier: AGPL-3.0-only
#
# Apex Arena production PostgreSQL image.
#
# Built on the official Debian-based postgres:16 image (not -alpine) on
# purpose: the production data volume was initialized under a glibc
# (Debian/pgdg) initdb, and swapping to a musl-libc (Alpine) binary against
# the same on-disk data directory risks silent collation drift on any
# locale-sensitive index. Stay on the same libc family the volume was born on.

# The base image's own /usr/local/bin/gosu (used only for the entrypoint's
# root-to-postgres privilege drop, no network I/O of its own) is a static Go
# binary carrying a CRITICAL crypto/tls advisory in its Go toolchain. gosu
# upstream has not cut a release against a patched Go yet, so rebuild it here
# from the same pinned source tag with a current toolchain instead of waiting.
FROM golang:1.25-alpine AS gosu-builder
RUN apk add --no-cache git
RUN git clone --depth 1 --branch 1.19 https://github.com/tianon/gosu.git /gosu
WORKDIR /gosu
RUN CGO_ENABLED=0 go build -ldflags '-s -w' -o /out/gosu .

FROM postgres:16

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gosu-builder /out/gosu /usr/local/bin/gosu
RUN chmod +x /usr/local/bin/gosu

COPY docker-entrypoint-ssl.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-ssl.sh

ENTRYPOINT ["docker-entrypoint-ssl.sh"]
CMD ["postgres"]

# SPDX-License-Identifier: AGPL-3.0-only
#
# Apex Arena production PostgreSQL image.
#
# Built on the official Debian-based postgres:16 image (not -alpine) on
# purpose: the production data volume was initialized under a glibc
# (Debian/pgdg) initdb, and swapping to a musl-libc (Alpine) binary against
# the same on-disk data directory risks silent collation drift on any
# locale-sensitive index. Stay on the same libc family the volume was born on.
FROM postgres:16

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/*

COPY docker-entrypoint-ssl.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-ssl.sh

ENTRYPOINT ["docker-entrypoint-ssl.sh"]
CMD ["postgres"]

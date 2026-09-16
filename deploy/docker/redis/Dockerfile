# SPDX-License-Identifier: AGPL-3.0-only
#
# Apex Arena production Redis image.
#
# Bakes TLS setup into the image's own ENTRYPOINT instead of relying on a
# platform-supplied startCommand: Railway does not apply a custom
# startCommand override to an image-sourced service, so the previous
# plain redis:7.4-alpine + startCommand approach silently booted with no TLS
# at all. Baking the cert generation and redis-server invocation into the
# image itself means Railway just runs the image's own default command.
FROM redis:7.4-alpine

RUN apk add --no-cache openssl

COPY docker-entrypoint-tls.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-tls.sh

ENTRYPOINT ["docker-entrypoint-tls.sh"]

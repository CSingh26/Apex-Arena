# SPDX-License-Identifier: AGPL-3.0-only
FROM node:22-alpine AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder

WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_APP_NAME="Apex Arena"
ARG NEXT_PUBLIC_APP_URL=http://localhost:3000
ARG NEXT_PUBLIC_APP_BASE_PATH=""
ENV NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \
    NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL \
    NEXT_PUBLIC_APP_BASE_PATH=$NEXT_PUBLIC_APP_BASE_PATH

RUN npm run build

FROM node:22-alpine AS runtime

ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app

# The base image lags Alpine security updates between rebuilds, same as the
# backend's Debian base. Apply pending package fixes here rather than waiting
# on the upstream tag to refresh.
RUN apk update && apk upgrade --no-cache

RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs

# The runtime container only ever runs `node server.js`; npm is never invoked.
# The base image's own bundled npm install carries its own dependency tree
# (including a vendored tar release with a published CRITICAL advisory) that
# the image scan flags regardless of whether it is reachable. Remove it here
# rather than waiting on an upstream base-image rebuild.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]

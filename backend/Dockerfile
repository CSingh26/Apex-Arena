# SPDX-License-Identifier: AGPL-3.0-only
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The slim base lags Debian security updates between image rebuilds, and the
# release scan fails on any fixed CRITICAL package. Apply pending OS security
# fixes at build time rather than waiting for the upstream tag to be refreshed.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system apex && adduser --system --ingroup apex apex

COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --require-hashes -r requirements.lock

USER apex

EXPOSE 8000

STOPSIGNAL SIGTERM

CMD ["python", "-m", "app.runtime"]

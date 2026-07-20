# SPDX-License-Identifier: AGPL-3.0-only
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system apex && adduser --system --ingroup apex apex

COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/scripts ./scripts
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./

RUN pip install --upgrade pip && pip install .

USER apex

EXPOSE 8000

STOPSIGNAL SIGTERM

CMD ["python", "-m", "app.runtime"]

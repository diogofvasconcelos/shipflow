# Build from the repository root:  docker build -f infra/Dockerfile .
FROM python:3.12-slim

WORKDIR /srv/backend

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY frontend /srv/frontend

RUN pip install --no-cache-dir .

EXPOSE 8000

# WORKDIR is /srv/backend, so the default relative paths in app.core.config
# (../frontend/templates, ./data/labels) resolve exactly as they do locally.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

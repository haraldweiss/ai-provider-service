# SPDX-License-Identifier: AGPL-3.0-or-later
# © 2026 Harald Weiss
#
# Single-stage build for ai-provider-service.
# Using Debian Bookworm (glibc 2.36+) for compatibility with cryptography
# native bindings and other binary wheels.

FROM docker.io/library/python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 8767

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8767/health || exit 1

CMD ["gunicorn", "--workers", "2", "--worker-class", "sync", "--bind", "0.0.0.0:8767", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:create_app()"]

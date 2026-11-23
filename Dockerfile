# SPDX-License-Identifier: AGPL-3.0-or-later
# © 2026 Harald Weiss
#
# Multi-stage build for ai-provider-service.
# Matching Debian/Python bases keep the compiled wheel native to the runtime.

# Build natively on the same Python/Debian base as the runtime stage.
FROM docker.io/library/python:3.12-slim AS crypto-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config libffi-dev rustc cargo perl curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
COPY security/cryptography/build-wheel.sh /usr/local/bin/build-crypto-wheel
RUN bash /usr/local/bin/build-crypto-wheel 50.0.2 /crypto-build

FROM docker.io/library/python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl gosu && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
COPY security/cryptography/constraints.txt security/cryptography/verify-runtime.py /opt/cryptography/
COPY --from=crypto-builder /crypto-build/wheels/ /opt/crypto-wheels/
RUN pip install --no-cache-dir -c /opt/cryptography/constraints.txt -r requirements.txt && \
    (cd /opt/crypto-wheels && sha256sum --check SHA256SUMS) && \
    pip install --no-cache-dir --no-index --no-deps --force-reinstall /opt/crypto-wheels/*.whl && \
    python /opt/cryptography/verify-runtime.py --expected-cryptography 50.0.2 && \
    pip check && \
    rm -rf /opt/crypto-wheels

COPY . .

RUN mkdir -p /app/data && \
    useradd -m -r appuser && \
    chown -R appuser:appuser /app

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8767

HEALTHCHECK --interval=30s --timeout=20s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8767/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["gunicorn", "--workers", "2", "--worker-class", "gthread", "--threads", "4", "--bind", "0.0.0.0:8767", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-", "app:create_app()"]

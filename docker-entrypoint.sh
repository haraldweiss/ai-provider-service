#!/bin/bash
set -e

chown -R appuser:appuser /app/data

exec "$@"

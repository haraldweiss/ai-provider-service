#!/bin/bash
set -e

if [ "$(id -u)" = '0' ]; then
    chown -R appuser:appuser /app/data
    exec gosu appuser "$@"
fi

exec "$@"

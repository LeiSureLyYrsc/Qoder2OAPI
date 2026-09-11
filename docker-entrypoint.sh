#!/bin/sh
set -e

DATA_DIR="${QODER2OAPI_DATA_DIR:-/app/data}"

mkdir -p "$DATA_DIR"

if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appgroup "$DATA_DIR"
    if ! gosu appuser:appgroup test -w "$DATA_DIR"; then
        echo "[docker-entrypoint] ERROR: Data directory '$DATA_DIR' is not writable by appuser (UID/GID 10001)." >&2
        echo "[docker-entrypoint] Check the mounted directory permissions on the host." >&2
        exit 1
    fi
    exec gosu appuser:appgroup "$@"
fi

if ! [ -w "$DATA_DIR" ]; then
    echo "[docker-entrypoint] ERROR: Data directory '$DATA_DIR' is not writable by UID $(id -u)." >&2
    echo "[docker-entrypoint] Check the mounted directory permissions on the host." >&2
    exit 1
fi

exec "$@"

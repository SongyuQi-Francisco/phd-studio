#!/usr/bin/env bash
# PhD Studio: http://127.0.0.1:8770
set -e
cd "$(dirname "$0")"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
exec "$UV" run uvicorn app.main:app --host 127.0.0.1 --port 8770 "$@"

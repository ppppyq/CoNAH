#!/usr/bin/env bash
set -euo pipefail

TARGET=${1:-zh}
PORT=${2:-${SPHINX_AUTOBUILD_PORT:-8001}}
HOST=${SPHINX_AUTOBUILD_HOST:-127.0.0.1}
export LC_ALL=C.UTF-8
export LANG=C.UTF-8

if [ "$TARGET" != "en" ] && [ "$TARGET" != "zh" ]; then
  echo "Usage: bash autobuild.sh [en|zh] [port]" >&2
  exit 2
fi

sphinx-build -W --keep-going "source-$TARGET" "build/html-$TARGET" && \
  sphinx-autobuild -W --watch _static --host "$HOST" --port "$PORT" \
    "source-$TARGET" "build/html-$TARGET"

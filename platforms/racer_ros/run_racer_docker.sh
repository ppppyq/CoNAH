#!/usr/bin/env bash
# Launch the CoNAH RACER ROS Noetic + RViz container with --network host and X11 forwarding.
set -euo pipefail

IMAGE="${RACER_IMAGE:-conah-racer:noetic}"
CONTAINER="${RACER_CONTAINER:-conah-racer-sim}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if command -v xhost >/dev/null 2>&1; then
  xhost +local:docker >/dev/null 2>&1 || true
fi

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

exec docker run --rm -it \
  --name "$CONTAINER" \
  --network host \
  --ipc host \
  -e DISPLAY="${DISPLAY:-:0}" \
  -e QT_X11_NO_MITSHM=1 \
  -e PYTHONPATH="/workspace/CoNAH" \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v "${REPO_ROOT}:/workspace/CoNAH:rw" \
  "$IMAGE" \
  "${@:-bash}"

#!/bin/sh
set -eu

JOBSLAYER_INIT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
JOBSLAYER_INIT_SCRIPT="$JOBSLAYER_INIT_ROOT/scripts/bootstrap.py"

if [ -n "${JOBSLAYER_BOOTSTRAP_PYTHON:-}" ]; then
    if [ ! -f "$JOBSLAYER_BOOTSTRAP_PYTHON" ]; then
        echo "configured bootstrap Python does not exist: $JOBSLAYER_BOOTSTRAP_PYTHON" >&2
        exit 127
    fi
    exec "$JOBSLAYER_BOOTSTRAP_PYTHON" "$JOBSLAYER_INIT_SCRIPT" "$@"
fi

if [ -x "$JOBSLAYER_INIT_ROOT/.venv/bin/python" ]; then
    exec "$JOBSLAYER_INIT_ROOT/.venv/bin/python" "$JOBSLAYER_INIT_SCRIPT" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
    exec python3 "$JOBSLAYER_INIT_SCRIPT" "$@"
fi

if command -v python >/dev/null 2>&1; then
    exec python "$JOBSLAYER_INIT_SCRIPT" "$@"
fi

echo "JobSlayer initialization requires Python 3.11 or newer." >&2
echo "Install Python, or set JOBSLAYER_BOOTSTRAP_PYTHON to an existing interpreter." >&2
exit 127

#!/bin/sh
set -eu
chronicle_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$chronicle_root"
chronicle_python=python3
if [ -x .venv/bin/python ]; then chronicle_python=.venv/bin/python; fi
exec "$chronicle_python" -m chronicle.launch --open "$@"

#!/bin/sh
set -eu
chronicle_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$chronicle_root"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mac]'
.venv/bin/python -m chronicle.doctor
printf '\nStart: ./run.sh\nDemo:  ./run.sh --demo\n'

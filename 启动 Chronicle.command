#!/bin/zsh
set -eu
chronicle_root="$(cd -- "$(dirname -- "$0")" && pwd)"
cd -- "$chronicle_root"
if [[ -f data/workbench.json ]]; then
  exec ./run.sh --config data/workbench.json --home data/browser
fi
exec ./run.sh

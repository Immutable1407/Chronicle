# Agent setup / 给 Agent 的启动说明

The repository can be run without packaging an application. Use project-local dependencies; do not install reference applications or modify the user's WeChat automatically.

## Demo first

```sh
python3 --version  # 3.11+
./run.sh --demo
```

The demo has no optional dependencies and never reads WeChat. Its voice sample is generated silence with explicitly artificial manual text. It is suitable for screenshots and UI experiments. Keep it separate from personal archives.

## Mac source setup

```sh
brew install sqlcipher zstd ffmpeg
./scripts/setup.sh
.venv/bin/python -m chronicle.doctor
./run.sh
```

Apple tools may need `xcode-select --install`; the user completes Apple's installer. `setup.sh` creates only the repository's `.venv` and installs the editable package and optional SILK decoder. It does not run Homebrew, log in, acquire credentials or initialize WeChat. Do not run Chronicle as root.

`SQLCIPHER_LIBRARY` and `ZSTD_LIBRARY` can point to compatible local libraries if not using Homebrew's standard paths. Use native arm64 Python for the supported Mac connection. Use the Python interpreter reported by the local virtual environment for voice decoding.

## Existing development archive

A private legacy config can be supplied without moving its archive:

```sh
./run.sh --config /absolute/path/to/private-workbench.json --home /absolute/path/to/browser-state
```

Config paths are relative to the config file. Optional keys: `archive`, `wechat_keys`, `codec_python`. `wechat_keys` must point to a private verified Chronicle manifest; never add it to source control. Keep a consistent `--home` for a workspace. The launcher reopens an existing process for that home; use a different home and port for a different workspace.

The Chinese Finder launcher uses a local `data/workbench.json` when present, otherwise the default workspace. It is just a source launcher and needs the prerequisites above.

## Verify

```sh
python3 -m unittest discover -s tests -q
python3 -m unittest discover -s tools/chronicle_mac -p 'test_*.py' -q
node --check chronicle/web/app.js
```

The second suite requires macOS and the local libraries. The core suite supports Linux and skips the Mac database fixture. An optional GitHub Actions template is in `docs/examples/core-tests.yml`; enabling it requires workflow-write authorization. Tests do not initialize a real account. For UI work, check first use, project creation, cross-chat selection, duplicate import, file view, manual note and narrow-screen layout in a real browser. State what was verified; do not equate synthetic tests with real-account acceptance.

To stop, use `Ctrl+C` in the server terminal. Do not use broad process-name kills. Before publishing, assemble and review the allowlisted snapshot; private local history and research notes are deliberately excluded.

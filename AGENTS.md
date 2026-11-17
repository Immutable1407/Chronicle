# Chronicle contributor instructions

- Read README.md and docs/architecture.md before changing boundaries.
- The current product is source-first and targets macOS Apple Silicon. App/DMG packaging is deferred. Do not treat old native-wrapper experiments as the supported entry point.
- Start the UI with `./run.sh --demo`. Use synthetic fixtures for tests and screenshots. Do not initialize, quit or log into a real WeChat account without the user's explicit instruction.
- Treat source databases as read-only. Never mutate chat history to make a test pass.
- Preserve stable account/conversation/message identity, duplicate protection, raw observations, attachment integrity and explicit missing states.
- Source adapters belong in chronicle/macos/. Keep archive and transcription contracts platform-independent; see docs/windows-handoff.md.
- No secret values in browser payloads, CLI arguments, logs, screenshots, issue text or Git. New Mac connections belong in Keychain. Runtime and user data are outside tracked source.
- Run `python3 -m unittest discover -s tests -q` for backend changes. Mac-specific changes also need synthetic adapter fixtures from tools/chronicle_mac/. Do not claim real-account initialization passed from mocks.
- Verify UI changes in a real browser. No frontend build is needed.
- Public source is assembled with `python3 scripts/publish_snapshot.py --output .publish/Chronicle`. This is a curated snapshot; private research/acceptance documents and local Git history are not publication inputs. Review the result before pushing. The script does not create repositories or push.
- Project status and supported workflows are documented in README.md, docs/architecture.md and docs/connection.md. Keep both README languages consistent.

- Schema 3 retains old message IDs through aliases. Read docs/account-identity.md before changing identity or migration code; use docs/mac-hardening.md for the paged HTTP contract.
- Never label ordinary workspaces encrypted. Native encrypted homes are optional, described in docs/encrypted-workspace.md; do not ask for or log the user’s password.
- First-connection compatibility is explicitly gated by the validated client version/build. Expand it only with corresponding real-platform evidence; a synthetic debugger fixture is not a new WeChat-version acceptance.

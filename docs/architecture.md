# Architecture and current scope

Chronicle archives selected project communication. It does not mirror every conversation into a project automatically.

## Boundaries

| Area | Implementation | Contract |
| --- | --- | --- |
| Browser workbench | `chronicle/web/` | Project first, cross-conversation selection, timeline, dates, files and manual records |
| Source launcher | `chronicle/launch.py` | Loopback service, one-time entry tickets, HttpOnly session, existing connection reuse and setup actions |
| Connection lifecycle | `chronicle/desktop_backend.py` | Preflight, explicit initialization, state reporting, recovery and profile management; shared with an earlier native experiment |
| macOS credential storage | `chronicle/keychain.py` | Apple Security API; settings contain profile identifiers rather than key material |
| Mac source | `chronicle/macos/` | Account-local database reader, source identities, resource resolution and explicit connection helpers |
| Archive | `chronicle/archive.py` | SQLite metadata and content-addressed copied objects; independent of WeChat availability |
| Transcription | `chronicle/asr.py` | Explicit jobs and provider profile; audio, machine text and manual corrections remain separate |
| Generated demo | `chronicle/demo.py` | Fake source, synthetic files and silent audio; no WeChat or real vault access |

`tools/chronicle_mac/` retains compatibility entry points and synthetic adapter tests. The supported application entry point is `python -m chronicle.launch`, not the earlier native wrapper. Installation packages are deferred.

## Archiving behavior

A source message belongs to an account and conversation. Its stable source identity is independent of its display name. A project links selected messages; the same message can belong to multiple projects. Repeated selection is idempotent within a project. Source observations and conflicts are retained rather than silently rewriting the archived event.

Resource resolution happens before or during an explicit archive request. Available objects are copied into `archive/objects/<digest-prefix>/<digest>` and referenced by digest. Source files are not moved. Primary data, preview and voice playback are separate roles. Ambiguous or missing assets remain explicit; thumbnails and first-frame previews are labeled. A quoted message stores only the quote snapshot present in the reply, without importing referenced conversations or attachments automatically.

A selection spanning browse pages or conversations is validated together before it is linked into a project. Original observation time and displayed event time are separate concepts. The archive timeline uses the configured time zone (currently Asia/Shanghai in the UI), then source order for ties.

## User data

Default home: `~/Library/Application Support/Chronicle/`.

- `settings.json`: welcome state, profile labels, selected profile and last project/date/view.
- `archive/`: SQLite archive and copied objects.
- `cache/`: private temporary browse/preparation state.
- `connection-attempts/`: initialization journal and temporary state; verified material is deleted after successful Keychain save. Interrupted attempts may retain private recovery material.
- `browser-runtime.json`: private runtime secret and port used by the local launcher to reopen the service.
- `demo/`: independent generated workspace.

`--home` separates workspaces. The legacy `--config` option can bind an existing archive and private verified connection file without changing the old archive. Never commit these runtime files. Back up archives while the service is stopped; include both SQLite and the object directory. No polished backup/restore UI is claimed yet.

## Deliberate limits

- Mac connection was validated on Apple Silicon and WeChat 4.1.15; it is not a supported WeChat API.
- The first connection still needs an explicit login confirmation. Normal quit can be canceled; Chronicle does not force-kill the user's installed WeChat.
- No automatic history sync, cloud sync, AI summaries or full-text reconstruction from screenshots.
- Automatic cloud transcription is not configured out of the box. The workbench currently offers playback and manual text, with jobs exposed through the CLI.
- Windows adapter, Intel Mac compatibility, installable releases and automatic updates are not completed.

Tests use generated fixtures. A passing mock of connection cancellation is not evidence that a new WeChat version can initialize successfully.

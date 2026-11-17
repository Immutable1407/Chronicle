# Windows handoff / Windows 协作约定

Mac and Windows can share the project archive and browser workflow. Windows connection must be implemented and validated on Windows; the Mac adapter is not a cross-platform credential acquisition module.

## Reuse

- `chronicle/archive.py`: projects, stable identities, observations, project links, copied objects and integrity checks.
- `chronicle/message_format.py`: lightweight quoted-message presentation.
- `chronicle/asr.py`: job lifecycle and provider adapter.
- `chronicle/web/`: project-first selection and timeline UI.
- Core tests and generated demo, which do not require a real WeChat account.

## Replace behind a source boundary

A source exposes account fingerprint, `conversations()` and `message_page(conversation, limit, before)`. Messages need stable account/conversation/source identity, sender, timestamp, ordering, body/type, decode status and provenance. Resource records associate a stable message identity with copied assets or an explicit missing/ambiguous reason. See `chronicle/workbench.py` and `chronicle/macos/reader.py` for the current shapes; synthetic examples live in `chronicle/demo.py` and tests.

`Workbench.source()`, `people()` and `prepare()` are the present integration seams. `DesktopWorkbench` and the source setup UI currently assume Mac paths, Apple tools and Keychain. Introduce a Windows source/setup/vault implementation there; do not embed Windows-specific behavior in the archive schema. A formal plugin ABI is not yet promised.

## Acceptance criteria

- Keep identical source IDs in different accounts/chats separate; repeat imports are idempotent.
- Merge selected chats chronologically while retaining per-record source labels.
- Include committed recent messages and preserve a consistent snapshot when database WAL exists.
- Resolve files and voice by validated relationships, not a filename-only guess; represent missing/ambiguous resources explicitly.
- Copy assets into the archive and verify they remain available after the source disappears.
- Keep credentials outside public files/logs and use an appropriate Windows credential store.
- Preserve quote snapshots without automatically pulling an entire conversation.
- Verify cancellation/recovery as well as successful connection on real Windows hardware.

Distribution is source-first for now. Future Mac DMG and Windows EXE releases can be built by their respective maintainers. Package signing, updates and version compatibility remain separate platform responsibilities. Coordinate archive schema migrations before either platform releases a change that affects shared data.

## Shared contracts updated in 0.3

Archive schema 3 and the timeline cursor API are documented in [account identity](account-identity.md) and [Mac hardening](mac-hardening.md). Reuse the common identity helper when the same verified internal principal is available. Old archived IDs are opaque and can resolve through aliases; do not recompute or overwrite them. Windows source acquisition remains separate. The optional Mac encrypted sparsebundle is a platform-specific storage wrapper, not a Windows encrypted file format.

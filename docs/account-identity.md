# Account identity and archive schema 3

## Identity contract

A physical database root is a **locator**, not an account identity. An account's display nickname, user-supplied label and editable public WeChat alias are also not identity keys.

The shared pure helper is `chronicle.account_identity.wechat_account_fingerprint(username)`. Its input is the verified internal `contact.username`, using its exact case and UTF-8 bytes. The fingerprint is SHA-256 of the bytes formed by joining `wechat`, `account-v1`, and that internal username with a single NUL byte. There is no trailing NUL. Do not substitute a public alias, nickname, numeric identifier from a different identity namespace or a random ID per installation.

The Mac adapter obtains the internal principal from the client-managed account directory and verifies the corresponding contact in the decrypted account. Initial setup asks the user to confirm the resulting nickname. The binding is retained in the private connection manifest as `account_identity`, with scheme `wechat-internal-user-v1`. Unknown identity does not silently fall back to path-based ingestion; existing archives can still be read offline.

Source messages use the fingerprint, conversation identifier and server message ID. If the server ID is unavailable, the fallback remains relative source database plus local ID. That fallback is only stable within that database generation; a database rebuild can require explicit reconciliation. Content and timestamps are not used to guess that two messages are identical.

## Legacy compatibility

Schema 1 and 2 archives upgrade transactionally to schema 3. Existing archived message IDs are preserved. New account/message aliases map incoming canonical IDs to those old IDs. Source observations retain their original JSON; semantic digests ignore the transport `archive_identity` field so identity migration alone is not reported as an edited message.

`Archive.bind_account(legacy_fingerprint, canonical_fingerprint)` moves the account association, consolidates duplicate source events if both identities were previously imported, and retains project links, attachment observations, transcription jobs and manual corrections. Old public message IDs remain resolvable through `Archive.resolve_message`. Treat returned IDs as opaque: legacy IDs need not equal the hash of their now-canonical account.

Normal Mac ingestion binds the verified source's retained legacy fingerprints before linking messages. A binding already assigned to a different canonical account is rejected. Cross-account events are never merged merely because they have identical text, time, sender labels or filenames.

## Relocating a connection

Stop the workbench before moving local data. Keep the private connection file; never share it in chat or GitHub. After moving the database root, produce a new validated connection manifest:

```sh
.venv/bin/python -m chronicle.macos.identity \
  --keys /private/path/connection.json \
  --root /new/path/to/account/db_storage \
  --output /private/path/relocated-connection.json
```

Every existing database key is checked against the new location before output is written. The old path fingerprints are retained, allowing a legacy archive to deduplicate later imports. A missing or conflicting identity fails without overwriting the input file. Import the resulting file through connection settings.

If an old archive and freshly initialized connection no longer share any recorded locator, automatic mapping cannot be proven. Inspect the archive's account fingerprints and explicitly associate only the account you have independently verified:

```sh
python3 -m chronicle --root /path/to/archive accounts
python3 -m chronicle --root /path/to/archive bind-account \
  --legacy LEGACY_ACCOUNT_FINGERPRINT --connection /private/path/verified-connection.json
```

Do not select an old account based only on an equal nickname. Back up the archive first. Direct binding is an explicit migration operation, not an account-discovery heuristic.

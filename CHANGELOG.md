# Changelog

## 0.3.0.dev0

- Mac accounts use a verified internal username binding instead of the absolute database path. Credential relocation retains legacy fingerprints.
- Archive schema 3 introduces account/message aliases and semantic observation digests. Existing archived IDs, project links, objects, corrections and observations survive binding; duplicate accounts can be consolidated transactionally.
- The timeline HTTP API returns bounded cursor pages and performs search, source, date and attachment filtering on the server. Related data is loaded in batches; summary counts are SQL aggregates.
- Mac file inventory and metadata-keyed MD5/SHA-256 caches are reused across resource requests. Changed bytes are revalidated before copying.
- First connection is gated to the validated Mac client version/build. Unknown clients are rejected before quitting WeChat. Existing archives remain readable offline.
- Optional AES-256 encrypted Mac workspaces use native APFS sparsebundles, with import, lock and verified encrypted-backup commands. Ordinary workspaces are still plaintext.
- Core tests now also run on a Mac arm64 hosted runner, including artificial database/debugger fixtures and the encrypted-workspace lifecycle. No real WeChat account is used by CI.
- New source launches choose an available loopback port and reopen an existing workspace through its private runtime ticket.

Windows source acquisition remains a separate implementation. Consumers of the archive/schema or timeline API should read the versioned contracts before updating.

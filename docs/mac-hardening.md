# Mac hardening, development version 0.3

This iteration addresses the concrete Mac-side risks found during review. Windows acquisition remains with the Windows maintainer.

| Concern | Current behavior | Remaining boundary |
| --- | --- | --- |
| Unofficial client access | First initialization is restricted to validated version/build 4.1.15 / 270115 on Apple Silicon, before any quit or temporary-copy operation | This is not an official API and cannot guarantee future compatibility or account safety |
| Path-bound accounts | Verified internal principal fingerprints, retained locator aliases and transactional schema-3 migration | Legacy mappings without evidence require explicit operator association; server-ID-less messages remain local-generation scoped |
| Attachment scanning | Reusable size inventory and metadata-keyed MD5/SHA-256 cache; copied bytes checked again | Inventory refresh is periodic; a newly downloaded/changed resource can require retry after cache refresh |
| Large timelines | Cursor pages, server-side filtering, SQL aggregates and batched attachment/transcript hydration | Summaries still scan project metadata; loading more pages deliberately grows the UI's loaded set |
| Plaintext archives | Optional native AES-256 encrypted workspace, import and encrypted backup | Ordinary workspaces remain plaintext; no automatic migration/deletion or cross-platform encryption container is claimed |
| Platform testing | Core Linux matrix plus Mac arm64 CI fixtures and local encryption/relocation tests | Artificial debugger children and hosted runners do not replace real WeChat account/version acceptance |

## Reproducible performance evidence

Run `python3 scripts/benchmark.py --output /tmp/chronicle-performance.json` to generate an independent fixture. The committed [performance result](performance.json) uses synthetic projects with 1,000, 10,000 and 100,000 messages. Timings are local warm-cache medians after import; they are not cold-start or real-media capacity guarantees.

The HTTP workbench returns 100 records by default (maximum 200 per request). The browser shows the loaded/matched count and an explicit load-more action. Search is evaluated over the complete project, including matches beyond the loaded page; source/date/file filters also run on the server. Related-data query count is bounded per page.

## Timeline HTTP contract

`GET /api/timeline?project=ID&limit=100&date=YYYY-MM-DD&q=TEXT&source=SOURCE_ID&mode=files`

The response contains `messages`, `next`, `matched`, project-wide `total`, `days`, `sources`, `resources`, and `source_options`. Pass the opaque JSON `next` value in the `after` query parameter to continue. Changing filters invalidates the cursor. `source` is an opaque source ID, not a display name. `mode=files` filters on the server. Archive CLI exports remain explicit whole-archive reads.

Read [identity/schema migration](account-identity.md) before consuming the SQLite format, and [encrypted workspace instructions](encrypted-workspace.md) before migrating private data.

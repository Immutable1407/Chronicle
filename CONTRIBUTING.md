# Contributing

Chronicle is in early development. Small, reviewable improvements to archiving, connection recovery and usability are welcome.

Read [AGENTS.md](AGENTS.md) and [architecture](docs/architecture.md), run the demo, and reproduce problems with generated data. Include the platform, Python/WeChat versions, steps and expected behavior in an issue. Remove personal identities and all credentials. Never attach a real WeChat database or connection manifest.

For a change, describe the concrete behavior and relevant validation. Keep platform adapters separate from archive storage. Preserve compatibility or document a migration. Run the core tests and appropriate Mac fixtures; use browser checks for UI changes. PRs adding a new platform should state which behavior was verified on real hardware and which remains unverified.

Contributions are under the repository's MIT license. Dependencies and referenced projects keep their own licenses.

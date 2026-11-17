# Security and private data

Chronicle is an early local application. Its archive and caches may contain readable personal messages and files. Protect the workspace with your operating system's account permissions, disk encryption and backups. A Keychain-backed connection does not encrypt an ordinary archive itself. The optional [Mac encrypted workspace](docs/encrypted-workspace.md) places Chronicle-managed files inside an AES-256 native disk image and locks it on normal shutdown; existing ordinary archives are not automatically moved or deleted. Password entry is local, and no recovery key is retained.

Do not post credentials, account material, source databases or one-time browser tickets in public issues. If reporting a vulnerability, provide a synthetic reproduction that contains no personal data. If a private reporting channel is unavailable, first open a public issue asking for a private contact without including exploit details or private records.

The web server is intended only for loopback use. Do not expose it to a LAN or public reverse proxy. Cloud transcription is an explicit, optional transfer to a user-selected service; it is not part of the demo or normal archiving.

# References and dependencies

Chronicle's source is distributed under MIT. This does not relicense external software, system APIs or WeChat. No WeChat application, dedicated third-party key helper, pretrained speech model or private chat data is distributed in this repository.

The development process studied these public projects for architecture, source mapping, export behavior and connection UX:

- [Dukou](https://github.com/qzz0518/Dukou), reviewed at `8c43324ae00d32c7cfb13a18b559ae0fc6db6d43`.
- [TraceMemo](https://github.com/Wxw-Gu/TraceMemo), reviewed at `c3986917929e931d817596c44b07a1139d91e5c9`.
- [wechat-ai-memory](https://github.com/ikevss/wechat-ai-memory), reviewed at `4dbc993090a862c55bbb55dee7886580bfa66b5e`.
- [WeFlow](https://github.com/hicccc77/WeFlow), reviewed at `91adcfdbb24e8e147d8332440b8c6ac899ad2abf`.
- [wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool), [wechat-chat-history-mac](https://github.com/BIBOYANG425/wechat-chat-history-mac) and the [Mac local toolkit](https://gist.github.com/0a01249ef72970d07a2603dbb629e80f) were research references for the Mac initialization/database boundary.

The application modules in this repository were written for Chronicle. The reference repositories and their specialized native binaries are not bundled. Consult each upstream license before copying or redistributing its code or binaries; links here describe research references, not a dependency on those applications.

Runtime components installed separately include Python/SQLite, SQLCipher, Zstandard, FFmpeg and the optional `pilk` SILK decoder. macOS integration uses Apple's Security, CommonCrypto, LLDB and application services. Each retains its own license and terms. Source setup uses local libraries rather than redistributing media/database binaries. Any future binary installer needs its own dependency/license review.

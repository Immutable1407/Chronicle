# Mac adapter tools

Implementation: `chronicle/macos/`. Files here preserve earlier command paths and host the synthetic fixture tests.

Use the guided source workbench for normal operation:

```sh
./run.sh
```

Read [connection details](../../docs/connection.md) before initializing an account. The low-level bootstrap requires explicit account selection, a closed WeChat, Apple Silicon and Apple tools; it is not a routine startup command.

```sh
python3 -m chronicle.doctor
python3 -m unittest discover -s tools/chronicle_mac -p 'test_*.py' -q
python3 -m chronicle.macos.reader --help
python3 -m chronicle.macos.resources --help
```

Tests use artificial encrypted databases and generated media. They do not log into or capture a real WeChat process. Never put captured connection files or output databases in this folder.

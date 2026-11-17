# Encrypted Mac workspace / Mac 加密工作区

Chronicle offers an optional macOS-native AES-256 encrypted APFS sparsebundle. The archive database, copied objects, browse caches, media derivatives and Chronicle connection-attempt files are inside this volume. Ordinary `--home` workspaces remain plaintext; existing data is not silently moved or deleted.

This protects Chronicle's files while the image is locked. While unlocked, processes with the same OS access can read them. Original WeChat storage, explicitly downloaded/exported files and OS memory are outside this feature's scope. Mac connection credentials continue to use Keychain. The encrypted image is a Mac storage wrapper; its decrypted archive contents still follow the shared SQLite/object contract.

The underlying mechanism is Apple's encrypted disk image support, not custom cryptography: [Apple Disk Utility documentation](https://support.apple.com/guide/disk-utility/dskutl11888/mac).

## Create and open

Run from the repository after source setup:

```sh
.venv/bin/python -m chronicle.macos.secure_workspace create \
  "$HOME/Documents/Chronicle.sparsebundle" --capacity 20g
./run.sh --secure-home "$HOME/Documents/Chronicle.sparsebundle"
```

创建时在本机终端输入并确认至少 12 个字符的密码。容量是稀疏映像的上限，不会立即占用 20 GB。密码不会写入命令参数、环境变量、设置或日志，也不会自动存进钥匙串。密码丢失没有恢复机制，请自行保管。

解锁后，浏览器侧栏会显示“加密工作区 · 当前已解锁”。保持启动终端运行；结束时按 `Ctrl+C`，工作台会关闭文件并正常卸载映像，终端确认 `Encrypted workspace locked.`。不会强制弹出仍被其他程序占用的卷。若连接清理尚未完成，程序会保留挂载与进程锁，避免后台任务在卸载后继续写入；请先等待临时连接恢复，再执行下面的锁定命令。

如果异常退出后映像仍挂载，关闭正在使用该卷的文件或任务，再运行：

```sh
.venv/bin/python -m chronicle.macos.secure_workspace lock \
  "$HOME/Documents/Chronicle.sparsebundle"
```

## Copy an existing archive into it

先停止旧工作台。导入目标必须是一个尚无项目的加密工作区：

```sh
.venv/bin/python -m chronicle.macos.secure_workspace import \
  "$HOME/Documents/Chronicle.sparsebundle" --archive /path/to/original/archive
```

This copies SQLite through a consistent backup and verifies copied objects. It retains the original ordinary archive and does not copy plaintext connection files. To stop retaining the ordinary archive, first accept the encrypted copy and decide separately what to do with the original; this command does not delete it.

## Encrypted backup

Stop and lock the workspace before backing it up:

```sh
.venv/bin/python -m chronicle.macos.secure_workspace backup \
  "$HOME/Documents/Chronicle.sparsebundle" \
  /path/to/backup/Chronicle-backup.sparsebundle
```

The backup command refuses a running/mounted workspace, copies the encrypted bundle and compares each copied file's SHA-256. It does not export a plaintext database or copied attachment directory. The backup can be opened with the original password. Keep backups somewhere independent of the source device.

The current setup is source/CLI first. A graphical password, migration and recovery flow is not claimed. Tests use generated passwords and artificial archives, including lock, wrong-password rejection, reopening and encrypted-backup read-back.

# 安装、升级与卸载

[返回首页](../README.md) · [日常操作](usage.md) · [排障](troubleshooting.md)

## 系统前提

运行需要 macOS、Python 3.10+、完整 Xcode、libimobiledevice。当前已实测的两台 Mac 使用 Apple Silicon 与 macOS 26.x；Intel Homebrew 路径已覆盖，但没有 Intel 真机验收。不要把这些实测版本当作经过承诺的最低 macOS/Xcode 版本；Xcode 与设备系统的兼容性以 Apple 支持范围为准。

Xcode Command Line Tools 单独安装不足以执行完整原生流程。首先打开 Xcode 完成其组件安装和许可流程，再确认全局选择：

```sh
env -u DEVELOPER_DIR xcode-select -p
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
xcodebuild -version
```

第二条仅在需要切换时执行，会要求管理员授权。`DEVELOPER_DIR` 是进程选择，不能代替某些服务使用的全局选择。遇到“平台未安装”时先检查这里，不要先下载模拟器。

在 Xcode Settings → Accounts 登录自己的账号；通过 Manage Certificates 确认这台 Mac 拥有可用的 Apple Development 私钥。CLI 不会复用 iCloud 登录作为开发者登录，不自动撤销别的 Mac 的证书。账号可访问的团队仍受 Apple 账户权限限制。

```sh
brew install python@3.12 libimobiledevice
python3.12 --version
command -v idevice_id
```

参考：[Python Homebrew 包](https://formulae.brew.sh/formula/python%403.12)、[libimobiledevice Homebrew 包](https://formulae.brew.sh/formula/libimobiledevice)。项目运行代码仅使用 Python 标准库，外部原生库与 Xcode 工具仍必须自行安装。不要向系统或 Homebrew Python 强行执行全局 pip 安装。

## 方式一：直接运行源码

取得本项目源码后，进入仓库根目录：

```sh
python3.12 scripts/sideloadlycmd --help
python3.12 scripts/sideloadlycmd native-doctor --json
```

无需 pip 或编译。如果 `python3` 已是 3.10+，可以用它替换 `python3.12`。设备验收时另加 `--device "My iPhone" --team ABCDE12345`，否则基础 doctor 不要求当前接有手机。

## 方式二：托管运行目录

```sh
python3.12 scripts/install_macos.py
~/.local/bin/sideloadlycmd --help
```

默认复制源码运行时到 `~/Library/Application Support/SideloadlyCmd/runtime`，并创建 `~/.local/bin/sideloadlycmd`。默认不改 shell 文件。需要短命令时，运行 `python3.12 scripts/install_macos.py --add-to-path`，新开终端即可使用 `sideloadlycmd`。它按当前 SHELL 选择 zsh 或 bash，也可指定 `--shell zsh` 或 `--shell bash`，对 login 和交互配置追加幂等块，保留原有内容；不自动 source 用户配置。符号链接、非本人配置或不完整的托管块会拒绝写入，可自行设置 PATH。重复安装不会重复追加目录。

自定义位置：

```sh
python3.12 scripts/install_macos.py --prefix "$HOME/Applications/SideloadlyCmd-runtime" --bin-dir "$HOME/.local/bin"
```

已存在的目录必须带有本安装器的管理标记；不覆盖无关启动器、符号链接或任意目录。运行时与账号、描述文件和刷新登记分开，升级不复制或重建这些状态。安装器生成的入口记录所用 Python 路径；移除或升级该 Python 后，可能需要重新运行安装器。

## 方式三：虚拟环境或 wheel

在源码根目录执行：

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
sideloadlycmd --help
```

普通安装可使用 `python -m pip install .`。已有本项目 wheel 时，在虚拟环境中运行 `python -m pip install /path/sideloadlycmd-1.0.0-py3-none-any.whl`。文件名随版本变化；构建见[开发文档](development.md)。wheel 不自动安装 Xcode、Homebrew 依赖或 AI skill。

## 可解压运行包

`python3.12 scripts/build_portable.py` 生成 `dist/sideloadlycmd-macos.tar.gz`。解压后进入 `sideloadlycmd-macos/`，按上述源码方式运行或使用安装器。包包含文件哈希清单；安装器检测篡改或不完整文件后停止。清单验证完整性，不等于验证发布者身份，应使用你信任的来源。

## 升级

1. 如果启用了自动刷新，先用**旧入口、原 state-dir**执行 `refresh-scheduler disable --json`，确认卸载。没有启用则跳过。
2. 等待正在进行的签名、安装或 Background 命令结束。
3. 从更新后的源码或解压包重跑安装器；虚拟环境用户重新 `pip install .`。
4. 执行 `native-doctor`；若原先使用调度，再按原 state-dir 显式启用。

不要通过复制另一台 Mac 的运行状态完成迁移。每台 Mac 使用自己的账号、私钥、描述文件和设备配对；在新 Mac 重新安装并登记刷新。

## 状态位置与卸载

- 托管运行时：`~/Library/Application Support/SideloadlyCmd/runtime`。
- 默认原生登记和源 IPA 快照：`~/Library/Application Support/SideloadlyCmd/native`。
- 精确描述文件缓存恢复副本：同一 App Support 下的 `profile-cache-backups`。
- 非敏感 Xcode 账号引用恢复副本：`xcode-account-backups`；不包含密码或会话令牌。
- 用户调度配置：`~/Library/LaunchAgents/org.sideloadlycmd.native-refresh.plist`。
- prepared 输出：由使用者指定，包含签名应用、manifest 和安装回执。

`--state-dir` 会改变相应登记位置。卸载前先用原入口和 state-dir 关闭调度。然后移除你安装的启动器和托管 runtime；虚拟环境用户可用 `python -m pip uninstall sideloadlycmd`。需要删除 IPA 快照和登记时，单独处理自己的状态目录。不要删除整个 Keychain、Apple 账号或共享 Xcode 描述文件目录。卸载 CLI 不会卸载手机上的应用。

## 非交互 SSH 的 PATH

`--add-to-path` 面向新开的交互终端。非交互 SSH 命令不一定加载 `.zshrc` / `.bashrc`；可在那条远程命令显式设置 PATH，或使用 `~/.local/bin/sideloadlycmd`。Homebrew 安装后使用其正常的 bin 和 shellenv 配置，不必经过本项目的源码路径。

## Homebrew 方向

项目目前已支持托管安装、wheel/pip 和运行包，并非只能用源码。Homebrew 的真实 Formula 生成器与发布步骤见 [homebrew.md](homebrew.md)。尚未发布 tap，不要直接假设 `brew install sideloadlycmd` 可用。

# SideloadlyCmd

[English](README.en.md) · [安装](docs/installation.md) · [使用](docs/usage.md) · [命令含义与示例](docs/commands.md) · [命令参考](docs/cli-reference.md) · [常见问题](docs/troubleshooting.md) · [AI 与 Skill](docs/ai-usage.md) · [开发与打包](docs/development.md)

使用 macOS 原生 CLI 为 IPA 签名、安装到指定 iPhone，并登记与执行刷新。正常路径复用本机 Xcode 保存的 Apple ID 会话和钥匙串私钥，不操作 Sideloadly/Xcode 窗口，不依赖 AppleScript、Accessibility 或鼠标。

- 明确选择设备、账号、团队与 Bundle ID；主应用和扩展逐一验证签名绑定。
- 支持本机、SSH 和一次性 launchd Background 执行；可选持久自动刷新。
- 安装完成后独立核对设备上的 Bundle ID、version 和 build。
- 可离线使用现有描述文件；在线请求和续期使用 Xcode 管理的账号。
- 总超时、安装互斥锁、原子私有回执、结构化脱敏日志及明确退出码。
- 项目自带 AI skill，可帮助完成环境检查、安装和后续刷新。

已在两台 Apple Silicon Mac 上实测原生签名、USB 安装复核和实际描述文件续期。日常场景是用户已登录 Mac 后通过终端、SSH 或后台任务操作。Intel 真机、注销后的无人值守运行、重启恢复与跨日调度尚未完整实测；它们不是当前日常使用的交付前提。首次账号登录、系统授权和设备信任仍由系统或用户处理。

## 快速开始

需要 macOS、Python 3.10+、完整 Xcode、libimobiledevice，以及已信任并启用开发者模式的 USB iPhone。无需编译 Sideloadly 本体。Apple ID 首次登录在 Xcode → Settings → Accounts 完成。

依赖准备完成后，安装一次短命令入口并配置当前用户 PATH：

```sh
python3.12 scripts/install_macos.py --add-to-path
# 新开终端后：
sideloadlycmd teams --json
sideloadlycmd sideload /path/App.ipa
```

一键 `sideload` 自动识别原始包 ID，唯一 USB 设备自动选中，优先选择唯一 Personal Team，签名、安装并核对版本。多设备用 `--device` 指定。一键命令默认输出 JSON，可省略 `--json`；默认保留受支持组件；已知不支持的可选组件自动移除并警告，详见[组件策略](docs/components.md)。可用 `--team ABCDE12345` 覆盖选择；多个 Personal Team 会停止询问。不会自动开启长期刷新。更多解释和 dry-run 示例见[命令指南](docs/commands.md)。

下面是源码方式的预检与分步操作（使用 Python 3.10+，系统 python3 过旧时改用 python3.12）：

```sh
brew install python@3.12 libimobiledevice
python3.12 scripts/sideloadlycmd --help

env -u DEVELOPER_DIR xcode-select -p
# 仅当全局选择不是完整 Xcode 时执行：
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
python3.12 scripts/sideloadlycmd native-doctor --device "My iPhone" --team ABCDE12345 --json
```

这里和下文的 `My iPhone`、`ABCDE12345`、路径和包 ID 都是示例，需要替换。doctor 成功表示工具与保存的元数据可用，不等于在线认证、签名或安装已完成。

```sh
python3.12 scripts/sideloadlycmd prepare /path/App.ipa \
  --output /path/new-prepared --account xcode --team ABCDE12345 \
  --device "My iPhone" --bundle-id com.example.app \
  --bundle-id-mode automatic --strip-watch --json

python3.12 scripts/sideloadlycmd install-prepared /path/new-prepared \
  --team ABCDE12345 --device "My iPhone" \
  --bundle-id com.example.app.ABCDE12345 --json
```

`prepare --bundle-id` 必须是 IPA 的**原始**主包 ID。`automatic` 生成 `<原始ID>.<TEAM>`，安装使用生成的**最终** ID。输出目录必须不存在。默认保留普通 iOS 扩展；当前不支持的 Watch 会自动移除并报告功能损失。`--strip-watch` 可显式指定移除，`--strict-components` 可要求遇到不支持组件就停止。

SSH 下建议先在交互终端解锁后台钥匙串，然后通过正式后台入口执行同样的命令：

```sh
python3.12 scripts/sideloadlycmd background-keychain unlock --timeout 600 --json
python3.12 scripts/sideloadlycmd background-run --json -- prepare /path/App.ipa \
  --output /path/new-prepared --account xcode --team ABCDE12345 \
  --device "My iPhone" --bundle-id com.example.app --bundle-id-mode automatic --strip-watch
```

密码只在隐藏终端输入；不发给 AI，不放在参数、环境变量或配置文件中。`background-run` 不启用持久任务，完成后卸载临时任务。

## 安装到 PATH 与构建分发包

```sh
# 无 pip、无第三方 Python 运行时依赖的安装方式
python3.12 scripts/install_macos.py --add-to-path
# 新开终端后
sideloadlycmd --help

# 可解压运行包
python3.12 scripts/build_portable.py

# 标准 wheel / sdist：在自己的虚拟环境中执行
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip build
python -m build
```

安装器默认使用 `~/Library/Application Support/SideloadlyCmd/runtime` 和 `~/.local/bin/sideloadlycmd`。默认不修改 shell 配置；`--add-to-path` 才显式追加幂等 PATH 块，新终端生效，不自动启用刷新。其他安装方式、升级、卸载、打包检查见[安装文档](docs/installation.md)与[开发文档](docs/development.md)。未声明已发布到 PyPI 或 Homebrew；wheel、托管安装和运行包都已可用。Homebrew 的 Formula 生成与 tap 发布准备见[分发说明](docs/homebrew.md)。

## 刷新、恢复与 AI

安装后可以单独登记刷新，再执行真实续期；首次安装成功不要求开启调度。如果安装报告完成但设备核对失败，先使用 `verify-prepared` 只读复核，避免重复安装。

- [完整安装、离线模式、刷新与 SSH 操作](docs/usage.md)
- [错误分类、钥匙串、设备与 Xcode 排障](docs/troubleshooting.md)
- [项目内置 skill](.agents/skills/sideloadly-cli/SKILL.md) 与 [AI 提示词示例](docs/ai-usage.md)

例如，把下面这段发给你使用的 AI 编程助手：

> 请读取本仓库 `.agents/skills/sideloadly-cli/SKILL.md`，先检查我的 Mac 环境。我要将 `/path/App.ipa` 安装到名为 `My iPhone` 的手机，团队是 `ABCDE12345`。复用 Xcode 账号，遇到密码、验证码或系统授权由我在本机输入。安装后核对版本；暂不启用自动刷新。

## 项目边界与参与

原生路径不要求安装 Sideloadly.app。可选桌面兼容命令仍保留，要求用户自行安装 Sideloadly 和授予相应系统权限；详见[使用文档](docs/usage.md#桌面兼容与实验命令)。项目不分发 Apple SDK、Sideloadly 专有库或预配置账号，不实现权益绕过，不提供 IPA 下载。

正式源码位于 `src/`，自动化回归测试位于 `tests/`，公开文档位于 `docs/`。机器相关实测记录、逆向探针和历史脚本统一保存在本地 `.local-dev/`，Git 与所有分发包均排除该目录。公开文档不依赖这些文件。

欢迎按 [CONTRIBUTING.md](CONTRIBUTING.md) 提交改进；敏感问题见 [SECURITY.md](SECURITY.md)。代码与文档采用 [MIT License](LICENSE)，外部工具保留各自许可，见[第三方说明](docs/third-party.md)。

安装过程会实时输出阶段提示，长时间等待每 10 秒报告一次；过程写 stderr，最终 JSON 写 stdout。详见[进度与日志说明](docs/usage.md#实时进度与等待提示)。

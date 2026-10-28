# 开发、测试与构建

[贡献说明](../CONTRIBUTING.md) · [命令参考](cli-reference.md) · [许可边界](third-party.md)

## 源码结构

- `src/sideloadlycmd/`：CLI、IPA 校验、Xcode 签名、安装查询、刷新、后台执行与桌面兼容层。
- `scripts/`：源码入口、托管安装器、分发包构建器和 CLI 文档生成器。
- `tests/`：正式回归测试与合成 fixture；不依赖开发者个人设备、账号或本地档案。
- `docs/`：维护中的公开使用、API 和开发说明。
- `.agents/skills/sideloadly-cli/`：随项目分发的 AI skill。
- `.local-dev/`：本地开发历史，不提交、不打包；公开构建和测试不能依赖它。

这是 Python 标准库运行时项目，没有 C/C++ 扩展编译步骤。`python -m build` 构建的是 Python 分发包；签名时 Xcode 构建一个最小临时项目以取得描述文件，不是编译 Sideloadly，也不从源代码构建用户提供的 IPA。

## 开发环境

建议使用 macOS 与 Python 3.12 维护文档和运行测试；运行时最低 Python 3.10。完整设备验收另需 Xcode 和 libimobiledevice，见安装文档。

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip build
python -m pip install -e .
PYTHONPATH=src python -W error::ResourceWarning -m unittest discover -s tests -v
python -m compileall -q src tests scripts
python scripts/generate_cli_reference.py --check
python scripts/check_release.py --docs-only
```

正式测试不自动登录 Apple、签名真实 IPA、安装 App、修改 Keychain ACL 或启用现有调度。部分测试直接调用 macOS 的公开会话 API，因此完整测试套件以 macOS 为目标，不承诺 Linux/Windows 运行。个别纯 Python 模块可以在其他平台测试，不等于支持其他平台侧载。

## 构建 wheel 和源码包

```sh
python -m build
```

默认在隔离构建环境中安装 pyproject 声明的构建依赖，生成 `dist/sideloadlycmd-<version>-py3-none-any.whl` 和源码 `.tar.gz`。运行时代码只有 Python，不代表可在任意操作系统运行：macOS 仍是实际执行要求。

验证 wheel 时在干净环境里安装明确文件，再运行 `sideloadlycmd --help`、`python -m sideloadlycmd --help`。完整侧载需要你显式提供自己的设备、账号和合法 IPA，不属于包安装 smoke test。

源码包应包含公开 docs、scripts、tests、LICENSE 和内置 skill；wheel 包含 CLI Python 模块、桌面兼容 AppleScript 资源和许可证。wheel 不向用户的 AI 配置目录自动写文件。

## 构建可解压源码运行包

```sh
python scripts/build_portable.py
```

输出 `dist/sideloadlycmd-macos.tar.gz`，包含显式白名单的源码、安装器、文档、测试、打包配置和 skill；没有 `.git`、本地证据、账户、IPA、profile、私钥或实验二进制。`MANIFEST.json` 列出内容哈希，安装器先校验再安装。它是文件完整性清单，不是发布签名。

解压到新目录、检查清单、运行安装器、`--help` 和回归测试。不要仅在原 checkout 中检查后就声称分发包独立可用。

## 更新 CLI 和文档

命令与参数定义位于 `build_parser()`。修改后运行：

```sh
python scripts/generate_cli_reference.py
python scripts/generate_cli_reference.py --check
```

`docs/cli-reference.md` 来自实际 parser，避免手写选项与代码漂移。README 保持快速入门；复杂流程分别维护在 usage 和 troubleshooting。不要把个人用户名、UDID、Apple ID、Team、实际 IPA 路径写进公开示例。

## 原生路径关键约定

- prepare 接收原始包 ID，安装接收最终 ID；设备、团队、profile 和实际叶证书分别校验。
- 安装前持久化意图，完成与版本查询是独立阶段；未知安装不能自动重试。
- register 只代表登记；续期需要逐组件有效期严格延长及设备核验。
- Background 与 SSH 的安全会话独立；子进程/总超时、SIGTERM 和临时任务清理必须维持。
- 保持 stdout 结果 JSON、stderr 事件日志和脱敏；错误诊断只输出安全类别，不输出原始 Apple 响应。
- 不导出私钥，不复制分发 Apple/Sideloadly 专有库，不绕过 entitlement、证书或设备限制。

## 本地开发档案

将抓取日志、机器相关验收、原生探针和实验补丁放在 `.local-dev/`。正式自动化测试留在 tests/；只有测试实验探针本身的历史测试随探针归档。原路径的已跟踪文件移出后，Git 会显示删除，忽略目录中的保留副本不会被正常 add 进提交。

已有 Git 历史中的内容不会因此消失。本次整理不重写历史；若以后发现曾提交凭据，需要单独处理泄漏和历史清理。不要使用 `git add -f` 把本地档案重新带回公开树。

## 发布前检查

构建后运行 `python scripts/check_release.py`，检查公开文档链接、三种分发物必需文件、MIT 元数据及本地档案排除。仓库带有 `.github/workflows/ci.yml`，提交到 GitHub 后执行 macOS 测试和打包检查；本地准备不等于远端 CI 已运行。

运行回归、文档检查与三种打包验证；查看源码包和 wheel 内容，确认无本地档案或用户数据。为版本更改同步 `pyproject.toml` 与 `src/sideloadlycmd/__init__.py`，更新 CHANGELOG，再由维护者选择发布渠道。仓库准备和本地构建不等于已经发布到 GitHub 或 PyPI。

打包方式参考 [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/)。

## Homebrew Formula

`scripts/generate_homebrew_formula.py` 从提供的公开源码归档读取版本和真实哈希，生成维护者 tap 的 Formula；不发布。模板不依赖构建沙箱中的 pip 联网。生成方式与真实 brew 安装测试要求见 [homebrew.md](homebrew.md)。托管安装器的 `--add-to-path` 仅在显式请求时更新当前用户 shell 配置，需保持幂等、原文保留和失败恢复的测试。

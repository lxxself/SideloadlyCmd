# Homebrew 分发准备

当前并非只能使用源码：可用托管安装器、wheel/pip、可解压运行包。见[安装文档](installation.md)。这些方式都能提供短命令 `sideloadlycmd`。

**当前尚未发布 Homebrew tap，也未进入 homebrew/core。** 本项目提供生成真实 Formula 的工具；需要维护者先提供自己的公开仓库/主页和稳定版本下载地址，不能给用户一条虚假的 `brew install sideloadlycmd`。

## 生成 Formula

先在对应版本源码中构建并检查公开产物：

```sh
python scripts/build_portable.py
python scripts/check_release.py
python scripts/generate_homebrew_formula.py \
  --archive dist/sideloadlycmd-macos.tar.gz \
  --url "https://example.org/releases/sideloadlycmd-1.0.0.tar.gz" \
  --homepage "https://example.org/sideloadlycmd" \
  --output dist/homebrew/Formula/sideloadlycmd.rb
ruby -c dist/homebrew/Formula/sideloadlycmd.rb
```

上面 example.org 是示例，必须替换为你自己已经发布且可下载的稳定 HTTPS URL。上传的归档字节必须与 `--archive` 相同；工具从归档读取版本并计算真实 SHA256，不生成虚假的 URL 或全零哈希，也不接受带密码、签名查询参数的临时下载地址。

生成的 Formula 声明 macOS、python@3.12、libimobiledevice。运行时使用标准库，因此直接安装 src 到 libexec 并生成 bin 入口，不在 Homebrew 构建沙箱里联网 pip 安装。文档和 skill 放入该 Formula 的 share 目录，不改用户 AI 全局配置。

## 发布自己的 tap

按 [Homebrew 官方 tap 指南](https://docs.brew.sh/How-to-Create-and-Maintain-a-Tap) 创建维护者的 tap 仓库，将 Formula 放入 `Formula/`。发布前在你自己的 tap 做真正的 `brew install` 与 `brew test`，确认下载 URL、依赖、安装和入口都正常；Ruby 语法检查不替代它们。

发布后用户可采用 `brew install <owner>/<tap>/sideloadlycmd`，这是未来 tap 的形式，不是已经可执行的本项目安装地址。Homebrew 会把 bin 链接到其前缀中；用户仍需正常完成 Homebrew 自身的 PATH 配置。更新 Formula 的版本、URL 与 SHA256 后，使用者才能通过 brew update / upgrade 获取新版本。

提交 homebrew/core 是另一个流程，需满足其收录要求；拥有自己的 tap 不等于已进入 core。项目不会在用户执行侧载时自动安装、更新或信任新的 tap。

若已经启用原生刷新调度，brew upgrade 或 Python 更新前先使用原入口和 state-dir disable，升级完成再重跑 doctor 并显式 enable。当前不承诺跨 Cellar 路径升级后旧任务可无感继续运行。

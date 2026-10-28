# 用 AI 安装和使用 CLI

项目内置 skill：[`sideloadly-cli`](../.agents/skills/sideloadly-cli/SKILL.md)。它给 AI 提供工具定位、预检、精确设备/团队选择、SSH 后台安全会话、安装复核和刷新规则，不包含账号或机器配置。

## 启用 skill

完整源码、源码分发包和可解压运行包都包含 `.agents/skills/sideloadly-cli/`。在支持该项目级目录的 AI 工具中，打开项目后按工具的技能机制启用；是否自动发现取决于具体工具。最通用方式是明确要求 AI 读取项目里的 SKILL.md，不依赖某个产品自动加载。

部分工具支持 `$sideloadly-cli` 这种显式调用语法，可在其识别该技能后使用。也可以把**整个** `sideloadly-cli` 文件夹复制到你使用的 AI 工具所要求的 skills 目录；本项目不会自动修改全局 AI 配置。skill 本身是自包含的，不要求隐藏的研究脚本或个人记忆。

只通过 wheel 安装 CLI 不会自动在 AI 工具中启用 skill。需要时另外取得项目源码或运行包中的技能目录。

## 让 AI 帮你安装 CLI

把你实际取得的仓库目录提供给 AI，例如：

> 请读取 `/path/SideloadlyCmd/.agents/skills/sideloadly-cli/SKILL.md`。我想在这台 Mac 安装 SideloadlyCmd。先确认 Python、完整 Xcode 的全局选择和 libimobiledevice，再使用项目的安装器安装到默认用户目录。不要改 shell 配置或启用自动刷新。管理员授权由我在终端输入；不要向我索要聊天密码。完成后运行只读 doctor 并告诉我还缺什么。

如果尚未下载项目，请提供你认可的仓库或发布包地址。不要让 AI 猜测仓库 URL，或直接安装来源不明的同名 PyPI 包。

## 安装一个 IPA，暂不自动刷新

> 请使用本项目的 sideloadly-cli skill，把 `/path/App.ipa` 安装到名为 `My iPhone` 的 USB 手机，使用团队 `ABCDE12345` 和 Xcode 已保存的账号。先 inspect，采用自动 Team 后缀包 ID；仅验收 iPhone，明确移除 Watch，保留主应用和普通扩展。安装后独立核对版本/build，先不要登记或开启自动刷新。遇到账号、钥匙串或系统授权需要我操作时停在对应步骤，不要发起实验登录或无限重试。

不清楚团队时可让 AI 先做只读预检并列出可选择的团队，不让它任意选一个。设备名称重复时应明确 USB 标识；不要在公开提示词中贴真实 UDID。

## 通过已连接的 SSH 操作另一台 Mac

> 我已连接目标 Mac 的 SSH。请读取 sideloadly-cli skill，在目标上检查完整 Xcode 全局选择、Python 和 USB 手机。使用该 Mac 自己的账号与私钥，通过正式 background-run 完成 `/path/App.ipa` 的签名、安装和版本复核。不要复制本机 Keychain、账号或描述文件，也不要注销或重启目标。需要我输入密码时只让我在目标交互终端操作。目标设备是 `My iPhone`，团队 `ABCDE12345`；暂不启用持久调度。

AI 是否能使用已有终端、SSH agent 或复用连接取决于所在工具。已有可读终端不代表 AI 可向它输入；若需要另外建立连接或认证，应由用户在本机完成。不要把 SSH 密码或临时私钥发给聊天。完成后让 AI 关闭它为本任务创建的临时通道，保留你未授权关闭的其他连接。

## 安装结果不确定时先复核

> 上一次 install-prepared 已报告 CoreDevice 完成，但设备查询未通过。请保留 `/path/new-prepared` 的 manifest 和回执，对同一设备、团队和最终包 ID运行 verify-prepared，只读检查现有安装，不要重新安装或删除 App。如果回执显示 completion_unknown，就如实报告证据不足，不要伪造完成记录。

## 登记并验收刷新

> 现有 `/path/new-prepared` 已完成设备版本核对，原始 IPA 是 `/path/App.ipa`。请为这一安装登记刷新，然后仅对返回的这个 job_id 执行一次实际刷新。分别报告登记、两个组件到期时间是否延长、重装与版本核对。先不要开启长期调度；确认结果后我再决定。

若确实要自动刷新，明确增加“请按该 state-dir 启用持久调度，并验证加载状态”。手动续期成功不能被描述成“重启后一定能自动续期”。

## English prompt

> Read `.agents/skills/sideloadly-cli/SKILL.md`. Install `/path/App.ipa` on the USB device named `My iPhone`, using team `ABCDE12345` and the account managed by Xcode. Inspect the IPA first and use an automatic team suffix. I explicitly want iPhone-only signing with Watch removed. For SSH, use background-run after I unlock the Background Keychain locally. Verify the exact bundle/version/build. Do not delete other apps, export private keys, enable scheduled refreshes, or log out/restart the Mac.

## 对 AI 结果的合理预期

要求 AI 报告它实际完成的阶段：环境检查、签名、安装完成、设备版本核对、刷新登记、真实续期和调度加载分别列明。密码和验证码由用户处理，安装请求不扩展成撤销证书或扩大系统权限的授权。缺设备、缺权益或服务端认证失败时，应保留证据并停止相关操作。

## 一键安装与 Personal Team 提示词

> 请使用 sideloadly-cli skill，通过已安装的短命令 `sideloadlycmd sideload` 安装 `/path/App.ipa` 到 `My iPhone`。先列出 teams，优先唯一 Personal Team；若不唯一就让我选。仅 iPhone，显式移除 Watch，不自动开启刷新。先给我 dry-run 计划，再执行已授权的安装；需要密码仍由我在本机终端处理。若尚未安装 CLI，可用安装器的 `--add-to-path` 配置当前用户的短命令入口，不要伪造已发布的 brew 地址。

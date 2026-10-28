# Changelog

## 1.0.0 — 2026-10-24

- 所有命令增加启动与每 10 秒等待事件，原生签名安装增加阶段和实际条目计数；过程 stderr 与最终 stdout 分离。

- 一键 sideload 默认输出 JSON，保留 --json 兼容已有脚本。

- 一键 sideload 可省略设备，仅在唯一 USB 目标时自动选择并固定绑定；补齐 JSON 和 Watch 参数的可选含义。

- 新增原生一键 sideload、只读 teams 和明确的 Personal Team 优先选择。
- 安装器可显式配置用户 PATH，保留配置并避免重复追加。
- 提供未来 Homebrew tap 的 Formula 生成器和分发说明；未声明已经发布。

- 原生在线签名与刷新复用 Xcode 管理的账号，逐组件校验 profile 与签名身份。
- 支持 prepare、install-prepared、verify-prepared、刷新登记、实际续期及可选调度。
- 正式 background-run 入口供 SSH 和后台会话使用，具有总超时与临时任务清理。
- 在线预检全局 Xcode 选择；设备查询增加只读 installation_proxy 回退。
- 补齐开源文档、MIT 许可、标准打包说明与项目内置 AI skill。
- 机器相关开发档案移入忽略的本地目录，公开测试与发布内容独立。

## 历史开发里程碑

- v0.2.0：使用本机现有签名材料的离线原生签名与安装 MVP。
- v0.1.0：可选 Sideloadly 桌面兼容基线。

上述历史条目描述开发里程碑。1.0.0 为本仓库的正式版本里程碑；分发包可本地构建，不声明已在 PyPI 或 Homebrew 发布。

# 许可、外部工具与项目边界

本项目原创代码与文档按根目录 [MIT License](../LICENSE) 分发。MIT 不替外部工具、商标、Apple 开发者账号权限或用户 IPA 授权。

运行时调用用户自行安装的工具：

- Apple Xcode、codesign、security、CoreDevice/devicectl：由 Apple 分发，遵守其许可与平台要求；本项目不复制或打包它们的 SDK 或专有库。参考 [Apple Xcode](https://developer.apple.com/xcode/)。
- [libimobiledevice](https://github.com/libimobiledevice/libimobiledevice) 与 [libplist](https://github.com/libimobiledevice/libplist)：由使用者通过适合自己的包管理器安装，保留各上游许可；本项目调用其公开接口，不把外部动态库塞入 wheel 或运行包。
- [Python](https://www.python.org/)：标准库运行时；标准 wheel/sdist 构建使用 setuptools 和 PyPA build，这些构建工具保留各自许可，不是本项目运行时捆绑代码。
- Sideloadly.app：仅可选桌面兼容路径使用，需用户从其官方来源自行安装，未随本项目分发。原生路径不要求它存在。

项目名用于说明兼容工作流，不表示获得 Apple 或 Sideloadly 官方背书。用户负责提供有权使用、未加密且可重签的 IPA；本项目不提供解密、下载、权益或 DRM 绕过。

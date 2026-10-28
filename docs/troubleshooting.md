# 常见问题与排障

先记录命令、退出码、结果的 `state` / `category`，以及是否已发生设备安装。不要只看最后一个“失败”字符串就重新安装。示例命令中的配置需替换成自己的值。

## 为什么说 iOS 平台未安装，Components 却显示 Installed？

先运行 `env -u DEVELOPER_DIR xcode-select -p`。若是 CommandLineTools，使用 `sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer` 后重跑带 `--device` 的 native-doctor。一次跨 Mac 验收中的错误就是由这个选择差异解除的，仅设置 DEVELOPER_DIR、安装硬件组件或模拟器都没有单独解决它。

全局已正确时，再检查 Xcode 与设备系统的兼容性、平台组件和实际目标状态。不要修改 SDK 版本文件、复制来历不明 SDK，或把任意模拟器下载当作修复。官方组件操作见 [Apple 文档](https://developer.apple.com/documentation/Xcode/downloading-and-installing-additional-xcode-components)。

## `global_xcode_not_selected`

在线 prepare 会在账号访问和解压前拒绝不完整的全局开发工具选择。确认 `xcode-select -p` 没有被 DEVELOPER_DIR 覆盖；只安装 Command Line Tools 不够。多版本 Xcode 时确认全局选择与本次目标版本均合理，CLI 不替用户执行 sudo 或全局切换。

## `device_not_unique` / `device_not_found` / `device_selection_required`、找不到手机、名称重复

运行 `idevice_id -l`，检查 USB、解锁与信任状态。一键 sideload 省略设备时只允许恰好一台 USB；多台会要求 --device，零台不进入签名。使用完整 USB 标识或唯一精确名称；不自动选择别的设备。首次连接该 Mac 的信任与 Developer Mode 由用户按系统提示完成，不重置配对或重启设备来“碰运气”。设备在另一台 Mac 上可见不等于在执行命令的 Mac 上可见。

## Xcode 有账号，但出现 `No Accounts`、会话过期或团队不匹配

系统 iCloud 登录不是 Xcode 开发者登录。打开 Xcode Settings → Accounts，确认账号及目标团队，必要时恢复登录。使用你自己有权限的团队；账号歧义需要明确选择，不硬编码另一台 Mac 的内部账号标识。登录正常后重新进行一次明确的操作；不循环试密码、验证码或实验 SRP 登录。

## `background_keychain_locked` / `terminal_required` / `errSecInternalComponent`

SSH、图形桌面和 Background 可以有不同钥匙串状态。`background-keychain status --json` 查询 Background；`background-keychain unlock` 必须在交互终端运行。解锁后通过 `background-run` 执行签名，而不是回到仍锁定的 SSH 签名会话。

解锁不保证私钥 ACL 一定允许签名。若系统仍要求授权，交给用户处理或检查证书私钥访问；不要自动执行 set-key-partition-list、导出私钥、扩大 ACL 或把密码加入命令参数。`local_signing_identity` 的数量只是元数据，不证明签名访问已通过。

## 缺证书、`cached_profile_missing`、profile 身份不匹配

在线使用先确认当前 Mac 有自己的 Apple Development 私钥和正确账号。离线模式要求所有组件已有匹配且有效的缓存，缺一个就失败，不自动上网。profile 必须绑定目标设备、团队、最终 Bundle ID、当前私钥及有效期；缓存文件存在不足以证明匹配。

首次在线签名可通过 Xcode 注册所选设备。注册失败或权限不够时停止，不能靠换成其他设备的 profile、改 entitlements 或移除签名校验解决。不得为测试自动撤销另一台 Mac 的证书。

## `ipa_identity_mismatch`、包 ID 错误、输出目录已存在

用 `inspect` 查看原始主包 ID。prepare 填原始 ID，automatic 模式输出追加 Team 的最终 ID，install-prepared 填最终 ID。不要追加两次。输出目录必须全新；保留旧 manifest 和回执做定位，不覆盖未核对的结果。

输入若加密或不完整，需要你提供有权使用且可重签的 IPA，项目不解密或提供下载。已知不支持的可选组件自动移除并报告；需要完整保留时使用 `--strict-components`。普通签名失败不会触发删除组件重试，详见[组件策略](components.md)。

## 安装返回 3，手机上似乎已经有 App

看 `install` 字段。`completed_this_run` 表示 CoreDevice 本轮报告完成，随后版本核对未通过；先对相同 prepared 运行 verify-prepared。此命令不重装。CoreDevice 列表为空时，CLI 可通过已安装的 libimobiledevice / libplist 只读查询确切应用；缺库或查询失败不会伪造成功。

`install_completion_unknown` 表示安装超时或中断。此时连“完成”也尚未证明，verify-prepared 会要求有效的已完成回执，不会自己造一个。保留日志与设备状态再处理，避免自动重复安装。

## 安装被设备拒绝、开发 App 配额或不受支持 entitlement

系统或 Apple 账号的限制不会由 CLI 绕过。不要自动卸载其他 App、移除用户数据、撤销证书或改变账号权益。已签名和安装成功也不证明所有业务功能（推送、共享组、登录、灵动岛交互）都通过验收；按实际应用进一步测试。

## 登记成功为什么还显示 `renewal: not_tested`？

登记只保存任务和哈希校验的原始 IPA，成功退出 0。实际刷新是独立命令，只有全部保留组件有效期延长、安装与设备核对都通过才算续期验收。历史开发记录可能保留旧的登记退出码 3，不应据此推断当前命令失败。

## `expiry_not_extended`、刷新没有延长有效期

Apple 可能提供相同有效期的 profile；`--force` 不保证服务端一定延长。CLI 不以重新下载、重新签名或安装成功替代到期时间检查。保留失败/部分结果，按退避时间再试；不要循环制造证书或批量创建 App ID。

## 调度已加载但没有刷新

用原 `--state-dir` 查看 refresh-status：任务是否 enabled、接近到期、有下次尝试时间或未决安装。还需 Mac 唤醒、USB 手机可访问、后台钥匙串可用、账号会话有效。手动刷新成功不证明跨日触发和重启恢复；先判断是否到期，不在测试中修改系统时钟。

## `scheduler_state_mismatch` / `scheduler_already_loaded`

不覆盖别的状态目录或安装入口的任务。使用原入口和原 state-dir disable，确认卸载后再 enable。升级前先暂停该调度、等待正在进行的操作完成；状态目录与源 IPA 快照不应随运行时覆盖。

## 是否必须注销或重启？

不需要。它们用于验证无人登录或开机恢复的扩展承诺，日常本机、SSH 和 Background CLI 使用不要求做这些测试。项目不会自动注销、重启或要求为了正常安装而关闭所有工作。

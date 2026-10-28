# 签名、安装与刷新

[安装](installation.md) · [命令参考](cli-reference.md) · [排障](troubleshooting.md)

以下示例使用已安装的 `sideloadlycmd`。源码用户可替换为 `python3.12 scripts/sideloadlycmd`，托管安装用户可替换为 `~/.local/bin/sideloadlycmd`。示例值不是有效的用户配置。

## 一键安装、默认设备与 Team

`sideloadlycmd sideload /path/App.ipa` 将 inspect、团队选择、prepare 和 install-prepared 合并执行，并返回 prepared 目录。默认原始包 ID 来自 IPA，默认最终 ID追加 Team；不自动登记或启用刷新。`--dry-run` 只查看计划。

省略 `--device` 时只自动选择唯一 USB 设备，多台要求显式指定，零台要求连接；选中的标识会固定，不在流程中重新自动选另一台。一键 `sideload` 默认输出 JSON，无需指定 `--json`；其他子命令的输出选项保持原有行为。默认保留受支持组件，自动移除已知不支持的可选组件并警告；无需预先判断是否含 Watch。

省略 `--team` 时优先唯一 Personal Team；显式 Team 优先，多 Personal Team 会停止，无 Personal Team 时仅在唯一可用团队的情况下自动选择。`teams --json` 可先查看分类。付费 Individual 团队不等于免费 Personal Team。离线一键模式必须显式指定 Team，保证不读取账号元数据。

完整[命令含义和示例](commands.md)覆盖 SSH 包装、分步模式和恢复场景。下文保留分步操作，以便检查中间产物。

## 选择与预检

```sh
idevice_id -l
sideloadlycmd inspect /path/App.ipa --json
sideloadlycmd native-doctor --device "My iPhone" --team ABCDE12345 --account xcode --json
```

`inspect` 是只读 IPA 检查。设备使用精确 USB 标识或唯一名称，不匹配或重名会停止，不自动选择另一台。`--team` 是 10 位大写字母/数字 Team ID，可在 Xcode 的账号团队设置查看；不是 Apple ID 邮箱或证书名称。`--account xcode` 从本机已保存且拥有该团队的 Xcode 账号中选择，歧义会拒绝；需要明确本机账号引用时用 `xcode:<local account identifier>`，不把这种引用移植到另一台 Mac。

doctor 只证明前提和保存的元数据，不会登录、生成描述文件、签名或安装。设备应解锁、信任本 Mac 并启用 Developer Mode。输入必须是未加密且可重签的 IPA。

## 在线签名与安装

```sh
sideloadlycmd prepare /path/App.ipa --output /path/new-prepared \
  --device "My iPhone" --team ABCDE12345 --account xcode \
  --bundle-id com.example.app --bundle-id-mode automatic --strip-watch --timeout 600 --json

sideloadlycmd install-prepared /path/new-prepared --device "My iPhone" \
  --team ABCDE12345 --bundle-id com.example.app.ABCDE12345 --timeout 300 --json
```

`--bundle-id` 在 prepare 中是 IPA 原始主包 ID。`exact` 保持该 ID，可能因不属于当前团队而无法注册；`automatic` 添加 Team 后缀并同步重写扩展。安装中填写 prepare 结果的 `final_bundle_id`。不要把已经追加的最终 ID再次作为 prepare 输入。

prepare 的输出目录必须不存在，防止覆盖既有结果。该命令请求匹配设备、团队、包 ID 和本机证书的描述文件，对主应用和扩展分别绑定，然后严格验签；不会安装。在线使用时可能在 Apple 开发者侧注册所选设备与 App ID，必须具有相应权限。

默认保留主应用和受支持扩展，包括适用的灵动岛扩展；当前不支持的 Watch、App Clip 等可选组件自动移除并报告。可用 `--strip-watch` 或 `--strip-extension 原始扩展ID` 显式排除，或用 `--strict-components` 要求停止而不自动移除。详见[组件策略](components.md)。重签会受团队的可用 entitlements 限制，不保证推送、App Groups、登录及所有应用功能与原发行版相同。

install-prepared 会重查签名、描述文件和证书，以及设备、团队、版本绑定，执行 CoreDevice 安装，再独立查设备版本。不要拿别的设备或团队的 prepared 结果直接安装。

## SSH 与 Background 会话

SSH 和 launchd Background 有不同的钥匙串解锁状态。解锁 Background 不会让直接在 SSH 会话执行的 codesign 自动获得同等访问。推荐：

```sh
sideloadlycmd background-keychain status --json
sideloadlycmd background-keychain unlock --timeout 600 --json
sideloadlycmd background-run --timeout 900 --json -- prepare /path/App.ipa \
  --output /path/new-prepared --device "My iPhone" --team ABCDE12345 --account xcode \
  --bundle-id com.example.app --bundle-id-mode automatic --strip-watch
sideloadlycmd background-run --json -- install-prepared /path/new-prepared \
  --device "My iPhone" --team ABCDE12345 --bundle-id com.example.app.ABCDE12345
```

unlock 需要交互终端并隐藏输入 Mac 钥匙串密码；不接收参数密码，不持久化密码，不修改 ACL。账号过期仍需通过 Xcode 恢复，不能用 Mac 密码代替 Apple ID 认证。

`background-run` 接受原生命令，拒绝 GUI、独立实验登录和递归执行。外层选项在 `--` 前，原生命令的选项在后。结果中 `result` 是原生命令 JSON，`security_context` 是运行上下文，`temporary_job_removed` 表示任务卸载是否确认；Shell 退出码保留原生命令含义，清理不确定时成功也会降为部分完成。总超时预留清理时间，不安装持久调度。

## 安装超时与只读复核

```sh
sideloadlycmd background-run --json -- verify-prepared /path/new-prepared \
  --device "My iPhone" --team ABCDE12345 --bundle-id com.example.app.ABCDE12345
```

只读复核要求此前已有匹配的“完成安装”回执，并重新检查本地签名和设备版本；不重新部署。CoreDevice 应用列表为空/不可读时，公开 installation_proxy Lookup 接口只读查询该应用。若 CoreDevice 明确返回不同版本，不会用另一查询覆盖冲突。

`install_completion_unknown` 表示部署超时或中断，不能据此认定没有安装。verify-prepared 不会把未知完成状态伪造为已完成；保留回执，检查设备状态与本次日志，再决定后续操作。不要无限自动重试或删除应用。

## 离线模式

```sh
sideloadlycmd prepare /path/App.ipa --offline --output /path/offline-prepared \
  --device "My iPhone" --team ABCDE12345 --bundle-id com.example.app \
  --bundle-id-mode automatic --strip-watch --json
```

每个可执行组件必须已有有效的本机缓存描述文件，且同时匹配设备、团队、最终包 ID 和本机私钥。缺少任何组件都停止，不回退在线登录或请求 profile。离线不要求可读取 Xcode 账号列表；仍需要可用的签名身份和安装工具。

## 刷新登记、执行与调度

```sh
sideloadlycmd background-run --json -- refresh-register /path/new-prepared \
  --source-ipa /path/App.ipa --device "My iPhone" --account xcode
sideloadlycmd refresh-status --json
sideloadlycmd background-run --json -- refresh-native --job "<job_id>" --force
```

register 需要已核验安装回执，校验原始 IPA 的哈希，保存私有快照并返回 job_id。成功返回 0；`renewal: not_tested` 说明登记不等于实际续期。Background 包装结果中 job_id 位于 `result.job_id`。

refresh-native 重新请求描述文件、验证所有组件有效期严格延长、重签、重装并核对设备。`--force` 跳过到期时间判断，不绕过账号、签名或设备检查。`--account xcode` 可在明确指定一个 `--job` 时把该项重新绑定到当前唯一的团队账号，不能无差别更换所有任务。

需要自动刷新时显式启用：

```sh
sideloadlycmd refresh-scheduler enable --persist --json
sideloadlycmd refresh-scheduler status --json
sideloadlycmd refresh-scheduler disable --json
```

调度按小时检查，尝试接近到期的登记（默认距到期 48 小时）；未延长时延后再试，其他失败有退避。它不会自动唤醒、解锁设备或登录账号。未决安装会阻止盲目重试。默认 state-dir 在用户 App Support，所有原生登记相关命令使用同一 `--state-dir` 才能操作同一组记录。已有不同 state-dir 的服务时拒绝覆盖；使用原入口与原 state-dir 卸载后再切换。`--persist` 保存未来用户域创建使用的 LaunchAgent，不等于已验证重启恢复。

## 输出与日志

`--json` 把结果写 stdout；事件 JSONL 写 stderr；`--log-file /path/events.jsonl` 追加到私有日志。可按需重定向：

```sh
sideloadlycmd native-doctor --json > result.json 2> events.jsonl
```

结果脱敏不等于可以不审查就公开：分享前删除账号、设备标识和私有路径。原始 IPA、prepared 应用、profile 和账号状态都不应提交。

## 桌面兼容与实验命令

`doctor`、`status`、`install`、`verify` 是可选 Sideloadly 桌面兼容路径，要求用户安装 Sideloadly.app，并依赖窗口及系统 Automation/Accessibility 权限。它们与 `native-doctor`、`prepare`、`install-prepared` 不同。桌面 `install` 要求明确账号/设备/团队/包 ID 和 `--auto-refresh`，以自己的 `--help` 为准；不用于 SSH 无图形运行。

`headless-doctor`、`headless-plan`、`login --experimental-auth` 属于保留的独立后端研究接口，不是已支持的账号登录方案，也不为其分发专有库或实验二进制。正常使用选择 Xcode 会话。


## 实时进度与等待提示

所有命令启动时向 stderr 输出 `command_progress` JSONL 事件；命令仍在执行时，每 10 秒输出 `command_waiting`，包含当前阶段和 `elapsed_seconds`。一键安装以及独立 prepare / install-prepared / verify-prepared 会进一步报告设备检查、解包、描述文件、签名、安装和独立版本核验。刷新经过相同原生签名安装路径时也会报告这些阶段。

描述文件与签名阶段的 `current` / `total` 是实际条目数；并非总流程百分比。系统安装工具未提供可用的实时传输百分比，因此这里只显示安装阶段与等待时长。等待提示表示进程尚在运行，不证明底层操作持续前进，也不重试或延长原超时。其他命令至少提供启动及等待提示。

`sideload` 默认最终 JSON 写 stdout，过程写 stderr：

```sh
# 同时看到过程提示与最终结果
sideloadlycmd sideload App.ipa
# 最终 JSON 保存到文件，终端继续显示实时过程
sideloadlycmd sideload App.ipa > result.json
# 如需保留过程日志（含阶段和等待事件）
sideloadlycmd sideload App.ipa --log-file events.jsonl > result.json
```

如果运行环境隐藏了 stderr，需要展开对应的过程日志；只查看 stdout 会直到最终结果出现才看到输出。`background-run` 外层会实时报告自身运行和等待；后台工作进程的细分事件由后台隔离捕获，不承诺全部实时转发。所有过程事件沿用脱敏机制，不打印原始认证响应或子进程输出。

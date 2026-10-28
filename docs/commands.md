# 命令含义与选择指南

[完整参数](cli-reference.md) · [完整流程](usage.md)

## 只想把一个 IPA 装进去

```sh
sideloadlycmd sideload /path/App.ipa
```

仅连接一台 USB 设备时，`sideload` 自动选择它；零台会要求连接，多台会要求显式 `--device`。自动选择后固定该 USB 标识供签名和安装使用，不在中途切换到另一台。高级分步命令仍要求明确设备，以保持产物绑定。

一键 `sideload` 默认返回结构化 JSON，成功和失败结果都无需指定 `--json`；原有 `--json` 保留兼容。其他子命令仍通过 `--json` 选择结构化输出。事件日志写 stderr，最终结果写 stdout。

默认保留受支持组件。已知不支持的可选组件（目前包括 Watch、App Clip 和非 iPhoneOS 可选 bundle）自动移除并警告，不阻塞主应用。`--strip-watch` 显式移除 Watch；`--strip-extension 原始扩展ID` 显式移除指定扩展，可重复；`--strict-components` 则遇到不支持组件停止。详见[组件策略](components.md)。

这是原生一键安装：读取 IPA 原始包 ID、选择团队、生成新 prepared 目录、签名并严格验签、安装、独立核对设备版本/build。不自动登记刷新或启用长期任务。无需先配置组件选项；默认策略会在结果中列出移除项与功能影响。

默认自动选择唯一 Personal Team；没有 Personal Team 但只有一个可用团队时选择该团队。多个 Personal Team 或多团队歧义会停止，要求 `--team`。显式团队永远优先。Personal Team 的免费签名仍受 Apple 的有效期、App ID、设备和 entitlement 限制，不会因“优先”而获得公司/付费权益。

```sh
sideloadlycmd teams --json
sideloadlycmd sideload /path/App.ipa --device "My iPhone" --team ABCDE12345
# 只看选择与包 ID 计划，不签名、不安装，也不证明设备已核对：
sideloadlycmd sideload /path/App.ipa --device "My iPhone" --strip-watch --dry-run
```

`--output` 可指定新的 prepared 目录，否则放在用户 App Support 下的私有 prepared/sideload-*/prepared。结果的 `prepared` 字段用于后续只读复核和刷新登记。`--bundle-id` 可断言原始 ID，通常可省略；默认 automatic 模式生成 `<原始ID>.<TEAM>`。`--offline` 必须显式给 Team，不读取账号元数据推断团队，也不请求新 profile。

## 最简、指定设备与 Watch 示例

```sh
# 一台 USB 设备，自动处理可选组件：
sideloadlycmd sideload /path/App.ipa
# 多台设备，指定目标：
sideloadlycmd sideload /path/App.ipa --device "My iPhone"
# IPA 含 Watch，但本次只要 iPhone 部分：
sideloadlycmd sideload /path/App.ipa --strip-watch
# 给 AI 或脚本读取结果：
sideloadlycmd sideload /path/App.ipa
```

## 在 SSH 下执行

```sh
sideloadlycmd background-keychain unlock --timeout 600 --json
sideloadlycmd background-run --timeout 900 --json -- sideload /path/App.ipa \
  --device "My iPhone" --strip-watch
```

第一条在交互终端安全解锁 Background 钥匙串；第二条把同一条一键命令放进临时 Background 会话。`background-run` 是执行环境包装器，不是第二种签名后端。它保留退出码，结果在 `result` 中，完成后卸载临时任务。

## 其他命令分别做什么

### `inspect`：只读 IPA 元数据

查看原始主包 ID、版本、签名/加密状态和归档内容，不签名、不安装。适合先确定输入。

```sh
sideloadlycmd inspect /path/App.ipa --json
```

### `teams`：只读 Xcode 团队列表

返回团队 ID、是否能明确识别为 Personal Team、拥有该团队的保存账号数量。不会登录或创建证书，不输出账号邮箱。`--account` 可限定本机 Xcode 账号选择。

### `native-doctor`：只读运行前提检查

检查 Xcode、全局工具选择、libimobiledevice、账号/证书元数据。带 `--device` 时还检查 Xcode 是否接受该真机目标。成功不代表已经签名或安装。

### `prepare`：分步签名

需要明确原始包 ID、团队和全新输出目录。只生成并严格验证签名应用，不安装；适合在安装前检查产物或把签名与部署分开。

```sh
sideloadlycmd prepare /path/App.ipa --output /path/new-prepared --device "My iPhone" \
  --team ABCDE12345 --bundle-id com.example.app --bundle-id-mode automatic --strip-watch --json
```

### `install-prepared`：安装已有签名产物

重新验证签名、团队、设备与版本绑定，再安装并核对设备版本。使用最终 ID。

```sh
sideloadlycmd install-prepared /path/new-prepared --device "My iPhone" \
  --team ABCDE12345 --bundle-id com.example.app.ABCDE12345 --json
```

### `verify-prepared`：只读恢复安装核对

在此前已经报告安装完成、版本查询却未通过时使用。检查相同 prepared 与设备，不重新安装；无法把 completion_unknown 伪造成成功回执。

### `refresh-register`：登记，不实际续期

保存已核验安装的绑定及原始 IPA 私有快照，返回 job_id。命令成功为 0，但结果仍说明 renewal 未测试。

```sh
sideloadlycmd refresh-register /path/new-prepared --source-ipa /path/App.ipa \
  --device "My iPhone" --account xcode --json
```

### `refresh-native`：真正续期、重签、重装

对明确任务重新生成 profile，并检查全部组件有效期延长、安装与设备版本。`--force` 只是跳过到期时间判断，不保证 Apple 返回更晚的有效期。

```sh
sideloadlycmd background-run --json -- refresh-native --job "<job_id>" --force
```

### `refresh-status`：只读登记状态

查看本机任务、有效期、待复核安装、错误与下次尝试时间。不是 Sideloadly 桌面端的刷新列表。

### `refresh-scheduler`：长期调度管理

`enable` / `status` / `disable` 管理按小时检查的原生调度；`--persist` 保存未来用户域使用的配置。只有明确需要自动刷新时启用，不随一键安装偷偷开启。

### `background-keychain`：后台钥匙串检查/解锁

`status` 只读，`unlock` 需要交互终端输入 Mac 钥匙串密码。不登录 Apple ID，不保存密码，不修改 ACL。

## 名称相近但用途不同的旧命令

`install` 是保留的 Sideloadly **桌面兼容**命令，依赖 GUI；一键原生安装使用 `sideload`。`doctor/status/verify` 也属于桌面兼容路径，`native-doctor/verify-prepared` 属于原生路径。实验 `login/headless-*` 不用于正常 Xcode 账号流程。

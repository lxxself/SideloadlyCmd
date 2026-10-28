# IPA 组件策略

默认尽可能保留主应用和受支持的 iOS 扩展，包括 WidgetKit / 灵动岛相关扩展。不要求用户先拆包判断有哪些组件。

当前签名路径不支持的可选组件会在签名前自动移除：Watch、App Clip，以及元数据明确表明不属于 iPhoneOS 的可选 app / extension。结果中的 `removed_components`、`warnings` 和 prepared 清单记录路径、原因与功能影响；人工输出也会显示警告。移除 Watch 后没有配套手表功能，移除 App Clip 后没有轻 App 功能。保留灵动岛扩展意味着参与签名安装，不代表已验证该应用所有灵动岛行为。

```sh
# 默认策略：保留受支持组件，报告自动移除项
sideloadlycmd sideload App.ipa
# 先只读查看组件计划，不签名、不安装
sideloadlycmd sideload App.ipa --dry-run
# 显式排除 Watch 或指定扩展（使用 IPA 原始扩展 Bundle ID，可重复）
sideloadlycmd sideload App.ipa --strip-watch
sideloadlycmd sideload App.ipa --strip-extension com.example.app.LiveActivity
# 需要完整组件集时：遇到不支持组件就停止，显式排除项仍允许
sideloadlycmd sideload App.ipa --strict-components
```

显式移除灵动岛 / Widget 扩展会失去它提供的功能，可能影响主应用依赖该扩展的流程。默认不会移除普通扩展。主应用和 Frameworks / Libraries 等必需依赖不会当作可选组件删除。未知或损坏的元数据、加密 IPA、账号、权限、配额、签名和安装错误仍会报错，不会通过反复删除组件掩盖原因；因此不保证任意 IPA 都能成功安装。

`prepare` 同样支持这些选项。刷新登记会保存已排除组件的策略，后续刷新沿用，避免组件集意外变化；若要改变组件集，请从原始 IPA 重新准备、安装并登记。

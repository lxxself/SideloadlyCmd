# CLI 命令参考

本文件由 `python scripts/generate_cli_reference.py` 从实际 parser 生成。不要手改参数段。流程见 [usage.md](usage.md)。

## 退出码

- 0：本条命令完成；登记成功不代表实际续期已验收。
- 3：部分完成，例如设备复核或临时任务清理尚未确认。
- 2：参数错误。
- 10：原生预检或状态文件问题；11–13：桌面后端或自动化检查问题。
- 20：IPA 无效。
- 30：签名或安装报告失败。
- 31：超时，不能据此断言设备未改变。
- 32：需要用户介入或操作取消。
- 33：身份、绑定或结果无法确认。

原生命令：sideload（一键签名安装）、teams（团队列表）、native-doctor、prepare、install-prepared、verify-prepared、background-run、background-keychain、refresh-register、refresh-status、refresh-native、refresh-scheduler。

桌面兼容：doctor、status、install、verify，依赖 Sideloadly GUI。inspect 是只读 IPA 检查。headless-doctor、headless-plan、实验 login 不是正常账号管理流程。

## 全局入口

```text
usage: sideloadlycmd [-h] [--app APP]
                     {native-doctor,teams,sideload,headless-doctor,login,headless-plan,prepare,install-prepared,verify-prepared,refresh-register,refresh-status,refresh-native,refresh-scheduler,background-run,background-keychain,doctor,inspect,status,install,verify}
                     ...

Sign and install iOS apps through native Xcode CLI or the Sideloadly desktop
backend.

positional arguments:
  {native-doctor,teams,sideload,headless-doctor,login,headless-plan,prepare,install-prepared,verify-prepared,refresh-register,refresh-status,refresh-native,refresh-scheduler,background-run,background-keychain,doctor,inspect,status,install,verify}
    native-doctor       read-only macOS, Xcode, signing and USB prerequisites;
                        no GUI or login
    teams               list saved Xcode teams and Personal Team
                        classification; no login
    sideload            inspect, select a team, sign, install and verify an
                        IPA in one command
    headless-doctor     check the independent native CLI backend without
                        logging in
    login               experimental standalone login; normal signing reuses
                        the account managed by Xcode
    headless-plan       read-only native install resource plan for explicit
                        identities
    prepare             prepare a strictly verified signed app using a saved
                        Xcode account; does not install
    install-prepared    install a verified prepared app through CoreDevice;
                        refresh is separately verified
    verify-prepared     read-only recovery of device verification after a
                        completed native install; never reinstalls
    refresh-register    register one verified native install and cache its
                        accepted source IPA
    refresh-status      read independent native refresh registrations
    refresh-native      renew profiles, re-sign, reinstall and independently
                        check expiry extension
    refresh-scheduler   enable, disable or inspect hourly native refresh in
                        the launchd user domain
    background-run      run one native command in a temporary Background
                        session; no persistent scheduler
    background-keychain
                        inspect or securely unlock the keychain in launchd
                        Background session without GUI
    doctor              check the local app and read-only UI access
    inspect             validate an IPA and inspect its original signing state
    status              read current Sideloadly UI state without clicking
                        controls
    install             load and install an IPA through Sideloadly's public
                        GUI
    verify              read-only check of the installed app version and
                        daemon refresh entry

options:
  -h, --help            show this help message and exit
  --app APP             Sideloadly.app bundle path
```

## native-doctor

```text
usage: sideloadlycmd native-doctor [-h] [--team TEAM] [--account ACCOUNT]
                                   [--device DEVICE] [--offline]
                                   [--timeout TIMEOUT] [--json]
                                   [--log-file LOG_FILE]

options:
  -h, --help           show this help message and exit
  --team TEAM
  --account ACCOUNT
  --device DEVICE
  --offline
  --timeout TIMEOUT
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## teams

```text
usage: sideloadlycmd teams [-h] [--account ACCOUNT] [--json]
                           [--log-file LOG_FILE]

options:
  -h, --help           show this help message and exit
  --account ACCOUNT
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## sideload

```text
usage: sideloadlycmd sideload [-h] [--device DEVICE] [--team TEAM]
                              [--account ACCOUNT] [--output OUTPUT]
                              [--bundle-id BUNDLE_ID]
                              [--bundle-id-mode {exact,automatic}]
                              [--strip-watch]
                              [--strip-extension STRIP_EXTENSION]
                              [--strict-components] [--offline] [--dry-run]
                              [--timeout TIMEOUT] [--json]
                              [--log-file LOG_FILE]
                              ipa

positional arguments:
  ipa

options:
  -h, --help            show this help message and exit
  --device DEVICE       exact USB name or UDID; automatically selects only
                        when exactly one device is connected
  --team TEAM           explicit Team ID; otherwise prefer one unique Personal
                        Team
  --account ACCOUNT
  --output OUTPUT       new prepared directory; defaults to a private
                        generated directory
  --bundle-id BUNDLE_ID
                        optional original Bundle ID assertion; inferred from
                        IPA when omitted
  --bundle-id-mode {exact,automatic}
  --strip-watch         explicitly exclude Watch; known unsupported optional
                        components are otherwise omitted with warnings
  --strip-extension STRIP_EXTENSION
                        remove this original iOS extension Bundle ID;
                        repeatable
  --strict-components   stop instead of automatically omitting known
                        unsupported optional components
  --offline
  --dry-run             inspect the plan without signing, device verification
                        or installation
  --timeout TIMEOUT
  --json                print JSON (already the default for sideload; retained
                        for compatibility)
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## headless-doctor

```text
usage: sideloadlycmd headless-doctor [-h] --backend BACKEND
                                     [--timeout TIMEOUT] [--json]
                                     [--log-file LOG_FILE]

options:
  -h, --help           show this help message and exit
  --backend BACKEND    path of the reviewed native backend executable
  --timeout TIMEOUT
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## login

```text
usage: sideloadlycmd login [-h] [--experimental-auth] --backend BACKEND
                           [--state-dir STATE_DIR] [--timeout TIMEOUT]
                           [--json] [--log-file LOG_FILE]

options:
  -h, --help            show this help message and exit
  --experimental-auth   explicitly opt into the unverified standalone research
                        login; normal operation uses Xcode
  --backend BACKEND
  --state-dir STATE_DIR
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## headless-plan

```text
usage: sideloadlycmd headless-plan [-h] --backend BACKEND
                                   [--state-dir STATE_DIR] --device DEVICE
                                   --account ACCOUNT --team TEAM --bundle-id
                                   BUNDLE_ID
                                   [--bundle-id-mode {exact,automatic}]
                                   [--timeout TIMEOUT] [--json]
                                   [--log-file LOG_FILE]
                                   ipa

positional arguments:
  ipa

options:
  -h, --help            show this help message and exit
  --backend BACKEND
  --state-dir STATE_DIR
  --device DEVICE
  --account ACCOUNT
  --team TEAM
  --bundle-id BUNDLE_ID
  --bundle-id-mode {exact,automatic}
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## prepare

```text
usage: sideloadlycmd prepare [-h] --output OUTPUT --device DEVICE --team TEAM
                             --bundle-id BUNDLE_ID [--account ACCOUNT]
                             [--offline] [--bundle-id-mode {exact,automatic}]
                             [--strip-watch]
                             [--strip-extension STRIP_EXTENSION]
                             [--strict-components] [--timeout TIMEOUT]
                             [--json] [--log-file LOG_FILE]
                             ipa

positional arguments:
  ipa

options:
  -h, --help            show this help message and exit
  --output OUTPUT       new private output directory for the signed app and
                        manifest
  --device DEVICE
  --team TEAM
  --bundle-id BUNDLE_ID
  --account ACCOUNT     xcode or xcode:<local account identifier>; ambiguity
                        is rejected
  --offline             use existing cached profiles and Keychain identity
                        only; never log in or request profiles
  --bundle-id-mode {exact,automatic}
  --strip-watch         explicitly remove the optional Watch component for
                        iPhone-only signing
  --strip-extension STRIP_EXTENSION
                        remove an original iOS extension Bundle ID; repeatable
  --strict-components   require all non-explicitly-removed components to be
                        supported
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## install-prepared

```text
usage: sideloadlycmd install-prepared [-h] --device DEVICE --team TEAM
                                      --bundle-id BUNDLE_ID
                                      [--timeout TIMEOUT] [--json]
                                      [--log-file LOG_FILE]
                                      prepared

positional arguments:
  prepared

options:
  -h, --help            show this help message and exit
  --device DEVICE
  --team TEAM
  --bundle-id BUNDLE_ID
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## verify-prepared

```text
usage: sideloadlycmd verify-prepared [-h] --device DEVICE --team TEAM
                                     --bundle-id BUNDLE_ID [--timeout TIMEOUT]
                                     [--json] [--log-file LOG_FILE]
                                     prepared

positional arguments:
  prepared

options:
  -h, --help            show this help message and exit
  --device DEVICE
  --team TEAM
  --bundle-id BUNDLE_ID
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## refresh-register

```text
usage: sideloadlycmd refresh-register [-h] --source-ipa SOURCE_IPA --device
                                      DEVICE --account ACCOUNT
                                      [--state-dir STATE_DIR]
                                      [--timeout TIMEOUT] [--json]
                                      [--log-file LOG_FILE]
                                      prepared

positional arguments:
  prepared

options:
  -h, --help            show this help message and exit
  --source-ipa SOURCE_IPA
  --device DEVICE
  --account ACCOUNT
  --state-dir STATE_DIR
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## refresh-status

```text
usage: sideloadlycmd refresh-status [-h] [--state-dir STATE_DIR] [--json]
                                    [--log-file LOG_FILE]

options:
  -h, --help            show this help message and exit
  --state-dir STATE_DIR
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## refresh-native

```text
usage: sideloadlycmd refresh-native [-h] [--state-dir STATE_DIR] [--job JOB]
                                    [--account ACCOUNT] [--force]
                                    [--timeout TIMEOUT] [--json]
                                    [--log-file LOG_FILE]

options:
  -h, --help            show this help message and exit
  --state-dir STATE_DIR
  --job JOB             one native refresh job ID
  --account ACCOUNT     explicitly rebind one --job to the selected saved
                        Xcode account for its existing team
  --force               attempt one explicit job even before it is due
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## refresh-scheduler

```text
usage: sideloadlycmd refresh-scheduler [-h] [--state-dir STATE_DIR]
                                       [--persist] [--timeout TIMEOUT]
                                       [--json] [--log-file LOG_FILE]
                                       {enable,disable,status}

positional arguments:
  {enable,disable,status}

options:
  -h, --help            show this help message and exit
  --state-dir STATE_DIR
  --persist             also install the Background LaunchAgent for future
                        user-domain creation; reboot verification remains
                        separate
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## background-run

```text
usage: sideloadlycmd background-run [-h] [--timeout TIMEOUT] [--json]
                                    [--log-file LOG_FILE]
                                    ...

positional arguments:
  native_args

options:
  -h, --help           show this help message and exit
  --timeout TIMEOUT
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## background-keychain

```text
usage: sideloadlycmd background-keychain [-h] [--timeout TIMEOUT] [--json]
                                         [--log-file LOG_FILE]
                                         {status,unlock}

positional arguments:
  {status,unlock}

options:
  -h, --help           show this help message and exit
  --timeout TIMEOUT
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## doctor

```text
usage: sideloadlycmd doctor [-h] [--timeout TIMEOUT] [--json]
                            [--log-file LOG_FILE]

options:
  -h, --help           show this help message and exit
  --timeout TIMEOUT    UI query timeout in seconds (default: 12)
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## inspect

```text
usage: sideloadlycmd inspect [-h] [--timeout TIMEOUT] [--json]
                             [--log-file LOG_FILE]
                             ipa

positional arguments:
  ipa

options:
  -h, --help           show this help message and exit
  --timeout TIMEOUT    maximum archive inspection time in seconds
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## status

```text
usage: sideloadlycmd status [-h] [--timeout TIMEOUT] [--json]
                            [--log-file LOG_FILE]

options:
  -h, --help           show this help message and exit
  --timeout TIMEOUT    UI query timeout in seconds (default: 12)
  --json               print the final result as JSON
  --log-file LOG_FILE  append redacted JSONL events to a mode 0600 file
```

## install

```text
usage: sideloadlycmd install [-h] --device DEVICE --account ACCOUNT --team
                             TEAM --bundle-id BUNDLE_ID
                             [--bundle-id-mode {exact,automatic}]
                             --auto-refresh [--timeout TIMEOUT] [--json]
                             [--log-file LOG_FILE]
                             ipa

positional arguments:
  ipa

options:
  -h, --help            show this help message and exit
  --device DEVICE       exact target device label visible in Sideloadly
  --account ACCOUNT     Apple Account already configured in Sideloadly; never
                        logged
  --team TEAM           exact Team ID that must be selected in Sideloadly
  --bundle-id BUNDLE_ID
                        exact final Bundle ID to verify in the UI
  --bundle-id-mode {exact,automatic}
                        preserve Sideloadly automatic ID generation
                        explicitly, or require an exact ID
  --auto-refresh        require Auto Refresh enabled and verify it
  --timeout TIMEOUT     total install deadline in seconds (default: 600)
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

## verify

```text
usage: sideloadlycmd verify [-h] --device DEVICE --team TEAM --bundle-id
                            BUNDLE_ID [--bundle-id-mode {exact,automatic}]
                            [--timeout TIMEOUT] [--json] [--log-file LOG_FILE]
                            ipa

positional arguments:
  ipa

options:
  -h, --help            show this help message and exit
  --device DEVICE       exact selected Sideloadly device label, including its
                        identifier
  --team TEAM
  --bundle-id BUNDLE_ID
                        input Bundle ID
  --bundle-id-mode {exact,automatic}
  --timeout TIMEOUT
  --json                print the final result as JSON
  --log-file LOG_FILE   append redacted JSONL events to a mode 0600 file
```

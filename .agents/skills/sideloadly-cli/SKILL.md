---
name: sideloadly-cli
description: Use SideloadlyCmd to inspect a Mac, sign and install a user-provided IPA on an explicitly selected USB iPhone, verify installation, and optionally register or execute refreshes. Use the native CLI and Xcode-managed account for local, SSH, and Background workflows.
---

# Sideloadly CLI

Use the project's native CLI for IPA sideloading. The user supplies the IPA; sideload may select exactly one USB device when the user omitted a selector, while zero/multiple devices require connection or explicit selection; honor an explicit team, otherwise prefer exactly one Personal Team from saved Xcode metadata and refuse ambiguity. Reuse the Apple ID managed by Xcode; do not implement another login or operate the Sideloadly GUI as a substitute for native success.

## Locate the tool

Use an existing `sideloadlycmd` installation, `~/.local/bin/sideloadlycmd`, or `python3.12 scripts/sideloadlycmd` from a known checkout. Python 3.10+ is required; choose an available compatible interpreter, not blindly the system python3. If the checkout is absent, obtain its location or trusted source from the user rather than inventing a repository URL. Run `--help` and command-specific help to confirm the actual installed version.

For installation from a checkout, `python3.12 scripts/install_macos.py` creates a managed runtime and user launcher. With an explicit user request to configure short commands, `--add-to-path` adds idempotent zsh/bash PATH blocks; it does not source profiles automatically. Open a new terminal afterwards. No Homebrew listing is currently claimed; never invent a brew tap URL. Full Xcode and libimobiledevice are external prerequisites; the project is Python and does not require compiling Sideloadly. Do not install credentials, SDKs, or private libraries from another Mac.

## Decide what is authorized

An installation request authorizes the selected app/device workflow; it does not authorize deleting another app, revoking certificates, exporting keys, changing Keychain ACLs, enabling persistent refresh, or logging the Mac out. Ask only for missing target/IPA/team information or genuine system authorization. Do not request a second confirmation for the same already-authorized installation.

Passwords, verification codes, and administrator authorization are entered by the user in their own secure terminal or system UI, never sent to chat or placed in argv, environment variables, files, or logs. An expired Xcode session requires the user to restore it in Xcode. Stop repeated authentication failures instead of trying experimental login paths.

## Preflight and selections

- Run `inspect <ipa> --json` to identify the original bundle ID, version/build and component structure.
- Check `env -u DEVELOPER_DIR xcode-select -p` before recommending component downloads. Full Xcode must be selected globally for online preparation; a per-process DEVELOPER_DIR alone did not resolve the cross-Mac destination error. A global switch requires the user's normal administrator authorization.
- Use `idevice_id -l` for USB availability and `native-doctor --device "<exact name or UDID>" --team ABCDE12345 --account xcode --json` for prerequisites and Xcode destination eligibility. Do not expose raw device IDs in the final summary.
- Require exact device/team binding. For sideload only, an omitted device selects exactly one USB device and pins its identifier across phases. Multiple devices require --device; zero devices stop before signing. Do not choose another connected device, team or account to make a failed test pass. `--account xcode` rejects ambiguity; internal `xcode:<id>` selectors are local to that Mac.
- A saved identity or successful doctor check is not proof of online authentication, signing access or installation.

## Prefer one-command sideload for ordinary installation

Check `sideloadlycmd teams --json` when the team is not explicit. `is_personal` comes from actual saved Xcode metadata, not account or company names. A paid Individual team is not automatically a Personal Team. Multiple Personal Teams require selection; no Personal Team permits only a unique available team. Do not switch teams after a permission or quota error without the user's instruction.

```sh
sideloadlycmd sideload /path/App.ipa --dry-run --json
sideloadlycmd sideload /path/App.ipa
```

These minimal examples assume one USB device. The one-command sideload defaults to JSON, so omit --json there; other commands still require --json for structured output. Preserve supported optional components by default. Known unsupported optional components are automatically omitted with warnings and feature impacts. Use --strip-watch or --strip-extension ORIGINAL_ID for explicit exclusions; use --strict-components when all remaining components must be retained. The first command only inspects the plan; it does not verify the device or authorize new actions. Execute the second when installation is already authorized. The original bundle ID is inferred, default final ID appends the selected team, and output defaults to a fresh private prepared directory. Honor explicit `--team` / `--output` / ID-mode choices. The result's `prepared` path supports recovery and later refresh registration. Nothing silently enables persistent refresh. Offline use requires an explicit team and complete cached signing material.

For SSH, run the same sideload command after `background-run --json --`. For advanced separate preparation and deployment, use the commands below.

## Native signing and installation

Use fresh output paths. The template values must be replaced with the user's actual inputs:

```sh
sideloadlycmd prepare /path/App.ipa --output /path/new-prepared \
  --account xcode --device "My iPhone" --team ABCDE12345 \
  --bundle-id com.example.app --bundle-id-mode automatic --strip-watch --json
sideloadlycmd install-prepared /path/new-prepared --device "My iPhone" \
  --team ABCDE12345 --bundle-id com.example.app.ABCDE12345 --json
```

Prepare takes the IPA's **original** ID. Automatic mode appends the team and rewrites extensions. Install takes prepare's **final_bundle_id**; never append the suffix twice. Exact mode preserves the original ID and may fail if the team cannot register it.

`--strip-watch` explicitly removes Watch while keeping the main app and ordinary extensions. Honor explicit component choices. Default processing omits known unsupported optional components with warnings; never discard supported extensions or required frameworks to hide authentication, provisioning, signing, or installation errors. If Watch is required, use --strict-components and report the unsupported path. Offline preparation uses `--offline`, requires every matching cached profile, and must not fall back to online login or profile issuance.

## SSH and Background

SSH and Background Keychain state are separate. Use:

```sh
sideloadlycmd background-keychain status --json
sideloadlycmd background-keychain unlock --timeout 600 --json
sideloadlycmd background-run --timeout 900 --json -- native-doctor --device "My iPhone" --team ABCDE12345
```

The user runs unlock interactively and enters the Mac Keychain password hidden. Then prefix prepare, install-prepared, verify-prepared or refresh commands with `background-run --json --`. This public entry replaces historical research harnesses. It uses a finite temporary job, preserves native exit codes, and reports cleanup. It does not enable persistent scheduling. Its native JSON is under `result`, for example `result.job_id`.

## Recovery and refresh

If CoreDevice reports deployment completed but version verification is incomplete, run `verify-prepared` on the same prepared directory, exact device/team and final ID. It rechecks signatures, the completed receipt and device version without reinstalling. If completion is unknown, do not fabricate a receipt or blindly retry. Query failures are different from deployment failures.

Registration requires a verified installation:

```sh
sideloadlycmd refresh-register /path/new-prepared --source-ipa /path/App.ipa \
  --device "My iPhone" --account xcode --json
sideloadlycmd refresh-native --job "<returned-job-id>" --force --json
```

Use Background wrappers when appropriate. Register success means only registration; `renewal: not_tested` remains until a real refresh verifies expiry extension, re-signing, reinstall and device version. Preserve the original IPA snapshot and task identity. Enable `refresh-scheduler enable --persist` only when the user requests automatic refresh. Do not force all jobs when the user selected one.

## Report actual evidence

Distinguish inspected, prepared/signed, installed, device-version-verified, refresh-registered, actually renewed, and scheduler-loaded. Exit 0 means this command succeeded; 3 is partial; 31 is timeout; 32 requires user intervention. Check both exit code and structured state, including `temporary_job_removed`. Never turn a stub, cached profile, loaded symbol or zero-status process into installation proof.

Summarize versions, selected app identity, expiry changes and pending actions without account secrets or raw UDIDs. Keep machine-specific logs local. True logout/reboot resilience is optional and unverified unless separately demonstrated; do not require disruptive tests for routine CLI installation.

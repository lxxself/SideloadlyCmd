# SideloadlyCmd

[中文](README.md) · [Installation](docs/installation.md) · [Usage](docs/usage.md) · [CLI reference](docs/cli-reference.md) · [Development](docs/development.md) · [AI skill](.agents/skills/sideloadly-cli/SKILL.md)

A native macOS CLI for signing an IPA, installing it on an explicitly selected USB iPhone, verifying its bundle/version/build, and registering or executing refreshes. Normal operation reuses the account managed by Xcode and the local Keychain private key. It does not automate Sideloadly/Xcode windows, Accessibility, AppleScript, or the mouse.

The native signing, installation verification, and actual profile renewal flow has been tested on two Apple Silicon Macs. Supported everyday use covers a signed-in Mac accessed locally, through SSH, or through launchd Background jobs. Intel hardware, logged-out operation, reboot recovery, and future scheduled runs remain additional validation areas. Initial Xcode sign-in, device trust, and OS authorization still require the user when requested by the system.

## Requirements and installation

Use macOS, Python 3.10+, full Xcode, and libimobiledevice. Connect and unlock your iPhone, trust the Mac, and enable Developer Mode. Sign in through Xcode Settings → Accounts and ensure this Mac has an Apple Development private key. Do not export another Mac's private key or revoke its certificate automatically.

From this checkout:

```sh
brew install python@3.12 libimobiledevice
env -u DEVELOPER_DIR xcode-select -p
# Only if global selection is not full Xcode:
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
python3.12 scripts/install_macos.py --add-to-path
# Open a new terminal, then:
sideloadlycmd --help
```

The installer creates a managed runtime and a launcher in `~/.local/bin`; it edits user zsh/bash PATH profiles only with explicit `--add-to-path`, does not source them automatically, and does not sign in or enable scheduled refreshes. There is no claimed PyPI release: install from this checkout or a verified release artifact. The detailed linked guides are currently in Chinese; CLI help is in English.

## One-command sideload

```sh
sideloadlycmd teams --json
sideloadlycmd sideload /path/App.ipa
```

The command selects exactly one connected USB device (multiple devices require `--device`), infers the source bundle ID, prefers one unique Personal Team, prepares and installs the app, then verifies its version/build. Use `--team ABCDE12345` to override; multiple Personal Teams require explicit selection. `sideload` outputs JSON by default; `--json` can be omitted. Supported optional components are preserved; known unsupported optional components are omitted with warnings. Use `--strict-components` to stop instead. See [component policy](docs/components.md). It does not enable scheduled refreshes. `--dry-run` prints a plan without signing or installing. See [command meanings](docs/commands.md). A [Homebrew formula generator](docs/homebrew.md) is provided for a future maintainer tap; no tap/core listing is claimed.

## Sign and install

Replace every sample path, device name, team, and bundle ID:

```sh
~/.local/bin/sideloadlycmd native-doctor --device "My iPhone" --team ABCDE12345 --json
~/.local/bin/sideloadlycmd prepare /path/App.ipa \
  --output /path/new-prepared --account xcode --team ABCDE12345 \
  --device "My iPhone" --bundle-id com.example.app \
  --bundle-id-mode automatic --strip-watch --json
~/.local/bin/sideloadlycmd install-prepared /path/new-prepared \
  --device "My iPhone" --team ABCDE12345 \
  --bundle-id com.example.app.ABCDE12345 --json
```

Preparation takes the IPA's original bundle ID. Automatic mode appends the team ID and rewrites extension identifiers; installation takes the resulting final ID. The output directory must not exist. `--strip-watch` explicitly removes the optional Watch component while retaining the main app and ordinary extensions.

For SSH, unlock the Background Keychain interactively and run native commands through the public one-shot dispatcher:

```sh
~/.local/bin/sideloadlycmd background-keychain unlock --timeout 600 --json
~/.local/bin/sideloadlycmd background-run --json -- native-doctor --device "My iPhone" --team ABCDE12345
```

Use the same `background-run --json --` prefix for preparation, installation, read-only verification, and refresh commands. The dispatcher preserves the native exit code, reports security context, and removes the temporary job. Never give passwords or verification codes to an AI assistant or put them in command arguments.

## Verification, refresh, and automation

If deployment completed but device verification did not, use `verify-prepared` before trying another install. Refresh registration requires a verified install and creates a private immutable copy of the original IPA. Registration success returns 0 but leaves `renewal: not_tested`; real renewal and scheduled execution are separate actions. Enable a persistent scheduler only when requested.

Exit 0 means the requested command completed; 3 means partial completion; 31 means timeout; 32 requires user intervention. Do not treat an installation timeout as proof that nothing was installed. Structured results go to stdout with `--json`, redacted events go to stderr. See [all exit codes](docs/cli-reference.md).

The embedded skill is `.agents/skills/sideloadly-cli/SKILL.md`. Use a project-skill-aware assistant, or explicitly ask:

> Read `.agents/skills/sideloadly-cli/SKILL.md`. Check this Mac and install `/path/App.ipa` on `My iPhone`, team `ABCDE12345`, using the account managed by Xcode. I will handle any password or system prompt locally. Verify the installed version and do not enable scheduled refreshes.

More prompts: [AI usage](docs/ai-usage.md).

## Build and contribute

This is a Python project; there is no native extension compilation step:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip build
python -m pip install -e .
PYTHONPATH=src python -m unittest discover -s tests -v
python -m build
python scripts/build_portable.py
```

Public tests use fixtures and mocks and do not sign in or install an app on your phone. Machine-specific records and historical experiments belong in ignored `.local-dev/`, not in commits or release archives. The source and portable archives include the project skill; wheel installation installs the CLI and does not silently modify any AI tool's global skills.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [third-party boundaries](docs/third-party.md). Licensed under [MIT](LICENSE). Apple SDKs, Sideloadly binaries, accounts, private keys, and IPAs are not distributed by this project.

Commands emit stage events to stderr and a wait notification every 10 seconds while running. Sideload keeps its final JSON on stdout; signing reports actual item counts, without estimated transfer percentages. See [progress and logging](docs/usage.md).

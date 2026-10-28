from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import platform
import re
import select
import signal
import stat
import subprocess
import tempfile
import sys
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import __version__
from .automation import (
    APP_BUNDLE_ID,
    DEFAULT_APP,
    AppleScriptUI,
    AutomationError,
    UISnapshot,
    app_metadata,
    bundle_id_control,
    contains_auth_prompt,
    contains_busy,
    contains_failure,
    contains_success,
    exact_value_matches,
    is_checked,
    launch_app_with_ipa,
    node_matches,
    require_visible_identity,
    sanitized_snapshot_summary,
    sanitized_device_label,
    selected_control_matches,
    success_transitioned,
    start_button,
    wait_for_running,
)
from .errors import CliError, ExitCode
from .ipa import IPAError, IPAInfo, inspect_ipa


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("<redacted>" if re.search(r"account|email|password|token|credential", key, re.I) and not isinstance(item, bool) else item if key == "job_id" and isinstance(item, str) and re.fullmatch(r"[a-f0-9]{24}", item) else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<redacted-email>", value)
        value = re.sub(r"\b(?:[0-9A-Fa-f]{8}-?){2,}[0-9A-Fa-f]{4,}\b", "<redacted-device-id>", value)
    return value


class JsonLogger:
    def __init__(self, logfile: Path | None):
        self.lock = threading.RLock()
        self.logfile = logfile
        self.stream = None
        if logfile:
            logfile = logfile.expanduser()
            logfile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(logfile, flags, 0o600)
            try:
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                self.stream = os.fdopen(fd, "a", encoding="utf-8")
            except Exception:
                os.close(fd)
                raise

    def emit(self, event: str, level: str = "info", **fields: Any) -> None:
        with self.lock:
            row = _redact({"timestamp": _timestamp(), "level": level, "event": event, **fields})
            line = json.dumps(row, ensure_ascii=False, sort_keys=True)
            print(line, file=sys.stderr, flush=True)
            if self.stream:
                self.stream.write(line + "\n")
                self.stream.flush()

    def close(self) -> None:
        if self.stream:
            self.stream.close()


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print the final result as JSON")
    parser.add_argument("--log-file", type=Path, help="append redacted JSONL events to a mode 0600 file")


def _seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 3600:
        raise argparse.ArgumentTypeError("timeout must be between 0 and 3600 seconds")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sideloadlycmd", description="Sign and install iOS apps through native Xcode CLI or the Sideloadly desktop backend.")
    parser.add_argument("--app", type=Path, default=DEFAULT_APP, help="Sideloadly.app bundle path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    native = subparsers.add_parser("native-doctor", help="read-only macOS, Xcode, signing and USB prerequisites; no GUI or login")
    native.add_argument("--team")
    native.add_argument("--account", default="xcode")
    native.add_argument("--device")
    native.add_argument("--offline", action="store_true")
    native.add_argument("--timeout", type=_seconds, default=30.0)
    _add_output_options(native)

    teams = subparsers.add_parser("teams", help="list saved Xcode teams and Personal Team classification; no login")
    teams.add_argument("--account", default="xcode")
    _add_output_options(teams)

    one = subparsers.add_parser("sideload", help="inspect, select a team, sign, install and verify an IPA in one command")
    one.add_argument("ipa", type=Path)
    one.add_argument("--device", help="exact USB name or UDID; automatically selects only when exactly one device is connected")
    one.add_argument("--team", help="explicit Team ID; otherwise prefer one unique Personal Team")
    one.add_argument("--account", default="xcode")
    one.add_argument("--output", type=Path, help="new prepared directory; defaults to a private generated directory")
    one.add_argument("--bundle-id", help="optional original Bundle ID assertion; inferred from IPA when omitted")
    one.add_argument("--bundle-id-mode", choices=("exact","automatic"), default="automatic")
    one.add_argument("--strip-watch", action="store_true", help="explicitly exclude Watch; known unsupported optional components are otherwise omitted with warnings")
    one.add_argument("--strip-extension", action="append", default=[], help="remove this original iOS extension Bundle ID; repeatable")
    one.add_argument("--strict-components", action="store_true", help="stop instead of automatically omitting known unsupported optional components")
    one.add_argument("--offline", action="store_true")
    one.add_argument("--dry-run", action="store_true", help="inspect the plan without signing, device verification or installation")
    one.add_argument("--timeout", type=_seconds, default=900.0)
    _add_output_options(one)
    one.set_defaults(json=True)
    one._option_string_actions["--json"].help = "print JSON (already the default for sideload; retained for compatibility)"

    native_doctor = subparsers.add_parser("headless-doctor", help="check the independent native CLI backend without logging in")
    native_doctor.add_argument("--backend", type=Path, required=True, help="path of the reviewed native backend executable")
    native_doctor.add_argument("--timeout", type=_seconds, default=20.0)
    _add_output_options(native_doctor)

    login = subparsers.add_parser("login", help="experimental standalone login; normal signing reuses the account managed by Xcode")
    login.add_argument("--experimental-auth", action="store_true", help="explicitly opt into the unverified standalone research login; normal operation uses Xcode")
    login.add_argument("--backend", type=Path, required=True)
    login.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/SideloadlyCmd/headless")
    login.add_argument("--timeout", type=_seconds, default=600.0)
    _add_output_options(login)

    plan = subparsers.add_parser("headless-plan", help="read-only native install resource plan for explicit identities")
    plan.add_argument("ipa", type=Path)
    plan.add_argument("--backend", type=Path, required=True)
    plan.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/SideloadlyCmd/headless")
    for name in ("device", "account", "team", "bundle-id"):
        plan.add_argument("--" + name, required=True)
    plan.add_argument("--bundle-id-mode", choices=("exact", "automatic"), default="exact")
    plan.add_argument("--timeout", type=_seconds, default=120.0)
    _add_output_options(plan)

    prepare = subparsers.add_parser("prepare", help="prepare a strictly verified signed app using a saved Xcode account; does not install")
    prepare.add_argument("ipa", type=Path)
    prepare.add_argument("--output", type=Path, required=True, help="new private output directory for the signed app and manifest")
    for name in ("device", "team", "bundle-id"):
        prepare.add_argument("--" + name, required=True)
    prepare.add_argument("--account", default="xcode", help="xcode or xcode:<local account identifier>; ambiguity is rejected")
    prepare.add_argument("--offline", action="store_true", help="use existing cached profiles and Keychain identity only; never log in or request profiles")
    prepare.add_argument("--bundle-id-mode", choices=("exact", "automatic"), default="exact")
    prepare.add_argument("--strip-watch", action="store_true", help="explicitly remove the optional Watch component for iPhone-only signing")
    prepare.add_argument("--strip-extension", action="append", default=[], help="remove an original iOS extension Bundle ID; repeatable")
    prepare.add_argument("--strict-components", action="store_true", help="require all non-explicitly-removed components to be supported")
    prepare.add_argument("--timeout", type=_seconds, default=600.0)
    _add_output_options(prepare)

    native_install = subparsers.add_parser("install-prepared", help="install a verified prepared app through CoreDevice; refresh is separately verified")
    native_install.add_argument("prepared", type=Path)
    for name in ("device", "team", "bundle-id"):
        native_install.add_argument("--" + name, required=True)
    native_install.add_argument("--timeout", type=_seconds, default=300.0)
    _add_output_options(native_install)

    native_verify = subparsers.add_parser("verify-prepared", help="read-only recovery of device verification after a completed native install; never reinstalls")
    native_verify.add_argument("prepared", type=Path)
    for name in ("device", "team", "bundle-id"):
        native_verify.add_argument("--" + name, required=True)
    native_verify.add_argument("--timeout", type=_seconds, default=120.0)
    _add_output_options(native_verify)

    register = subparsers.add_parser("refresh-register", help="register one verified native install and cache its accepted source IPA")
    register.add_argument("prepared", type=Path)
    register.add_argument("--source-ipa", type=Path, required=True)
    register.add_argument("--device", required=True)
    register.add_argument("--account", required=True)
    register.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/SideloadlyCmd/native")
    register.add_argument("--timeout", type=_seconds, default=120.0)
    _add_output_options(register)
    refresh_status = subparsers.add_parser("refresh-status", help="read independent native refresh registrations")
    refresh_status.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/SideloadlyCmd/native")
    _add_output_options(refresh_status)
    refresh = subparsers.add_parser("refresh-native", help="renew profiles, re-sign, reinstall and independently check expiry extension")
    refresh.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/SideloadlyCmd/native")
    refresh.add_argument("--job", help="one native refresh job ID")
    refresh.add_argument("--account", help="explicitly rebind one --job to the selected saved Xcode account for its existing team")
    refresh.add_argument("--force", action="store_true", help="attempt one explicit job even before it is due")
    refresh.add_argument("--timeout", type=_seconds, default=900.0)
    _add_output_options(refresh)

    scheduler = subparsers.add_parser("refresh-scheduler", help="enable, disable or inspect hourly native refresh in the launchd user domain")
    scheduler.add_argument("action", choices=("enable", "disable", "status"))
    scheduler.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/SideloadlyCmd/native")
    scheduler.add_argument("--persist", action="store_true", help="also install the Background LaunchAgent for future user-domain creation; reboot verification remains separate")
    scheduler.add_argument("--timeout", type=_seconds, default=30.0)
    _add_output_options(scheduler)

    background = subparsers.add_parser("background-run", help="run one native command in a temporary Background session; no persistent scheduler")
    background.add_argument("--timeout", type=_seconds, default=900.0)
    _add_output_options(background)
    background.add_argument("native_args", nargs=argparse.REMAINDER)

    keychain = subparsers.add_parser("background-keychain", help="inspect or securely unlock the keychain in launchd Background session without GUI")
    keychain.add_argument("action", choices=("status", "unlock"))
    keychain.add_argument("--timeout", type=_seconds, default=300.0)
    _add_output_options(keychain)

    doctor = subparsers.add_parser("doctor", help="check the local app and read-only UI access")
    doctor.add_argument("--timeout", type=_seconds, default=12.0, help="UI query timeout in seconds (default: 12)")
    _add_output_options(doctor)

    inspect = subparsers.add_parser("inspect", help="validate an IPA and inspect its original signing state")
    inspect.add_argument("ipa", type=Path)
    inspect.add_argument("--timeout", type=_seconds, default=120.0, help="maximum archive inspection time in seconds")
    _add_output_options(inspect)

    status = subparsers.add_parser("status", help="read current Sideloadly UI state without clicking controls")
    status.add_argument("--timeout", type=_seconds, default=12.0, help="UI query timeout in seconds (default: 12)")
    _add_output_options(status)

    install = subparsers.add_parser("install", help="load and install an IPA through Sideloadly's public GUI")
    install.add_argument("ipa", type=Path)
    install.add_argument("--device", required=True, help="exact target device label visible in Sideloadly")
    install.add_argument("--account", required=True, help="Apple Account already configured in Sideloadly; never logged")
    install.add_argument("--team", required=True, help="exact Team ID that must be selected in Sideloadly")
    install.add_argument("--bundle-id", required=True, help="exact final Bundle ID to verify in the UI")
    install.add_argument("--bundle-id-mode", choices=("exact", "automatic"), default="exact", help="preserve Sideloadly automatic ID generation explicitly, or require an exact ID")
    install.add_argument("--auto-refresh", action="store_true", required=True, help="require Auto Refresh enabled and verify it")
    install.add_argument("--timeout", type=_seconds, default=600.0, help="total install deadline in seconds (default: 600)")
    _add_output_options(install)
    verify = subparsers.add_parser("verify", help="read-only check of the installed app version and daemon refresh entry")
    verify.add_argument("ipa", type=Path)
    verify.add_argument("--device", required=True, help="exact selected Sideloadly device label, including its identifier")
    verify.add_argument("--team", required=True)
    verify.add_argument("--bundle-id", required=True, help="input Bundle ID")
    verify.add_argument("--bundle-id-mode", choices=("exact", "automatic"), default="exact")
    verify.add_argument("--timeout", type=_seconds, default=60.0)
    _add_output_options(verify)
    return parser


def _emit_result(result: dict[str, Any], as_json: bool) -> None:
    public = _redact(result)
    if as_json:
        print(json.dumps(public, ensure_ascii=False, sort_keys=True))
        return
    status = public.get("state", "complete")
    summary = public.get("message", status)
    print(f"{status}: {summary}")


def _ensure_app(app_path: Path) -> tuple[str | None, str | None]:
    version, bundle_id = app_metadata(app_path.expanduser())
    if not version or bundle_id != APP_BUNDLE_ID:
        raise CliError("Sideloadly.app is missing, unreadable, or has an unexpected bundle ID", ExitCode.APP_MISSING, "app_missing")
    return version, bundle_id


def _handle_automation_error(exc: AutomationError, logger: JsonLogger) -> CliError:
    logger.emit("automation_error", level="error", category=exc.category, message=str(exc))
    return CliError(str(exc), exc.code, exc.category)


def _run_doctor(args: argparse.Namespace, logger: JsonLogger) -> tuple[dict[str, Any], ExitCode]:
    if sys.platform != "darwin":
        raise CliError("GUI automation requires macOS", ExitCode.PREFLIGHT, "unsupported_platform")
    version, bundle_id = _ensure_app(args.app)
    ui = AppleScriptUI(timeout_seconds=args.timeout)
    try:
        snapshot = ui.snapshot()
    except AutomationError as exc:
        raise _handle_automation_error(exc, logger) from exc
    result = {
        "command": "doctor",
        "state": "ready_for_ui_check" if snapshot.running else "app_not_running",
        "cli_version": __version__,
        "macos": platform.mac_ver()[0],
        "app_version": version,
        "app_bundle_id": bundle_id,
        "app_path": args.app.name,
        "ui": sanitized_snapshot_summary(snapshot),
        "message": "UI is readable; no signing or installation was started" if snapshot.running else "Sideloadly is installed but not running; doctor did not launch it",
    }
    logger.emit("doctor_complete", app_version=version, app_running=snapshot.running, ui_summary=result["ui"])
    return result, ExitCode.OK


def _run_status(args: argparse.Namespace, logger: JsonLogger) -> tuple[dict[str, Any], ExitCode]:
    if sys.platform != "darwin":
        raise CliError("GUI automation requires macOS", ExitCode.PREFLIGHT, "unsupported_platform")
    version, _ = _ensure_app(args.app)
    ui = AppleScriptUI(timeout_seconds=args.timeout)
    try:
        snapshot = ui.snapshot()
    except AutomationError as exc:
        raise _handle_automation_error(exc, logger) from exc
    if not snapshot.running:
        state = "not_running"
    elif contains_failure(snapshot):
        state = "failure_text_visible"
    elif contains_auth_prompt(snapshot):
        state = "needs_user_authentication"
    elif contains_busy(snapshot):
        state = "busy"
    elif contains_success(snapshot):
        state = "unattributed_success_text"
    else:
        state = "idle_or_unknown"
    result = {
        "command": "status",
        "state": state,
        "app_version": version,
        "task_attribution": "unavailable_without_a_cli_started_install",
        "ui": sanitized_snapshot_summary(snapshot),
        "message": "read-only UI state; visible completion text is not attributed to a CLI install",
    }
    logger.emit("status_read", state=state, app_version=version, ui_summary=result["ui"])
    return result, ExitCode.OK


def _run_inspect(args: argparse.Namespace, logger: JsonLogger) -> tuple[dict[str, Any], ExitCode]:
    try:
        info = inspect_ipa(args.ipa, timeout_seconds=args.timeout)
    except IPAError as exc:
        timed_out = "exceeded its time limit" in str(exc).lower()
        code = ExitCode.TIMEOUT if timed_out else ExitCode.INVALID_IPA
        category = "timeout" if timed_out else "invalid_ipa"
        logger.emit("ipa_inspection_failed", level="error", category=category, filename=args.ipa.name)
        raise CliError(str(exc), code, category) from exc
    result = {"command": "inspect", "state": "valid", "ipa": info.public_dict(), "message": "archive and device app metadata validated; signing data was checked in a temporary directory where supported"}
    logger.emit("ipa_inspected", filename=info.filename, bundle_id=info.bundle_id, version=info.version)
    return result, ExitCode.OK


@contextmanager
def _install_mutex(deadline: float) -> Iterator[None]:
    lock_dir = Path.home() / "Library" / "Application Support" / "SideloadlyCmd"
    try:
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError("could not create the per-user install lock directory", ExitCode.PREFLIGHT, "lock_unavailable") from exc
    lock_path = lock_dir / "install.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise CliError("could not open the per-user install lock", ExitCode.PREFLIGHT, "lock_unavailable") from exc
    os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CliError("another SideloadlyCmd install still owns the install lock", ExitCode.TIMEOUT, "lock_timeout")
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise CliError("total install timeout expired", ExitCode.TIMEOUT, "timeout")
    return value


def _wait_for_manual_auth(ui: AppleScriptUI, deadline: float, logger: JsonLogger) -> UISnapshot:
    if not sys.stdin.isatty():
        raise CliError("authentication is required; complete it in Sideloadly, then run status", ExitCode.NEEDS_USER, "user_authentication_required")
    print("Complete the password, verification, or system prompt in Sideloadly. Press Enter here when done.", file=sys.stderr, flush=True)
    logger.emit("waiting_for_user_authentication", level="warning")
    input_fd = sys.stdin.fileno()
    while time.monotonic() < deadline:
        wait_seconds = min(0.5, max(0.0, deadline - time.monotonic()))
        ready, _, _ = select.select([input_fd], [], [], wait_seconds)
        if ready:
            sys.stdin.readline()
        try:
            snapshot = ui.snapshot()
        except AutomationError as exc:
            raise _handle_automation_error(exc, logger) from exc
        if not contains_auth_prompt(snapshot):
            logger.emit("authentication_prompt_cleared")
            return snapshot
    raise CliError("authentication was not completed before the total timeout", ExitCode.TIMEOUT, "timeout")


def _select_exact(
    ui: AppleScriptUI,
    snapshot: UISnapshot,
    value: str,
    label: str,
    eligible_roles: set[str],
    selector_phrases: tuple[str, ...],
    deadline: float,
) -> UISnapshot:
    matches = selected_control_matches(snapshot, value, eligible_roles)
    if len(matches) == 1:
        return snapshot
    if len(matches) > 1:
        raise AutomationError("identity_ambiguous", f"requested {label} appeared more than once in the UI", ExitCode.UNSUPPORTED_UI)

    selectors: list[Any] = []
    for role in ("AXPopUpButton", "AXComboBox"):
        for phrase in selector_phrases:
            selectors.extend(node_matches(snapshot, role, phrase))
    unique = {(node.role, node.name): node for node in selectors}
    if len(unique) != 1:
        raise AutomationError("identity_not_confirmable", f"could not identify a unique {label} selector in the current UI", ExitCode.UNSUPPORTED_UI)
    selector = next(iter(unique.values()))
    ui.click(selector.role, selector.name)
    time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    opened = ui.snapshot()
    options = [node for node in exact_value_matches(opened, value) if node.role in {"AXMenuItem", "AXRadioButton", "AXRow"}]
    if len(options) != 1:
        raise AutomationError("identity_option_not_found", f"the requested {label} was not a unique choice in the Sideloadly menu", ExitCode.UNSUPPORTED_UI)
    if options[0].value.strip().casefold() == value.casefold():
        ui.click_value(options[0].role, value)
    else:
        ui.click(options[0].role, options[0].name)
    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    selected = ui.snapshot()
    confirmed = selected_control_matches(selected, value, eligible_roles)
    if len(confirmed) != 1:
        raise AutomationError("identity_selection_unverified", f"Sideloadly did not confirm the requested {label}", ExitCode.UNSUPPORTED_UI)
    return selected


def _ensure_install_settings(args: argparse.Namespace, ui: AppleScriptUI, snapshot: UISnapshot, deadline: float) -> UISnapshot:
    if contains_auth_prompt(snapshot):
        raise AutomationError("user_authentication_required", "complete the account or system authentication in Sideloadly, then check status", ExitCode.NEEDS_USER)
    if contains_failure(snapshot) or contains_busy(snapshot):
        raise AutomationError("app_not_idle", "Sideloadly shows an existing failure or active operation; inspect it before starting another", ExitCode.UNKNOWN)

    account_roles = {"AXTextField", "AXComboBox", "AXPopUpButton"}
    require_visible_identity(snapshot, args.account, "Apple Account", account_roles)

    device_roles = {"AXTextField", "AXComboBox", "AXPopUpButton"}
    snapshot = _select_exact(ui, snapshot, args.device, "target device", device_roles, ("device", "iphone", "ipad", "设备", "手机"), deadline)

    team_roles = {"AXTextField", "AXComboBox", "AXPopUpButton"}
    team_controls = [node for node in snapshot.nodes if node.role in team_roles and args.team.casefold() in node.text.casefold()]
    if team_controls:
        snapshot = _select_exact(ui, snapshot, args.team, "Team ID", team_roles, ("team", "团队"), deadline)
    args._team_preflight_verified = bool(team_controls)
    # 0.71.1 presents the team chooser only after Start. It is handled in the
    # installation loop; a requested team is never replaced by the default row.

    automatic = node_matches(snapshot, "AXCheckBox", "Use automatic bundle ID")
    mode = getattr(args, "bundle_id_mode", "exact")
    if len(automatic) != 1 or is_checked(automatic[0]) != (mode == "automatic"):
        raise AutomationError("bundle_id_mode_mismatch", "Sideloadly Bundle ID mode differs from --bundle-id-mode; inspect the selected setting", ExitCode.UNSUPPORTED_UI)
    bundle_fields = bundle_id_control(snapshot)
    if len(bundle_fields) > 1:
        raise AutomationError("bundle_id_ambiguous", "multiple Bundle ID fields are visible", ExitCode.UNSUPPORTED_UI)
    if len(bundle_fields) == 1 and bundle_fields[0].value.strip() != args.bundle_id:
        ui.set_value("AXTextField", bundle_fields[0].selector, args.bundle_id)
        snapshot = ui.snapshot()
        bundle_fields = bundle_id_control(snapshot)
    bundle_exact = [node for node in exact_value_matches(snapshot, args.bundle_id) if node.role == "AXTextField" and node.value.strip() == args.bundle_id]
    if len(bundle_exact) != 1:
        raise AutomationError("bundle_id_unverified", "could not uniquely verify the final Bundle ID in Sideloadly", ExitCode.UNSUPPORTED_UI)

    refresh_controls = node_matches(snapshot, "AXCheckBox", "refresh")
    if not refresh_controls:
        refresh_controls = node_matches(snapshot, "AXCheckBox", "自动刷新")
    if len(refresh_controls) != 1:
        raise AutomationError("refresh_control_unverified", "could not uniquely identify the Auto Refresh checkbox", ExitCode.UNSUPPORTED_UI)
    refresh_control = refresh_controls[0]
    if not is_checked(refresh_control):
        ui.click("AXCheckBox", refresh_control.selector)
        snapshot = ui.snapshot()
        refresh_controls = node_matches(snapshot, "AXCheckBox", refresh_control.selector)
        if len(refresh_controls) != 1 or not is_checked(refresh_controls[0]):
            raise AutomationError("refresh_not_enabled", "Auto Refresh did not become enabled in Sideloadly", ExitCode.UNSUPPORTED_UI)

    # Repeat the account and device checks after opening selectors so that a
    # menu interaction cannot silently replace either identity.
    require_visible_identity(snapshot, args.account, "Apple Account", account_roles)
    require_visible_identity(snapshot, args.device, "target device", device_roles)
    if team_controls:
        require_visible_identity(snapshot, args.team, "Team ID", team_roles)
    bundle_exact = [node for node in exact_value_matches(snapshot, args.bundle_id) if node.role == "AXTextField" and node.value.strip() == args.bundle_id]
    if len(bundle_exact) != 1:
        raise AutomationError("bundle_id_unverified", "final Bundle ID changed or became ambiguous before Start", ExitCode.UNSUPPORTED_UI)
    refresh_controls = node_matches(snapshot, "AXCheckBox", refresh_control.selector)
    if len(refresh_controls) != 1 or not is_checked(refresh_controls[0]):
        raise AutomationError("refresh_not_enabled", "Auto Refresh is not confirmed enabled before Start", ExitCode.UNSUPPORTED_UI)
    return snapshot


def _select_install_team(ui: AppleScriptUI, snapshot: UISnapshot, team: str, deadline: float) -> UISnapshot:
    choosers = [node for node in snapshot.nodes if node.role == "AXComboBox" and re.match(r"^[A-Z0-9]{10}:", node.name)]
    if len(choosers) != 1:
        raise AutomationError("team_selection_unverified", "team dialog selector could not be uniquely identified", ExitCode.NEEDS_USER)
    if not choosers[0].name.startswith(team + ":"):
        ui.click(choosers[0].role, choosers[0].selector)
        opened = ui.snapshot()
        choices = [node for node in opened.nodes if node.role in {"AXStaticText", "AXMenuItem"} and node.name.startswith(team + ":")]
        if len(choices) != 1:
            raise AutomationError("team_option_unavailable", "requested Team ID is not a unique choice in the team dialog", ExitCode.NEEDS_USER)
        label = choices[0].name
        ui.click(choices[0].role, choices[0].selector)
        snapshot = ui.snapshot()
        confirmed = [node for node in snapshot.nodes if node.role == "AXComboBox" and node.name == label]
        if len(confirmed) != 1:
            raise AutomationError("team_selection_unverified", "requested team was not confirmed selected", ExitCode.NEEDS_USER)
    buttons = [node for node in snapshot.nodes if node.role == "AXButton" and node.name == "OK"]
    if len(buttons) != 1:
        raise AutomationError("team_confirmation_unavailable", "team dialog OK button is not unique", ExitCode.UNSUPPORTED_UI)
    ui.click(buttons[0].role, buttons[0].selector)
    time.sleep(min(0.3, _remaining(deadline)))
    return ui.snapshot()


def _run_install(args: argparse.Namespace, logger: JsonLogger) -> tuple[dict[str, Any], ExitCode]:
    if sys.platform != "darwin":
        raise CliError("GUI automation requires macOS", ExitCode.PREFLIGHT, "unsupported_platform")
    if not args.auto_refresh:
        raise CliError("--auto-refresh is required for an install", ExitCode.USAGE, "missing_refresh_confirmation")
    if not args.bundle_id.strip() or not args.team.strip() or not args.device.strip() or not args.account.strip():
        raise CliError("device, account, team, and bundle ID must be non-empty", ExitCode.USAGE, "missing_identity")
    if args.timeout < 10 or args.timeout > 3600:
        raise CliError("--timeout must be between 10 and 3600 seconds", ExitCode.USAGE, "invalid_timeout")

    app_path = args.app.expanduser()
    app_version, _ = _ensure_app(app_path)
    deadline = time.monotonic() + args.timeout
    ui = AppleScriptUI(timeout_seconds=min(10.0, args.timeout))
    ui.set_deadline(deadline)
    try:
        with _install_mutex(deadline):
            logger.emit("install_lock_acquired", filename=args.ipa.name, app_version=app_version)
            try:
                info = inspect_ipa(args.ipa, timeout_seconds=min(120.0, _remaining(deadline)))
            except IPAError as exc:
                timed_out = "exceeded its time limit" in str(exc).lower()
                code = ExitCode.TIMEOUT if timed_out else ExitCode.INVALID_IPA
                category = "timeout" if timed_out else "invalid_ipa"
                raise CliError(str(exc), code, category) from exc
            if info.encryption_status == "encrypted":
                raise CliError("IPA's main or nested Mach-O executable is encrypted and is not a supported re-signing input", ExitCode.INVALID_IPA, "encrypted_ipa")
            if info.encryption_status == "unknown":
                raise CliError("could not confirm the IPA executable encryption state; refusing to start a GUI install", ExitCode.PREFLIGHT, "encryption_state_unknown")
            logger.emit("ipa_validated", filename=info.filename, bundle_id=info.bundle_id, version=info.version)

            try:
                before = ui.snapshot()
                if before.running and (contains_busy(before) or contains_auth_prompt(before) or "which team to use" in before.all_text):
                    raise CliError("Sideloadly already appears to be busy; refusing to load another IPA", ExitCode.UNKNOWN, "app_busy")
                launch_app_with_ipa(app_path.resolve(), Path(info.path), _remaining(deadline))
                current = wait_for_running(ui, deadline)
                ui.activate()
            except AutomationError as exc:
                raise _handle_automation_error(exc, logger) from exc

            if contains_auth_prompt(current):
                current = _wait_for_manual_auth(ui, deadline, logger)

            try:
                advanced = node_matches(current, "AXCheckBox", "Advanced Options")
                if len(advanced) == 1 and not is_checked(advanced[0]):
                    ui.click("AXCheckBox", advanced[0].selector)
                    time.sleep(min(0.3, _remaining(deadline)))
                    current = ui.snapshot()
                if not any(node.role == "AXStaticText" and node.value.strip() == info.filename for node in current.nodes):
                    raise AutomationError("ipa_load_unverified", "requested IPA filename is not visible after opening the document", ExitCode.UNSUPPORTED_UI)
                current = _ensure_install_settings(args, ui, current, deadline)
                start = start_button(current)
                logger.emit(
                    "install_preflight_confirmed",
                    filename=info.filename,
                    bundle_id=args.bundle_id,
                    team=args.team,
                    device_selector="<redacted>",
                    account="<redacted>",
                    auto_refresh=True,
                )
                previous = current
                ui.click(start.role, start.name)
                logger.emit("start_clicked", bundle_id=args.bundle_id)
            except AutomationError as exc:
                raise _handle_automation_error(exc, logger) from exc

            return _monitor_install(args, logger, ui, previous, info, deadline)

    except AutomationError as exc:
        raise _handle_automation_error(exc, logger) from exc


def _installed_app_matches(data: dict[str, Any], bundle_id: str, info: IPAInfo) -> bool:
    result = data.get("result", {})
    if not isinstance(result, dict) or not info.version or not info.build:
        return False
    apps = result.get("apps", [])
    if not isinstance(apps, list):
        return False
    matches = [app for app in apps if isinstance(app, dict) and app.get("bundleIdentifier") == bundle_id]
    return len(matches) == 1 and matches[0].get("version") == info.version and matches[0].get("bundleVersion") == info.build


def _verify_installation(args: argparse.Namespace, ui: AppleScriptUI, info: IPAInfo, deadline: float) -> dict[str, Any]:
    candidate = args.bundle_id if args.bundle_id_mode == "exact" else args.bundle_id + "." + args.team
    identity = re.search(r"\b(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}|[0-9A-Fa-f]{40})\b", args.device)
    device_verified = False
    if identity and time.monotonic() < deadline:
        try:
            with tempfile.TemporaryDirectory(prefix="sideloadlycmd-verify-") as temp_name:
                output = Path(temp_name) / "apps.json"
                remaining = min(25.0, _remaining(deadline))
                result = subprocess.run(
                    ["xcrun", "devicectl", "device", "info", "apps", "--device", identity.group(), "--bundle-id", candidate,
                     "--json-output", str(output), "--timeout", str(max(1, int(remaining)))],
                    capture_output=True, text=True, timeout=remaining,
                )
                if result.returncode == 0 and output.is_file():
                    device_verified = _installed_app_matches(json.loads(output.read_text()), candidate, info)
        except (OSError, ValueError, subprocess.TimeoutExpired, CliError):
            pass
    final_id = candidate if device_verified or args.bundle_id_mode == "exact" else None
    refresh_verified = False
    if final_id and time.monotonic() < deadline:
        try:
            refresh_verified = ui.refresh_registration(final_id, args.device)
        except AutomationError:
            pass
    return {"final_bundle_id": final_id, "device_app": "version_and_build_verified" if device_verified else "unverified",
            "auto_refresh": "registered_and_visible" if refresh_verified else "unverified"}


def _run_verify(args: argparse.Namespace, logger: JsonLogger) -> tuple[dict[str, Any], ExitCode]:
    if sys.platform != "darwin":
        raise CliError("device verification requires macOS", ExitCode.PREFLIGHT, "unsupported_platform")
    deadline = time.monotonic() + args.timeout
    try:
        info = inspect_ipa(args.ipa, timeout_seconds=_remaining(deadline))
    except IPAError as exc:
        raise CliError(str(exc), ExitCode.INVALID_IPA, "invalid_ipa") from exc
    ui = AppleScriptUI(timeout_seconds=12)
    ui.set_deadline(deadline)
    verification = _verify_installation(args, ui, info, deadline)
    complete = verification["device_app"] == "version_and_build_verified" and verification["auto_refresh"] == "registered_and_visible"
    result = {"command": "verify", "state": "device_and_refresh_verified" if complete else "verification_incomplete",
              "device": sanitized_device_label(args.device), "ipa": info.filename, "version": info.version, "build": info.build,
              "install": "present_on_device" if verification["device_app"] != "unverified" else "unverified",
              "message": "read-only verification of device app and automatic refresh registration", **verification}
    logger.emit("post_install_verification", **verification)
    return result, ExitCode.OK if complete else ExitCode.PARTIAL


def _monitor_install(args: argparse.Namespace, logger: JsonLogger, ui: AppleScriptUI, current: UISnapshot, info: IPAInfo, deadline: float) -> tuple[dict[str, Any], ExitCode]:
    previous = current
    busy_seen = contains_busy(current)
    team_verified = getattr(args, "_team_preflight_verified", False)
    while time.monotonic() < deadline:
        try:
            current = ui.snapshot()
        except AutomationError as exc:
            raise _handle_automation_error(exc, logger) from exc
        if "which team to use" in current.all_text:
            current = _select_install_team(ui, current, args.team, deadline)
            team_verified = True
            logger.emit("team_selection_confirmed", team=args.team)
        if contains_failure(current):
            logger.emit("install_failed", level="error", bundle_id=args.bundle_id, evidence="failure_text_visible")
            raise CliError("Sideloadly reported an installation or signing error", ExitCode.INSTALL_FAILED, "install_failed")
        if contains_auth_prompt(current):
            logger.emit("user_action_required", level="warning", category="authentication")
            current = _wait_for_manual_auth(ui, deadline, logger)
            if contains_failure(current):
                logger.emit("install_failed", level="error", bundle_id=args.bundle_id, evidence="failure_text_visible")
                raise CliError("Sideloadly reported an installation or signing error", ExitCode.INSTALL_FAILED, "install_failed")
        if contains_busy(current):
            busy_seen = True
        if success_transitioned(previous, current, busy_seen):
            logger.emit(
                "installation_completed",
                bundle_id=args.bundle_id,
                busy_state_seen=busy_seen,
                evidence="new_success_text",
            )
            verification = _verify_installation(args, ui, info, deadline)
            refresh_verified = verification["auto_refresh"] == "registered_and_visible"
            result = {
                "command": "install",
                "state": "installed_refresh_verified" if refresh_verified else "installed_refresh_unverified",
                "ipa": {"filename": info.filename, "app_name": info.app_name, "source_bundle_id": info.bundle_id, "version": info.version, "build": info.build},
                "final_bundle_id": verification["final_bundle_id"],
                "device_app": verification["device_app"],
                "input_bundle_id": args.bundle_id,
                "bundle_id_mode": args.bundle_id_mode,
                "team_id": args.team if team_verified else None,
                "requested_team_id": args.team,
                "team_verification": "confirmed_in_ui" if team_verified else "unverified",
                "device": sanitized_device_label(args.device),
                "account": "<redacted>",
                "install": "completed_this_run",
                "auto_refresh": "registered_and_visible" if refresh_verified else "unverified",
                "message": "Sideloadly showed a fresh completion after this run; refresh registration was " + ("visible" if refresh_verified else "not independently verified"),
            }
            logger.emit("refresh_registration_checked", result=result["auto_refresh"], bundle_id=args.bundle_id)
            return result, ExitCode.OK if refresh_verified else ExitCode.PARTIAL
        previous = current
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    raise CliError("install did not reach an attributable terminal state before the total timeout", ExitCode.UNKNOWN, "result_unknown")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        logger = JsonLogger(args.log_file)
    except OSError:
        print(json.dumps({"timestamp": _timestamp(), "level": "error", "event": "log_file_unavailable", "category": "log_file_unavailable"}, sort_keys=True), file=sys.stderr)
        _emit_result({"command": args.command, "state": "failed", "category": "log_file_unavailable", "exit_code": int(ExitCode.PREFLIGHT), "message": "could not create the requested log file"}, args.json)
        return int(ExitCode.PREFLIGHT)
    def terminate(signum, frame):
        # launchd bootout sends SIGTERM. Raise through native process boundaries
        # so their finally blocks terminate child process groups as well.
        raise KeyboardInterrupt()
    previous_termination = signal.signal(signal.SIGTERM, terminate)
    from .progress import Progress, stage
    progress = Progress(logger.emit, args.command)
    try:
        progress.start()
        if args.command == "background-run":
            from .background_run import run
            result, code = run(args.native_args,args.timeout)
            logger.emit("background_command_finished",state=result["state"],exit_code=int(code))
        elif args.command == "native-doctor":
            from .native_doctor import diagnose
            result, code = diagnose(team=args.team,account=args.account,device=args.device,offline=args.offline,timeout=args.timeout)
            logger.emit("native_prerequisites_checked",state=result["state"])
        elif args.command == "teams":
            from .teams import list_teams
            result, code = {"command":"teams","state":"saved_teams_read","teams":list_teams(args.account)}, ExitCode.OK
        elif args.command == "sideload":
            from .workflow import sideload
            deadline=time.monotonic()+args.timeout
            stage("install_lock", "Waiting for the per-user installation lock")
            with _install_mutex(deadline):
                result, code=sideload(args.ipa,args.device,args.team,args.account,args.output,args.bundle_id,
                    args.bundle_id_mode,args.strip_watch,args.offline,_remaining(deadline),args.dry_run,args.strip_extension,args.strict_components)
            logger.emit("sideload_checked",state=result["state"],exit_code=int(code))
        elif args.command == "headless-doctor":
            from .headless import doctor
            result, code = doctor(args.backend, args.timeout), ExitCode.OK
            logger.emit("native_preflight_complete", state=result["state"])
        elif args.command == "login":
            if not args.experimental_auth:
                raise CliError("Apple ID sign-in is managed by Xcode Settings > Accounts; standalone research login requires explicit --experimental-auth",
                               ExitCode.USAGE, "research_auth_disabled")
            from .headless import login
            logger.emit("terminal_authentication_started", account="<redacted>")
            result, code = login(args.backend, args.state_dir, args.timeout), ExitCode.OK
            logger.emit("terminal_authentication_completed", account="<redacted>")
        elif args.command == "headless-plan":
            from .headless import install_plan
            result = install_plan(args.backend, args.state_dir, args.ipa, args.device, args.account,
                                  args.team, args.bundle_id, args.bundle_id_mode, args.timeout)
            code = ExitCode.OK
            logger.emit("native_install_plan_checked", team=args.team, bundle_id=args.bundle_id, account="<redacted>")
        elif args.command == "prepare":
            from .xcode_backend import prepare
            logger.emit("native_preparation_started", team=args.team, bundle_id=args.bundle_id, account="<redacted>")
            try:
                result = prepare(args.ipa, args.output, args.account, args.team, args.bundle_id,
                                 args.bundle_id_mode, args.device, args.strip_watch, args.timeout, offline=args.offline,strip_extensions=args.strip_extension,strict_components=args.strict_components)
            except OSError as exc:
                raise CliError("native preparation could not read or write its files", ExitCode.PREFLIGHT, "prepare_io_failed") from exc
            except ValueError as exc:
                raise CliError("native preparation encountered invalid app metadata", ExitCode.INVALID_IPA, "invalid_ipa") from exc
            for warning in result.get("warnings",[]): logger.emit("optional_component_removed",level="warning",message=warning)
            code = ExitCode.OK
            logger.emit("native_signature_verified", team=args.team, bundle_id=result["final_bundle_id"])
        elif args.command in {"install-prepared", "verify-prepared"}:
            from .xcode_backend import install_prepared
            logger.emit("native_verification_started" if args.command=="verify-prepared" else "native_install_started", team=args.team, bundle_id=args.bundle_id, device="<redacted>")
            deadline = time.monotonic() + args.timeout
            with _install_mutex(deadline):
                result = install_prepared(args.prepared, args.device, args.team, args.bundle_id, _remaining(deadline), verify_only=args.command=="verify-prepared")
            code = ExitCode(result.get("exit_code", ExitCode.OK if result["device_app"] == "version_and_build_verified" else ExitCode.PARTIAL))
            logger.emit("native_install_checked", bundle_id=args.bundle_id, device_app=result["device_app"], refresh=result["refresh"])
        elif args.command == "refresh-register":
            from .refresh import register
            deadline = time.monotonic() + args.timeout
            with _install_mutex(deadline):
                result = register(args.state_dir, args.prepared, args.source_ipa, args.device, args.account, _remaining(deadline))
            code = ExitCode.OK
            logger.emit("native_refresh_registered", job_id=result["job_id"], bundle_id=result["final_bundle_id"])
        elif args.command == "refresh-status":
            from .refresh import status
            result, code = status(args.state_dir), ExitCode.OK
        elif args.command == "refresh-native":
            from .refresh import run
            deadline = time.monotonic() + args.timeout
            with _install_mutex(deadline):
                result, code = run(args.state_dir, args.job, args.force, _remaining(deadline), account=args.account)
            logger.emit("native_refresh_checked", results=result["jobs"])
        elif args.command == "refresh-scheduler":
            from .scheduler import manage
            launcher = Path(__file__).resolve().parents[2] / "scripts/sideloadlycmd"
            result = manage(args.action, args.state_dir, launcher, args.timeout, persist=args.persist)
            code = ExitCode.OK
        elif args.command == "background-keychain":
            from .background_keychain import manage
            result = manage(args.action, args.timeout)
            code = ExitCode.OK
        elif args.command == "doctor":
            result, code = _run_doctor(args, logger)
        elif args.command == "inspect":
            result, code = _run_inspect(args, logger)
        elif args.command == "status":
            result, code = _run_status(args, logger)
        elif args.command == "install":
            result, code = _run_install(args, logger)
        elif args.command == "verify":
            result, code = _run_verify(args, logger)
        else:
            raise CliError("unknown command", ExitCode.USAGE, "usage")
        _emit_result(result, args.json)
        return int(code)
    except CliError as exc:
        logger.emit("command_failed", level="error", category=exc.category, exit_code=int(exc.code), message=exc.message)
        result = {"command": args.command, "state": "failed", "category": exc.category, "exit_code": int(exc.code), "message": exc.message}
        _emit_result(result, args.json)
        return int(exc.code)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if args.command not in {"sideload", "teams", "background-run", "prepare", "install-prepared", "verify-prepared", "refresh-register", "refresh-status", "refresh-native", "refresh-scheduler", "background-keychain", "native-doctor"}:
            raise
        message = "native command could not read or validate its private state; no sensitive values were logged"
        logger.emit("command_failed", level="error", category="native_state_invalid", exit_code=int(ExitCode.PREFLIGHT), message=message)
        _emit_result({"command": args.command, "state": "failed", "category": "native_state_invalid", "exit_code": int(ExitCode.PREFLIGHT), "message": message}, args.json)
        return int(ExitCode.PREFLIGHT)
    except KeyboardInterrupt:
        logger.emit("command_interrupted", level="warning", category="cancelled")
        _emit_result({"command": args.command, "state": "cancelled", "category": "cancelled", "exit_code": int(ExitCode.NEEDS_USER), "message": "cancelled by user"}, args.json)
        return int(ExitCode.NEEDS_USER)
    finally:
        progress.close()
        signal.signal(signal.SIGTERM, previous_termination)
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())

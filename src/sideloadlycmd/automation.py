from __future__ import annotations

import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .errors import CliError, ExitCode


APP_BUNDLE_ID = "io.sideloadly.sideloadly"
DEFAULT_APP = Path("/Applications/Sideloadly.app")
SCRIPT_PATH = Path(__file__).with_name("sideloadly.applescript")


class AutomationError(Exception):
    def __init__(self, category: str, message: str, code: ExitCode = ExitCode.AUTOMATION):
        super().__init__(message)
        self.category = category
        self.code = code


@dataclass(frozen=True)
class UINode:
    depth: int
    role: str
    subrole: str
    name: str
    value: str
    description: str = ""

    @property
    def selector(self) -> str:
        return self.name if self.name not in {"", "missing value"} else self.description

    @property
    def text(self) -> str:
        return f"{self.name} {self.value}".strip()


@dataclass(frozen=True)
class UISnapshot:
    running: bool
    windows: tuple[str, ...]
    nodes: tuple[UINode, ...]

    @property
    def all_text(self) -> str:
        return "\n".join([*self.windows, *(node.text for node in self.nodes)]).lower()


class AppleScriptUI:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout_seconds = timeout_seconds
        self.deadline: float | None = None

    def set_deadline(self, deadline: float) -> None:
        self.deadline = deadline

    def call(self, action: str, *args: str, timeout_seconds: float | None = None) -> str:
        if not SCRIPT_PATH.is_file():
            raise AutomationError("script_missing", "bundled AppleScript automation script is missing")
        request_timeout = timeout_seconds or self.timeout_seconds
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise AutomationError("automation_timeout", "total install timeout expired", ExitCode.TIMEOUT)
            request_timeout = min(request_timeout, remaining)
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", str(SCRIPT_PATH), action, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=request_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise AutomationError("automation_timeout", "AppleScript UI query timed out", ExitCode.TIMEOUT) from exc
        except OSError as exc:
            raise AutomationError("osascript_unavailable", "AppleScript runtime could not be started") from exc

        output = result.stdout.strip()
        if result.returncode != 0:
            diagnostic = result.stderr.lower()
            if "-1719" in diagnostic or "-1743" in diagnostic or "not authorized" in diagnostic:
                raise AutomationError("permission_denied", "macOS denied Accessibility or Automation access")
            if "-10827" in diagnostic or "sysmon" in diagnostic or "process list" in diagnostic:
                raise AutomationError("process_access_unavailable", "System Events could not inspect application processes; check the desktop execution sandbox as well as macOS access")
            raise AutomationError("applescript_error", "AppleScript could not inspect the Sideloadly UI")
        return output

    def snapshot(self) -> UISnapshot:
        output = self.call("snapshot")
        lines = output.splitlines()
        if not lines or lines[0] == "APP¦NOT_RUNNING":
            return UISnapshot(False, (), ())
        if lines[0] != "APP¦RUNNING":
            raise AutomationError("invalid_ui_response", "Sideloadly returned an unexpected UI response")
        windows: list[str] = []
        nodes: list[UINode] = []
        for line in lines[1:]:
            parts = line.split("¦", 6)
            if parts[0] == "WINDOW" and len(parts) == 3:
                windows.append(parts[2])
            elif parts[0] == "NODE" and len(parts) in {6, 7}:
                try:
                    depth = int(parts[1])
                except ValueError:
                    continue
                nodes.append(UINode(depth, parts[2], parts[3], parts[4], parts[5], parts[6] if len(parts) == 7 else ""))
        return UISnapshot(True, tuple(windows), tuple(nodes))

    def refresh_registration(self, bundle_id: str, device: str) -> bool:
        output = self.call("refresh-list")
        lines = output.splitlines()
        if not lines or lines[0] != "REFRESH¦OK":
            return False
        return refresh_rows_match([line[5:] for line in lines[1:] if line.startswith("ITEM¦")], bundle_id, device)

    def activate(self) -> None:
        output = self.call("activate")
        if output != "ACTION¦OK":
            raise AutomationError("activation_failed", "Sideloadly could not be brought to the foreground")

    def click(self, role: str, name: str) -> None:
        self._check_action(self.call("click", role, name))

    def click_value(self, role: str, value: str) -> None:
        self._check_action(self.call("click-value", role, value))

    def set_value(self, role: str, name: str, value: str) -> None:
        self._check_action(self.call("set", role, name, value))

    @staticmethod
    def _check_action(output: str) -> None:
        if output == "ACTION¦OK":
            return
        if output == "ACTION¦AMBIGUOUS":
            raise AutomationError("ambiguous_control", "Accessibility matched more than one control; no action was taken", ExitCode.UNSUPPORTED_UI)
        if output == "ACTION¦NOT_FOUND":
            raise AutomationError("control_not_found", "required Accessibility control was not found", ExitCode.UNSUPPORTED_UI)
        if output == "ACTION¦SECURE_FIELD_BLOCKED":
            raise AutomationError("secure_field_blocked", "automation is not allowed to write to a secure field", ExitCode.UNSUPPORTED_UI)
        raise AutomationError("control_action_failed", "Sideloadly rejected an Accessibility action", ExitCode.UNSUPPORTED_UI)


def app_metadata(app_path: Path) -> tuple[str | None, str | None]:
    info_path = app_path / "Contents" / "Info.plist"
    if not app_path.is_dir() or not info_path.is_file():
        return None, None
    try:
        import plistlib

        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        bundle_id = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
        version = info.get("CFBundleShortVersionString") if isinstance(info, dict) else None
        executable = info.get("CFBundleExecutable") if isinstance(info, dict) else None
        if (
            not isinstance(info, dict)
            or info.get("CFBundlePackageType") != "APPL"
            or not isinstance(executable, str)
            or not executable
            or Path(executable).name != executable
        ):
            return None, None
        executable_path = app_path / "Contents" / "MacOS" / executable
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            return None, None
        return (version if isinstance(version, str) else None, bundle_id if isinstance(bundle_id, str) else None)
    except (OSError, ValueError):
        return None, None


def launch_app_with_ipa(app_path: Path, ipa_path: Path, timeout_seconds: float) -> None:
    try:
        result = subprocess.run(
            ["/usr/bin/open", "-a", str(app_path), str(ipa_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=max(0.01, timeout_seconds),
        )
    except subprocess.TimeoutExpired as exc:
        raise AutomationError("launch_timeout", "Sideloadly launch request timed out", ExitCode.TIMEOUT) from exc
    except OSError as exc:
        raise AutomationError("launch_failed", "macOS could not open Sideloadly") from exc
    if result.returncode != 0:
        diagnostic = (result.stdout + "\n" + result.stderr).casefold()
        if "klsnoexecutableerr" in diagnostic or "-10827" in diagnostic:
            raise AutomationError(
                "launch_unavailable",
                "Launch Services returned kLSNoExecutableErr; the Sideloadly bundle exists but macOS does not consider it launchable",
                ExitCode.APP_MISSING,
            )
        raise AutomationError("launch_failed", "macOS refused to open Sideloadly or the IPA document", ExitCode.APP_MISSING)


def wait_for_running(ui: AppleScriptUI, deadline: float, poll_seconds: float = 0.5) -> UISnapshot:
    last_error: AutomationError | None = None
    while time.monotonic() < deadline:
        try:
            snapshot = ui.snapshot()
            if snapshot.running:
                return snapshot
        except AutomationError as exc:
            last_error = exc
            if exc.category in {"permission_denied", "process_access_unavailable", "osascript_unavailable"}:
                raise
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
    if last_error:
        raise last_error
    raise AutomationError("app_not_running", "Sideloadly did not expose a window before the timeout", ExitCode.APP_MISSING)


def node_matches(snapshot: UISnapshot, role: str, phrase: str) -> list[UINode]:
    needle = phrase.casefold()
    return [node for node in snapshot.nodes if node.role == role and needle in (node.name + " " + node.description).casefold()]


def exact_value_matches(snapshot: UISnapshot, value: str) -> list[UINode]:
    target = value.casefold()
    return [node for node in snapshot.nodes if node.value.strip().casefold() == target or node.name.strip().casefold() == target]


def selected_control_matches(snapshot: UISnapshot, value: str, roles: set[str]) -> list[UINode]:
    target = value.strip().casefold()
    matches = []
    for node in snapshot.nodes:
        if node.role not in roles:
            continue
        candidates = [node.value.strip().casefold()]
        if node.role in {"AXPopUpButton", "AXComboBox"}:
            candidates.append(node.name.strip().casefold())
        if any(candidate == target or ("@" in target and candidate.startswith(target + " [")) for candidate in candidates):
            matches.append(node)
    # Qt exposes the selected combo value again through its editable child.
    combos = [node for node in matches if node.role in {"AXPopUpButton", "AXComboBox"}]
    return combos if combos else matches


def require_visible_identity(snapshot: UISnapshot, value: str, label: str, roles: set[str]) -> UINode:
    matches = selected_control_matches(snapshot, value, roles)
    if len(matches) != 1:
        raise AutomationError(
            "identity_not_confirmable",
            f"could not uniquely confirm the requested {label} in Sideloadly's Accessibility UI",
            ExitCode.UNSUPPORTED_UI,
        )
    return matches[0]


def is_checked(node: UINode) -> bool:
    return node.value.strip().casefold() in {"1", "true", "checked", "on", "yes"}


def contains_auth_prompt(snapshot: UISnapshot) -> bool:
    if any(node.role in {"AXSecureTextField", "AXPasswordField"} for node in snapshot.nodes):
        return True
    terms = ("verification code", "two-factor", "enter password", "sign in to", "验证码", "输入密码", "双重认证")
    return any(term in text for term in terms for text in (snapshot.all_text,))


def contains_failure(snapshot: UISnapshot) -> bool:
    terms = ("installation failed", "signing failed", "error:", "failed to install", "安装失败", "签名失败")
    return any(term in snapshot.all_text for term in terms)


def contains_busy(snapshot: UISnapshot) -> bool:
    terms = ("signing", "installing", "uploading", "processing ipa", "preparing", "正在签名", "正在安装", "处理中")
    return any(term in snapshot.all_text for term in terms)


def contains_success(snapshot: UISnapshot) -> bool:
    terms = ("installation complete", "installed successfully", "successfully installed", "done", "安装完成", "安装成功")
    return any(term in snapshot.all_text for term in terms)


def success_transitioned(previous: UISnapshot, current: UISnapshot, busy_state_seen: bool = False) -> bool:
    """Return true for new explicit success text, or a new Done marker after observed work."""
    if not contains_success(current):
        return False
    before = _success_evidence(previous)
    after = _success_evidence(current)
    explicit_terms = {"installation complete", "installed successfully", "successfully installed", "安装完成", "安装成功"}
    for marker, count in after.items():
        if count <= before[marker]:
            continue
        if marker[-1] in explicit_terms or (marker[-1] == "done" and busy_state_seen):
            return True
    return False


def _success_evidence(snapshot: UISnapshot) -> Counter[tuple[str, ...]]:
    terms = ("installation complete", "installed successfully", "successfully installed", "done", "安装完成", "安装成功")
    evidence: Counter[tuple[str, ...]] = Counter()
    for text in [*snapshot.windows, *(node.text for node in snapshot.nodes)]:
        folded = text.casefold()
        for term in terms:
            pattern = r"\bdone\b" if term == "done" else re.escape(term)
            evidence[(term,)] += len(re.findall(pattern, folded))
    return evidence



def refresh_registration_visible(snapshot: UISnapshot, bundle_id: str, device: str) -> bool:
    context = any("current install" in title.casefold() or "refresh" in title.casefold() for title in snapshot.windows)
    if not context:
        return False
    lowered = snapshot.all_text
    return bundle_id.casefold() in lowered and device.casefold() in lowered


def refresh_rows_match(rows: list[str], bundle_id: str, device: str) -> bool:
    friendly = re.split(r" \([^)]*\) (?=[0-9A-Fa-f]{8}-)", device, maxsplit=1)[0].strip()
    require_usb = "@USB" in device
    matching_headers = 0
    target_section = False
    found = False
    for row in rows:
        header = re.fullmatch(r"Device: (.+?)(?: \[([^]]+)\])?:", row)
        if header:
            flags = (header.group(2) or "").casefold()
            target_section = header.group(1) == friendly and (not require_usb or ("online" in flags and "usb" in flags))
            matching_headers += int(target_section)
        elif target_section and f"[{bundle_id}]" in row:
            found = True
    return found and matching_headers == 1


def bundle_id_control(snapshot: UISnapshot) -> list[UINode]:
    return [
        node for node in snapshot.nodes
        if node.role == "AXTextField"
        and any(token in (node.name + " " + node.description).casefold() for token in ("bundle", "identifier", "应用 id", "应用标识"))
    ]


def start_button(snapshot: UISnapshot) -> UINode:
    labels = {"start", "install", "sideload", "开始", "安装"}
    found = [node for node in snapshot.nodes if node.role == "AXButton" and node.name.strip().casefold() in labels]
    if len(found) != 1:
        raise AutomationError("start_button_unconfirmed", "could not uniquely identify Sideloadly's Start button", ExitCode.UNSUPPORTED_UI)
    return found[0]


def sanitized_snapshot_summary(snapshot: UISnapshot) -> dict[str, object]:
    # Never expose raw field values: they may contain an Apple ID or other account data.
    return {
        "running": snapshot.running,
        "window_count": len(snapshot.windows),
        "roles": sorted({node.role for node in snapshot.nodes if node.role}),
        "secure_fields_present": any(node.role in {"AXSecureTextField", "AXPasswordField"} for node in snapshot.nodes),
        "has_refresh_control": bool(node_matches(snapshot, "AXCheckBox", "refresh")),
        "has_bundle_id_field": bool(bundle_id_control(snapshot)),
        "has_start_button": any(node.role == "AXButton" and node.name.strip().casefold() in {"start", "install", "sideload", "开始", "安装"} for node in snapshot.nodes),
        "has_auth_prompt": contains_auth_prompt(snapshot),
        "has_failure_text": contains_failure(snapshot),
        "has_busy_text": contains_busy(snapshot),
        "has_success_text": contains_success(snapshot),
        "ui_tree_truncated": len(snapshot.nodes) >= 320,
    }


def redact_diagnostic(text: str) -> str:
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "<redacted-email>", text)
    text = re.sub(r"\b(?:[0-9A-Fa-f]{8}-?){2,}[0-9A-Fa-f]{4,}\b", "<redacted-device-id>", text)
    return text


def sanitized_device_label(value: str) -> str:
    return re.sub(
        r"\b(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{24}|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16,}|(?:[0-9A-Fa-f]{8}-){3}[0-9A-Fa-f]{12})\b",
        "<redacted-device-id>",
        value,
    )[:100]

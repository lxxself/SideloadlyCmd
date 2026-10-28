from __future__ import annotations

import unittest
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sideloadlycmd.automation import UISnapshot, UINode, contains_auth_prompt, refresh_registration_visible, selected_control_matches, sanitized_device_label, success_transitioned, node_matches, bundle_id_control, refresh_rows_match
from sideloadlycmd.cli import _redact, _select_install_team, _installed_app_matches, _verify_installation


class CLISafetyTests(unittest.TestCase):
    def test_sideload_defaults_to_json_including_failures(self):
        import io
        from contextlib import redirect_stdout, nullcontext
        from sideloadlycmd.cli import build_parser, main
        from sideloadlycmd.errors import CliError, ExitCode
        self.assertTrue(build_parser().parse_args(["sideload", "App.ipa"]).json)
        self.assertTrue(build_parser().parse_args(["sideload", "App.ipa", "--json"]).json)
        output = io.StringIO()
        with patch("sideloadlycmd.cli._install_mutex", return_value=nullcontext()), patch("sideloadlycmd.workflow.sideload", side_effect=CliError("missing device", ExitCode.PREFLIGHT, "device_not_found")), redirect_stdout(output):
            code = main(["sideload", "App.ipa"])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["category"], "device_not_found")

    def test_redaction_removes_email_and_long_device_identifier(self) -> None:
        result = _redact({"account": "owner@example.com", "message": "owner@example.com on 00008020-001C2D1234567890"})
        self.assertEqual(result["account"], "<redacted>")
        self.assertNotIn("owner@example.com", result["message"])
        self.assertNotIn("00008020-001C2D1234567890", result["message"])

    def test_authentication_takes_precedence_signal(self) -> None:
        snapshot = UISnapshot(True, ("Sign In",), (UINode(2, "AXSecureTextField", "", "Password", ""),))
        self.assertTrue(contains_auth_prompt(snapshot))

    def test_refresh_registration_requires_current_installs_context(self) -> None:
        nodes = (
            UINode(1, "AXStaticText", "", "Bundle ID", "com.example.app"),
            UINode(1, "AXStaticText", "", "Device", "iPhone 15"),
        )
        self.assertFalse(refresh_registration_visible(UISnapshot(True, ("Sideloadly",), nodes), "com.example.app", "iPhone 15"))
        self.assertTrue(refresh_registration_visible(UISnapshot(True, ("Current Installs",), nodes), "com.example.app", "iPhone 15"))

    def test_device_label_is_human_readable_but_hides_udid(self) -> None:
        self.assertEqual(sanitized_device_label("iPhone 15"), "iPhone 15")
        self.assertEqual(sanitized_device_label("00008020-001C2D1234567890"), "<redacted-device-id>")

    def test_visible_static_text_does_not_confirm_a_selected_device(self) -> None:
        snapshot = UISnapshot(True, ("Sideloadly",), (UINode(1, "AXStaticText", "", "Device", "iPhone 15"),))
        self.assertEqual(selected_control_matches(snapshot, "iPhone 15", {"AXPopUpButton", "AXComboBox"}), [])

    def test_explicit_new_success_text_does_not_require_busy_state(self) -> None:
        previous = UISnapshot(True, ("Sideloadly",), ())
        completed = UISnapshot(True, ("Sideloadly",), (UINode(1, "AXStaticText", "", "Status", "Installation complete"),))
        self.assertTrue(success_transitioned(previous, completed))

    def test_generic_done_requires_observed_busy_state(self) -> None:
        previous = UISnapshot(True, ("Sideloadly",), ())
        done = UISnapshot(True, ("Sideloadly",), (UINode(1, "AXButton", "", "Done", ""),))
        self.assertFalse(success_transitioned(previous, done))
        self.assertTrue(success_transitioned(previous, done, busy_state_seen=True))

    def test_preexisting_done_text_is_not_a_new_completion(self) -> None:
        done = UISnapshot(True, ("Sideloadly",), (UINode(1, "AXButton", "", "Done", ""),))
        self.assertFalse(success_transitioned(done, done, busy_state_seen=True))

    def test_qt_description_exposes_refresh_and_bundle_controls(self) -> None:
        snapshot = UISnapshot(True, ("Sideloadly",), (
            UINode(1, "AXCheckBox", "", "missing value", "1", "AutoRefresh [enabled]"),
            UINode(1, "AXTextField", "", "missing value", "com.example.app", "Enter the desired Bundle ID"),
        ))
        self.assertEqual(len(node_matches(snapshot, "AXCheckBox", "refresh")), 1)
        self.assertEqual(bundle_id_control(snapshot)[0].selector, "Enter the desired Bundle ID")

    def test_account_combo_child_is_not_a_second_account(self) -> None:
        label = "owner@example.com [8 App IDs Remaining, updates 6d]"
        snapshot = UISnapshot(True, (), (
            UINode(1, "AXComboBox", "", label, "missing value"),
            UINode(2, "AXTextField", "", "missing value", label),
        ))
        matched = selected_control_matches(snapshot, "owner@example.com", {"AXComboBox", "AXTextField"})
        self.assertEqual([node.role for node in matched], ["AXComboBox"])

    def test_appending_progress_to_old_done_log_is_not_success(self) -> None:
        before = UISnapshot(True, (), (UINode(1, "AXStaticText", "", "", "Done. Starting next task"),))
        after = UISnapshot(True, (), (UINode(1, "AXStaticText", "", "", "Done. Starting next task Signing..."),))
        self.assertFalse(success_transitioned(before, after, busy_state_seen=True))

    def test_collapsed_team_combo_confirms_remembered_team(self) -> None:
        snapshot = UISnapshot(True, ("Select",), (
            UINode(1, "AXComboBox", "", "ABCDEFGHIJ: Example (Individual)", "missing value"),
            UINode(2, "AXButton", "", "OK", "missing value"),
        ))
        ui = Mock()
        ui.snapshot.return_value = UISnapshot(True, ("Sideloadly",), ())
        with patch("sideloadlycmd.cli.time.sleep"):
            _select_install_team(ui, snapshot, "ABCDEFGHIJ", time.monotonic() + 10)
        ui.click.assert_called_once_with("AXButton", "OK")

    def test_refresh_entry_must_be_in_target_usb_device_section(self) -> None:
        device = "Test Phone (27.0) 00008140-00020DD60C4A001E @USB"
        rows = ["Device: Test Phone [online,usb]:", "Other.ipa [com.other.app] 6 days left",
                "Device: Another Phone:", "Test.ipa [com.example.app.ABCDEFGHIJ] 6 days left"]
        self.assertFalse(refresh_rows_match(rows, "com.example.app.ABCDEFGHIJ", device))
        rows.insert(1, "Test.ipa [com.example.app.ABCDEFGHIJ] 6 days left")
        self.assertTrue(refresh_rows_match(rows, "com.example.app.ABCDEFGHIJ", device))
        rows.extend(["Device: Test Phone [online,usb]:", "Test.ipa [com.example.app.ABCDEFGHIJ] 6 days left"])
        self.assertFalse(refresh_rows_match(rows, "com.example.app.ABCDEFGHIJ", device))

    def test_installed_app_verification_rejects_old_build(self) -> None:
        info = SimpleNamespace(version="5.4.0", build="2026101811000")
        app = {"bundleIdentifier": "com.example.app", "version": "5.4.0", "bundleVersion": "older"}
        self.assertFalse(_installed_app_matches({"result": {"apps": [app]}}, "com.example.app", info))
        app["bundleVersion"] = info.build
        self.assertTrue(_installed_app_matches({"result": {"apps": [app]}}, "com.example.app", info))
        self.assertFalse(_installed_app_matches({"result": {"apps": [app, app]}}, "com.example.app", info))

    def test_device_query_uses_integer_timeout_and_checks_generated_id(self) -> None:
        args = SimpleNamespace(device="Test Phone (27.0) 00008140-00020DD60C4A001E @USB", bundle_id="com.example.app", team="ABCDEFGHIJ", bundle_id_mode="automatic")
        info = SimpleNamespace(version="5.4.0", build="2026101811000")
        ui = Mock()
        ui.refresh_registration.return_value = True
        def query(argv, **kwargs):
            self.assertTrue(argv[argv.index("--timeout") + 1].isdigit())
            bundle = argv[argv.index("--bundle-id") + 1]
            self.assertEqual(bundle, "com.example.app.ABCDEFGHIJ")
            Path(argv[argv.index("--json-output") + 1]).write_text(json.dumps({"result": {"apps": [
                {"bundleIdentifier": bundle, "version": info.version, "bundleVersion": info.build}
            ]}}))
            return subprocess.CompletedProcess(argv, 0, "", "")
        with patch("sideloadlycmd.cli.subprocess.run", side_effect=query):
            result = _verify_installation(args, ui, info, time.monotonic() + 30)
        self.assertEqual(result["final_bundle_id"], "com.example.app.ABCDEFGHIJ")
        self.assertEqual(result["device_app"], "version_and_build_verified")
        self.assertEqual(result["auto_refresh"], "registered_and_visible")


if __name__ == "__main__":
    unittest.main()

class XcodeManagedLoginContractTests(unittest.TestCase):
    def test_ordinary_login_never_prompts_for_standalone_apple_id(self):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        from sideloadlycmd.cli import main
        with patch('sideloadlycmd.headless.login') as login, redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
            code=main(['login','--backend','/missing-research-backend','--json'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.getvalue())['category'],'research_auth_disabled')
        login.assert_not_called()

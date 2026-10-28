from __future__ import annotations

import os
import posixpath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class IPAError(ValueError):
    pass


@dataclass(frozen=True)
class IPAInfo:
    path: str
    filename: str
    app_name: str | None
    bundle_id: str
    version: str | None
    build: str | None
    minimum_os: str | None
    platform: str
    app_directory: str
    original_signature: str
    original_team_id: str | None
    embedded_profile: str
    profile_team_id: str | None
    profile_expires: str | None
    provisioned_device_count: int | None
    encryption_status: str

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("path", None)
        return value


def _validate_member_name(name: str) -> PurePosixPath:
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise IPAError("IPA contains an absolute or platform-specific archive path")
    path = PurePosixPath(name)
    if ".." in path.parts:
        raise IPAError("IPA contains a path traversal entry")
    return path


def _validate_symlink_target(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    member: PurePosixPath,
    safe_root: PurePosixPath | None = None,
) -> None:
    if info.file_size > 4096:
        raise IPAError("IPA symbolic link target is unexpectedly large")
    if info.flag_bits & 0x1:
        raise IPAError("encrypted IPA symbolic links are not supported")
    try:
        raw_target = zf.read(info)
        target = raw_target.decode("utf-8")
    except (UnicodeDecodeError, RuntimeError, zipfile.BadZipFile) as exc:
        raise IPAError("IPA contains an unreadable symbolic link") from exc
    if not target or "\\" in target or target.startswith("/") or re.match(r"^[A-Za-z]:", target):
        raise IPAError("IPA contains an absolute symbolic link")
    if safe_root is not None:
        if member.parts[: len(safe_root.parts)] != safe_root.parts:
            raise IPAError("IPA symbolic link is outside the app extraction root")
        relative_member = PurePosixPath(*member.parts[len(safe_root.parts) :])
        parent = str(relative_member.parent)
    else:
        parent = str(member.parent)
    resolved = posixpath.normpath(posixpath.join(parent, target))
    if resolved == ".." or resolved.startswith("../"):
        raise IPAError("IPA contains a symbolic link that escapes the archive root")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IPAError("IPA inspection exceeded its time limit")
    return remaining


def _safe_extract_app(zf: zipfile.ZipFile, app_directory: str, destination: Path, deadline: float) -> Path:
    prefix = f"Payload/{app_directory}/"
    app_path = destination / app_directory
    entries: list[tuple[zipfile.ZipInfo, PurePosixPath, bool]] = []
    seen: set[str] = set()
    for info in zf.infolist():
        name = info.filename.rstrip("/")
        if name == f"Payload/{app_directory}":
            relative = PurePosixPath(".")
        elif info.filename.startswith(prefix):
            member = _validate_member_name(info.filename)
            relative = PurePosixPath(*member.parts[2:])
        else:
            continue
        if relative.as_posix() == ".":
            continue
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise IPAError("IPA contains duplicate app archive paths")
        seen.add(relative_text)
        mode = (info.external_attr >> 16) & 0xFFFF
        entries.append((info, relative, stat.S_ISLNK(mode)))

    app_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    symlink_entries: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info, relative, is_symlink in entries:
        if time.monotonic() > deadline:
            raise IPAError("IPA extraction exceeded its time limit")
        target = app_path.joinpath(*relative.parts)
        if is_symlink:
            symlink_entries.append((info, relative))
            continue
        mode = (info.external_attr >> 16) & 0xFFFF
        if info.is_dir() or stat.S_ISDIR(mode):
            target.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            raise IPAError("IPA contains a duplicate or colliding app path")
        with zf.open(info) as source, target.open("xb") as output:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                if time.monotonic() > deadline:
                    raise IPAError("IPA extraction exceeded its time limit")
        permissions = mode & 0o777
        os.chmod(target, permissions if permissions else 0o600)

    for info, relative in symlink_entries:
        if time.monotonic() > deadline:
            raise IPAError("IPA extraction exceeded its time limit")
        target_path = app_path.joinpath(*relative.parts)
        current = app_path
        for part in relative.parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise IPAError("IPA symbolic link has a symbolic-link parent")
        target_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target_path.exists() or target_path.is_symlink():
            raise IPAError("IPA contains a duplicate or colliding symbolic link")
        raw_target = zf.read(info)
        try:
            link_target = raw_target.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IPAError("IPA contains a symbolic link with an invalid target") from exc
        _validate_symlink_target(
            zf,
            info,
            PurePosixPath(f"Payload/{app_directory}") / relative,
            safe_root=PurePosixPath("Payload") / app_directory,
        )
        os.symlink(link_target, target_path)
    return app_path


def _inspect_embedded_state(path: Path, app_directory: str, deadline: float) -> tuple[str, str | None, str, str | None, str | None, int | None]:
    unavailable = ("not_checked", None, "not_checked", None, None, None)
    codesign = shutil.which("codesign")
    security = shutil.which("security")
    if sys.platform != "darwin" or not codesign:
        return unavailable

    signature_status = "unavailable"
    team_id: str | None = None
    profile_status = "missing"
    profile_team: str | None = None
    profile_expires: str | None = None
    device_count: int | None = None

    try:
        with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory(prefix="sideloadlycmd-") as temp_name:
            app_path = _safe_extract_app(zf, app_directory, Path(temp_name), deadline)
            verify = subprocess.run(
                [codesign, "--verify", "--deep", "--strict", str(app_path)],
                capture_output=True,
                text=True,
                timeout=_remaining(deadline),
            )
            signature_status = "valid" if verify.returncode == 0 else "invalid_or_unverifiable"
            describe = subprocess.run(
                [codesign, "-dv", "--verbose=2", str(app_path)],
                capture_output=True,
                text=True,
                timeout=_remaining(deadline),
            )
            signature_text = describe.stdout + "\n" + describe.stderr
            if "not signed at all" in signature_text.lower() or "not signed at all" in (verify.stderr or "").lower():
                signature_status = "unsigned"
            team_match = re.search(r"^TeamIdentifier=([^\r\n]+)", signature_text, re.MULTILINE)
            if team_match and team_match.group(1) != "not set":
                team_id = team_match.group(1).strip()

            profile_path = f"Payload/{app_directory}/embedded.mobileprovision"
            try:
                profile_info = zf.getinfo(profile_path)
                if profile_info.file_size > 16 * 1024 * 1024:
                    profile_bytes = None
                    profile_status = "invalid_or_unverifiable"
                else:
                    profile_bytes = zf.read(profile_info)
            except KeyError:
                profile_bytes = None
            if profile_bytes is not None:
                profile_status = "unavailable"
                if security:
                    cms_path = Path(temp_name) / "embedded.mobileprovision"
                    cms_path.write_bytes(profile_bytes)
                    decoded = subprocess.run(
                        [security, "cms", "-D", "-i", str(cms_path)],
                        capture_output=True,
                        timeout=_remaining(deadline),
                    )
                    if decoded.returncode == 0:
                        try:
                            import plistlib

                            profile = plistlib.loads(decoded.stdout)
                        except (ValueError, plistlib.InvalidFileException):
                            profile = None
                        if isinstance(profile, dict):
                            profile_status = "decoded"
                            teams = profile.get("TeamIdentifier")
                            if isinstance(teams, list) and teams and isinstance(teams[0], str):
                                profile_team = teams[0]
                            expiration = profile.get("ExpirationDate")
                            if isinstance(expiration, datetime):
                                if expiration.tzinfo is None:
                                    expiration = expiration.replace(tzinfo=timezone.utc)
                                profile_expires = expiration.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                            devices = profile.get("ProvisionedDevices")
                            if isinstance(devices, list):
                                device_count = len(devices)
                        else:
                            profile_status = "invalid_or_unverifiable"
                    else:
                        profile_status = "invalid_or_unverifiable"
    except subprocess.TimeoutExpired:
        raise IPAError("codesign or provisioning-profile inspection exceeded its time limit")
    except (OSError, zipfile.BadZipFile):
        signature_status = "unavailable"
        if profile_status == "missing":
            profile_status = "unavailable"
    return signature_status, team_id, profile_status, profile_team, profile_expires, device_count


def _thin_macho_encrypted(stream: Any, offset: int, total_size: int) -> bool | None:
    stream.seek(offset)
    header = stream.read(32)
    if len(header) < 28:
        return None
    magic = header[:4]
    endian = "<" if magic in {b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"} else ">"
    is_64 = magic in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}
    if magic not in {b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"}:
        return None
    header_size = 32 if is_64 else 28
    if len(header) < header_size:
        return None
    command_count, command_bytes = struct.unpack_from(endian + "II", header, 16)
    if command_count > 65535 or command_bytes > 1024 * 1024 or offset + header_size + command_bytes > total_size:
        return None
    load_commands = stream.read(command_bytes)
    if len(load_commands) != command_bytes:
        return None
    cursor = 0
    for _ in range(command_count):
        if cursor + 8 > len(load_commands):
            return None
        command, command_size = struct.unpack_from(endian + "II", load_commands, cursor)
        if command_size < 8 or cursor + command_size > len(load_commands):
            return None
        command_type = command & 0x7FFFFFFF
        if command_type in {0x21, 0x2C} and command_size >= 20:
            crypt_id = struct.unpack_from(endian + "I", load_commands, cursor + 16)[0]
            return crypt_id != 0
        cursor += command_size
    return False


def _macho_encrypted(stream: Any, total_size: int) -> bool | None:
    stream.seek(0)
    header = stream.read(8)
    if len(header) < 4:
        return None
    magic = header[:4]
    if magic in {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}:
        endian = ">" if magic in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"} else "<"
        is_64 = magic in {b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
        count = struct.unpack_from(endian + "I", header, 4)[0]
        arch_size = 32 if is_64 else 20
        if count == 0 or count > 64:
            return None
        arch_table = stream.read(count * arch_size)
        if len(arch_table) != count * arch_size:
            return None
        observations: list[bool] = []
        for index in range(count):
            entry = arch_table[index * arch_size : (index + 1) * arch_size]
            if is_64:
                _cpu, _subcpu, slice_offset, _slice_size, _align, _reserved = struct.unpack(endian + "IIQQII", entry)
            else:
                _cpu, _subcpu, slice_offset, _slice_size, _align = struct.unpack(endian + "IIIII", entry)
            observed = _thin_macho_encrypted(stream, slice_offset, total_size)
            if observed is True:
                return True
            if observed is False:
                observations.append(False)
        return False if observations else None
    return _thin_macho_encrypted(stream, 0, total_size)


def inspect_ipa(path: str | os.PathLike[str], timeout_seconds: float = 120.0) -> IPAInfo:
    supplied = Path(path).expanduser()
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise IPAError("IPA path does not exist or cannot be resolved") from exc
    if not resolved.is_file():
        raise IPAError("IPA path is not a regular file")
    if resolved.suffix.lower() != ".ipa":
        raise IPAError("input file must have an .ipa extension")

    if timeout_seconds <= 0:
        raise IPAError("IPA inspection exceeded its time limit")
    deadline = time.monotonic() + timeout_seconds
    try:
        with zipfile.ZipFile(resolved) as zf:
            members = zf.infolist()
            if not members:
                raise IPAError("IPA archive is empty")
            if len(members) > 200_000:
                raise IPAError("IPA contains an unreasonable number of archive entries")

            total_size = 0
            app_roots: set[str] = set()
            info_paths: list[str] = []
            for member_info in members:
                member = _validate_member_name(member_info.filename)
                mode = (member_info.external_attr >> 16) & 0xFFFF
                total_size += member_info.file_size
                if total_size > 8 * 1024 * 1024 * 1024:
                    raise IPAError("IPA expands beyond the 8 GiB inspection limit")
                if member_info.flag_bits & 0x1:
                    raise IPAError("encrypted IPA entries are not supported")
                if stat.S_ISLNK(mode):
                    _validate_symlink_target(zf, member_info, member)
                if len(member.parts) >= 3 and member.parts[0] == "Payload" and member.parts[1].endswith(".app"):
                    app_roots.add(member.parts[1])
                    if member.parts[2:] == ("Info.plist",):
                        info_paths.append(member_info.filename)

            if len(app_roots) != 1:
                raise IPAError("IPA must contain exactly one app directly under Payload/")
            if len(info_paths) != 1:
                raise IPAError("the app must contain exactly one Payload/<App>.app/Info.plist")

            # Read each member in bounded chunks to validate CRCs without extracting
            # archive entries to disk or trusting archive-provided paths.
            for member_info in members:
                if time.monotonic() > deadline:
                    raise IPAError("IPA integrity check exceeded its time limit")
                if member_info.is_dir() or stat.S_ISLNK((member_info.external_attr >> 16) & 0xFFFF):
                    continue
                if member_info.flag_bits & 0x1:
                    raise IPAError("encrypted IPA entries are not supported")
                with zf.open(member_info) as stream:
                    while stream.read(1024 * 1024):
                        if time.monotonic() > deadline:
                            raise IPAError("IPA integrity check exceeded its time limit")

            plist_bytes = zf.read(info_paths[0])
    except IPAError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError) as exc:
        raise IPAError("IPA archive is corrupt or unreadable") from exc

    try:
        import plistlib

        plist = plistlib.loads(plist_bytes)
    except Exception as exc:
        raise IPAError("the app Info.plist is invalid") from exc
    if not isinstance(plist, dict):
        raise IPAError("the app Info.plist root is not a dictionary")

    bundle_id = plist.get("CFBundleIdentifier")
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        raise IPAError("the app Info.plist has no CFBundleIdentifier")
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id):
        raise IPAError("the app bundle identifier has an unexpected format")

    supported = plist.get("CFBundleSupportedPlatforms")
    platform_name = plist.get("DTPlatformName")
    if isinstance(supported, list) and supported and "iPhoneOS" not in supported:
        raise IPAError("IPA is not built for iPhoneOS devices")
    if isinstance(platform_name, str) and platform_name.lower() == "iphonesimulator":
        raise IPAError("simulator builds cannot be installed as device IPAs")
    device_platform_confirmed = (isinstance(supported, list) and "iPhoneOS" in supported) or (
        isinstance(platform_name, str) and platform_name.lower() == "iphoneos"
    )
    if not device_platform_confirmed:
        raise IPAError("cannot confirm that the IPA was built for iPhoneOS devices")
    families = plist.get("UIDeviceFamily")
    if isinstance(families, list) and families and not any(item in (1, 2) for item in families):
        raise IPAError("IPA does not target iPhone or iPad devices")

    app_directory = next(iter(app_roots))
    executable_relative = plist.get("CFBundleExecutable")
    executable_member: str | None = None
    if isinstance(executable_relative, str) and executable_relative and "/" not in executable_relative and "\\" not in executable_relative:
        executable_member = f"Payload/{app_directory}/{executable_relative}"
        try:
            with zipfile.ZipFile(resolved) as zf:
                executable_info = zf.getinfo(executable_member)
                with zf.open(executable_info) as executable_stream:
                    main_executable_encrypted = _macho_encrypted(executable_stream, executable_info.file_size)
        except (KeyError, OSError, zipfile.BadZipFile, RuntimeError):
            main_executable_encrypted = None
    else:
        main_executable_encrypted = None

    encryption_observations: list[bool] = []
    if main_executable_encrypted is not None:
        encryption_observations.append(main_executable_encrypted)
    try:
        with zipfile.ZipFile(resolved) as zf:
            for member_info in zf.infolist():
                if time.monotonic() > deadline:
                    raise IPAError("IPA encryption inspection exceeded its time limit")
                member = _validate_member_name(member_info.filename)
                if len(member.parts) < 3 or member.parts[:2] != ("Payload", app_directory):
                    continue
                mode = (member_info.external_attr >> 16) & 0xFFFF
                if executable_member is not None and member_info.filename.rstrip("/") == executable_member:
                    continue
                if not (mode & 0o111) or member_info.is_dir() or stat.S_ISLNK(mode):
                    continue
                try:
                    with zf.open(member_info) as candidate:
                        encrypted = _macho_encrypted(candidate, member_info.file_size)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    encrypted = None
                if encrypted is not None:
                    encryption_observations.append(encrypted)
                if encrypted is True:
                    break
    except IPAError:
        raise
    except (OSError, zipfile.BadZipFile):
        pass
    encryption_status = "encrypted" if True in encryption_observations else ("not_encrypted" if encryption_observations else "unknown")

    signature, signing_team, profile_state, profile_team, profile_expires, provisioned_count = _inspect_embedded_state(
        resolved, app_directory, deadline
    )
    return IPAInfo(
        path=str(resolved),
        filename=resolved.name,
        app_name=plist.get("CFBundleDisplayName") or plist.get("CFBundleName"),
        bundle_id=bundle_id,
        version=plist.get("CFBundleShortVersionString"),
        build=plist.get("CFBundleVersion"),
        minimum_os=plist.get("MinimumOSVersion"),
        platform="iPhoneOS",
        app_directory=app_directory,
        original_signature=signature,
        original_team_id=signing_team,
        embedded_profile=profile_state,
        profile_team_id=profile_team,
        profile_expires=profile_expires,
        provisioned_device_count=provisioned_count,
        encryption_status=encryption_status,
    )

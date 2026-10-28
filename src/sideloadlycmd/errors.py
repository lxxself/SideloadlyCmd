from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    PARTIAL = 3
    USAGE = 2
    PREFLIGHT = 10
    APP_MISSING = 11
    AUTOMATION = 12
    UNSUPPORTED_UI = 13
    INVALID_IPA = 20
    INSTALL_FAILED = 30
    TIMEOUT = 31
    NEEDS_USER = 32
    UNKNOWN = 33


@dataclass
class CliError(Exception):
    message: str
    code: ExitCode
    category: str

    def __str__(self) -> str:
        return self.message

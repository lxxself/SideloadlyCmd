"""Atomic private JSON state writes, including interruption-safe cleanup."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def write_json(path: Path, data: dict[str, Any]) -> None:
    if path.is_symlink():
        raise OSError('private JSON target cannot be a symbolic link')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.state-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory = os.open(path.parent,os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

"""Stable file entry point for source, copied runtimes and installed wheels."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from sideloadlycmd.cli import main

if __name__=='__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run every Python script in this folder except variableextraction.py.

Usage:
    python runall.py
    python runall.py usda-milk-production.yml
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
EXCLUDED_FILES = {"variableextraction.py", "runall.py", "community_identifiers.py", "detect_five_ws.py"}
DEFAULT_TARGET = "all"


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET

    scripts = sorted(
        path for path in SCRIPT_DIR.glob("*.py")
        if path.name not in EXCLUDED_FILES
    )

    if not scripts:
        print(f"No Python scripts found in {SCRIPT_DIR}")
        return

    for script_path in scripts:
        print(f"\n=== Running {script_path.name} on {target} ===")
        result = subprocess.run(
            [sys.executable, str(script_path), target],
            cwd=SCRIPT_DIR,
        )
        if result.returncode != 0:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
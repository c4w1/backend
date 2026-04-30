#!/usr/bin/env python3
"""
Linear Regression true or false labeler

Reads in one or all YML files from data/sources, looks at the variable_report field
and a write a linear_regression boolean to the YML file based on the criteria below.

A dataset gets tagged with linear regression = true if:
  - has at least 10 rows of data
  - has at least 2 numeric (integer or float) columns
  - At least one numeric column has more than 1 unique value (variance exists)
  - At least one numeric column has fewer than 80% missing values

Files without the variable_report field are skipped.

Usage:

  python labelregression.py all
  python labelregression.py usda-milk-production.yml
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources" #Change if the sources are moved to a different folder

#adjust if needed
MIN_ROWS = 10
MIN_NUMERIC_COLUMNS = 2
MAX_MISSING_PCT = 80.0

#checks dataset based on the criteria
def is_suitable_for_regression(variable_report: list) -> tuple[bool, str]:
    """
    Returns (suitable, reason) based on variable_report entries.
    """
#checks that the YML file has a variable_report field
    if not variable_report:
        return False, "no variable_report data"

#checks for the minimum number of rows
    total_rows = variable_report[0].get("total_rows", 0)
    if total_rows < MIN_ROWS:
        return False, f"too few rows ({total_rows} < {MIN_ROWS})"

#maximum missing percentage check
    numeric_cols = [
        col for col in variable_report
        if col.get("type") in ("integer", "float")
        and col.get("missing_pct", 100.0) < MAX_MISSING_PCT
    ]
#minum number of numeric columns check
    if len(numeric_cols) < MIN_NUMERIC_COLUMNS:
        return False, f"fewer than {MIN_NUMERIC_COLUMNS} usable numeric columns ({len(numeric_cols)} found)"

    has_variance = any(col.get("unique_count", 0) > 1 for col in numeric_cols)
    if not has_variance:
        return False, "numeric columns have no variance (all values identical)"

    return True, f"{len(numeric_cols)} numeric columns, {total_rows} rows"


#write to the YML file
def load_yml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yml(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

#process each YML file, check for the variable report field and write the boolean value
def process_yml(yml_path: str) -> None:
    print(f"\nProcessing: {yml_path}")

    doc = load_yml(yml_path)

    variable_report = doc.get("variable_report")
    if not variable_report:
        print("  SKIPPED — no variable_report field found.")
        return

    suitable, reason = is_suitable_for_regression(variable_report)
    doc["linear_regression"] = suitable

    save_yml(yml_path, doc)
    label = "true" if suitable else "false"
    print(f"  linear_regression: {label} ({reason})")

#print the usage and read the command line arguments
def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python labelregression.py all")
        print("  python labelregression.py usda-milk-production.yml")
        sys.exit(1)

    target = sys.argv[1]
#if targeting to process all YML files
    if target == "all":
        yml_files = sorted(
            glob.glob(str(DEFAULT_SOURCES_DIR / "*.yml")) +
            glob.glob(str(DEFAULT_SOURCES_DIR / "*.yaml"))
        )
        if not yml_files:
            print(f"No .yml files found in '{DEFAULT_SOURCES_DIR}'.")
            sys.exit(0)
        print(f"Found {len(yml_files)} YML file(s) in '{DEFAULT_SOURCES_DIR}'")
        for yml_path in yml_files:
            process_yml(yml_path)
    #if processing a single YML file
    else:
        target_path = Path(target)
        if not target_path.is_absolute() and not target_path.exists():
            target_path = DEFAULT_SOURCES_DIR / target_path
        if not target_path.is_file():
            print(f"Error: '{target_path}' is not a file.")
            sys.exit(1)
        process_yml(str(target_path))

    print("\nDone.")


if __name__ == "__main__":
    main()

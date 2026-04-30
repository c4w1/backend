#!/usr/bin/env python3
"""
Automation Rules Tagger

Reads in one or more YAML files from backend/data/sources, downloads the data from the
download url, and records which pipeline automation rules apply to the dataset.
Writes automation_rules_tags to the YAML file.

These tags encode the structural prerequisites that govern which analysis scripts produce
meaningful results for this dataset.

Rules applied:
  - Categorical variable = non-numeric dtype OR numeric with < 10 unique values
  - Numeric variable     = numeric dtype AND >= 10 unique values
  - Pair analysis requires 2+ numeric columns (all possible pairs are tested)
  - Group comparisons test ALL numeric columns against ALL categorical variables
  - Inference tests require both sample size AND distributional assumption checks
  - Paired t-test uses differences between the two numeric columns, tests normality of diff

Tags:
  * has-multiple-numeric-columns:    2+ numeric columns exist; ALL pairs will be tested for
                                     correlation and regression tags
  * has-categorical-variable:        at least 1 categorical column (non-numeric OR < 10 unique
                                     values); ANY-variable and ALL-variable rules apply
  * has-group-comparison-candidates: at least 1 categorical AND at least 1 numeric column;
                                     ALL numeric columns tested against ALL categorical variables
  * meets-minimum-sample-size:       n >= 10; minimum threshold for inference procedure checks
  * has-paired-test-candidates:      exactly 2 numeric columns AND n >= 10; differences between
                                     the two columns will be computed and tested for normality

Usage:
    python automationrules.py all
    python automationrules.py usda-milk-production.yml
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests
import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"


def find_yaml_files(sources_dir: Path, one_file: str | None) -> list[Path]:
    if one_file:
        file_path = Path(one_file)
        return [file_path] if file_path.exists() else []
    return sorted(sources_dir.glob("*.yml"))


def download_file(url: str, timeout: int) -> bytes | None:
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content
    except Exception as exc:
        print(f"  - Could not download {url}: {exc}")
        return None


def extract_data_from_zip(zip_data: bytes) -> pd.DataFrame | None:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            data_files = [f for f in zf.namelist() if f.lower().endswith((".csv", ".tsv", ".xlsx"))]
            if not data_files:
                print("  - No CSV, TSV, or XLSX files found in ZIP")
                return None
            first_file = data_files[0]
            with zf.open(first_file) as f:
                if first_file.lower().endswith(".csv"):
                    return pd.read_csv(f, low_memory=False)
                elif first_file.lower().endswith(".tsv"):
                    return pd.read_csv(f, sep="\t", low_memory=False)
                else:
                    return pd.read_excel(f)
    except Exception as exc:
        print(f"  - Could not extract data from ZIP: {exc}")
        return None


def load_csv_file(data: bytes) -> pd.DataFrame | None:
    try:
        return pd.read_csv(io.BytesIO(data), low_memory=False)
    except Exception as exc:
        print(f"  - Could not parse CSV: {exc}")
        return None


def load_tsv_file(data: bytes) -> pd.DataFrame | None:
    try:
        return pd.read_csv(io.BytesIO(data), sep="\t", low_memory=False)
    except Exception as exc:
        print(f"  - Could not parse TSV: {exc}")
        return None


def load_xlsx_file(data: bytes) -> pd.DataFrame | None:
    try:
        return pd.read_excel(io.BytesIO(data))
    except Exception as exc:
        print(f"  - Could not parse XLSX: {exc}")
        return None


def download_and_parse_data(url: str, timeout: int) -> pd.DataFrame | None:
    file_data = download_file(url, timeout)
    if file_data is None:
        return None

    url_lower = url.lower()
    if url_lower.endswith(".csv"):
        df = load_csv_file(file_data)
        if df is not None:
            return df
    elif url_lower.endswith(".tsv"):
        df = load_tsv_file(file_data)
        if df is not None:
            return df
    elif url_lower.endswith(".xlsx"):
        df = load_xlsx_file(file_data)
        if df is not None:
            return df
    elif url_lower.endswith(".zip"):
        df = extract_data_from_zip(file_data)
        if df is not None:
            return df

    for loader in [load_csv_file, load_tsv_file, load_xlsx_file, extract_data_from_zip]:
        df = loader(file_data)
        if df is not None:
            return df

    return None


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Record which automation rules apply to this dataset."""
    n = len(df)

    # categorical: non-numeric dtype OR numeric with < 10 unique values
    cat_count = sum(
        1 for col in df.columns
        if not pd.api.types.is_numeric_dtype(df[col]) or df[col].nunique() < 10
    )

    # numeric: numeric dtype AND >= 10 unique values
    num_cols = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col]) and df[col].nunique() >= 10
    ]
    num_count = len(num_cols)

    return {
        # has-multiple-numeric-columns: 2+ numeric columns; ALL pairs tested for correlation/regression
        "has-multiple-numeric-columns": num_count >= 2,

        # has-categorical-variable: at least 1 categorical column (non-numeric OR < 10 unique values)
        "has-categorical-variable": cat_count >= 1,

        # has-group-comparison-candidates: both categorical AND numeric columns exist;
        # ALL numeric columns will be tested against ALL categorical variables
        "has-group-comparison-candidates": cat_count >= 1 and num_count >= 1,

        # meets-minimum-sample-size: n >= 10; inference procedure checks require this
        "meets-minimum-sample-size": n >= 10,

        # has-paired-test-candidates: exactly 2 numeric columns AND n >= 10;
        # differences between the two columns will be computed and tested for normality
        "has-paired-test-candidates": num_count == 2 and n >= 10,
    }


def process_file(path: Path, timeout: int, dry_run: bool) -> None:
    print(f"\nProcessing: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        print("  - Skipped (YAML root is not an object)")
        return

    download_info = data.get("download", {})
    download_url = download_info.get("url") if isinstance(download_info, dict) else None

    if not download_url:
        print("  - Skipped (no download URL found)")
        return

    df = download_and_parse_data(download_url, timeout=timeout)
    if df is None:
        print("  - Skipped (could not download or parse data)")
        return

    num_count = sum(
        1 for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col]) and df[col].nunique() >= 10
    )
    cat_count = sum(
        1 for col in df.columns
        if not pd.api.types.is_numeric_dtype(df[col]) or df[col].nunique() < 10
    )
    print(f"  - Numeric columns: {num_count}, Categorical columns: {cat_count}, Rows: {len(df)}")

    tags = infer_tags(df)
    matched = [tag for tag, val in tags.items() if val]
    print(f"  - Tags matched: {matched}")

    if dry_run:
        return

    data["automation_rules_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python automationrules.py all")
        print("  python automationrules.py usda-milk-production.yml")
        sys.exit(1)

    target = sys.argv[1]

    if target == "all":
        files = find_yaml_files(sources_dir=DEFAULT_SOURCES_DIR, one_file=None)
    else:
        target_path = Path(target)
        if not target_path.is_absolute() and not target_path.exists():
            target_path = DEFAULT_SOURCES_DIR / target_path
        files = find_yaml_files(sources_dir=DEFAULT_SOURCES_DIR, one_file=str(target_path))

    if not files:
        print("No YAML files found.")
        return

    for path in files:
        process_file(path=path, timeout=30, dry_run=False)

    print("\nDone.")


if __name__ == "__main__":
    main()

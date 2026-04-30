#!/usr/bin/env python3
"""
Value Scale Tagger

Reads in one or more YAML files from backend/data/sources, downloads the data from the download url
writes the tags back to the YAML file as value_scale_tags: [..]
  * single-digit-values: max(abs(all values across all numeric columns)) < 10
  * double-digit-values: 10 <= max(abs(all values)) < 100
  * three-digit-values: 100 <= max(abs(all values)) < 1000
  * large-scale-values: max(abs(all values)) >= 1000

Usage:
    python valuescale.py all
    python valuescale.py usda-milk-production.yml
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
    """Download file from URL and return raw bytes."""
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content
    except Exception as exc:
        print(f"  - Could not download {url}: {exc}")
        return None


def extract_data_from_zip(zip_data: bytes) -> pd.DataFrame | None:
    """Extract first data file (CSV, TSV, or XLSX) from ZIP and return as DataFrame."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            data_files = [
                f for f in zf.namelist() if f.lower().endswith((".csv", ".tsv", ".xlsx"))
            ]
            if not data_files:
                print("  - No CSV, TSV, or XLSX files found in ZIP")
                return None

            first_file = data_files[0]
            with zf.open(first_file) as f:
                if first_file.lower().endswith(".csv"):
                    df = pd.read_csv(f, low_memory=False)
                elif first_file.lower().endswith(".tsv"):
                    df = pd.read_csv(f, sep="\t", low_memory=False)
                else:
                    df = pd.read_excel(f)
                return df
    except Exception as exc:
        print(f"  - Could not extract data from ZIP: {exc}")
        return None


def load_csv_file(data: bytes) -> pd.DataFrame | None:
    """Load CSV data from bytes."""
    try:
        return pd.read_csv(io.BytesIO(data), low_memory=False)
    except Exception as exc:
        print(f"  - Could not parse CSV: {exc}")
        return None


def load_tsv_file(data: bytes) -> pd.DataFrame | None:
    """Load TSV data from bytes."""
    try:
        return pd.read_csv(io.BytesIO(data), sep="\t", low_memory=False)
    except Exception as exc:
        print(f"  - Could not parse TSV: {exc}")
        return None


def load_xlsx_file(data: bytes) -> pd.DataFrame | None:
    """Load XLSX data from bytes."""
    try:
        return pd.read_excel(io.BytesIO(data))
    except Exception as exc:
        print(f"  - Could not parse XLSX: {exc}")
        return None


def download_and_parse_data(url: str, timeout: int) -> pd.DataFrame | None:
    """Download and parse data from URL. Handles CSV, TSV, XLSX, and ZIP files."""
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


def infer_tags(df: pd.DataFrame) -> list[str]:
    """Infer value scale tags from DataFrame."""
    numeric_df = df.select_dtypes(include=["number"])

    if numeric_df.empty:
        # Fallback when no numeric columns are available.
        return ["single-digit-values"]

    max_abs = numeric_df.abs().max().max()

    if pd.isna(max_abs):
        return ["single-digit-values"]

    if max_abs < 10:
        return ["single-digit-values"]
    if max_abs < 100:
        return ["double-digit-values"]
    if max_abs < 1000:
        return ["three-digit-values"]
    return ["large-scale-values"]


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

    tags = infer_tags(df)
    print(f"  - Tags: {tags}")

    if dry_run:
        return

    data["value_scale_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python valuescale.py all")
        print("  python valuescale.py usda-milk-production.yml")
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


if __name__ == "__main__":
    main()

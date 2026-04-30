#!/usr/bin/env python3
"""
Structure and Complexity Tagger


Tags: [..]
  * univariate: count of numeric columns == 1
  * bivariate: count of numeric columns == 2
  * multivariate: count of numeric columns >= 3
  * small-dataset: row count < 20
  * medium-dataset: 20 <= row count < 100
  * large-dataset: row count >= 100


Usage:
    python structureandcomplexity.py all
    python structureandcomplexity.py usda-milk-production.yml
"""

from __future__ import annotations

import io
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

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
    """Download file from URL and return byte data."""
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
            # Look for CSV, TSV, or XLSX files
            data_files = [f for f in zf.namelist() if f.lower().endswith(('.csv', '.tsv', '.xlsx'))]
            if not data_files:
                print(f"  - No CSV, TSV, or XLSX files found in ZIP")
                return None
            
            first_file = data_files[0]
            with zf.open(first_file) as f:
                if first_file.lower().endswith('.csv'):
                    df = pd.read_csv(f, low_memory=False)
                elif first_file.lower().endswith('.tsv'):
                    df = pd.read_csv(f, sep='\t', low_memory=False)
                elif first_file.lower().endswith('.xlsx'):
                    df = pd.read_excel(f)
                return df
    except Exception as exc:
        print(f"  - Could not extract data from ZIP: {exc}")
        return None


def load_csv_file(data: bytes) -> pd.DataFrame | None:
    """Load CSV data from bytes."""
    try:
        df = pd.read_csv(io.BytesIO(data), low_memory=False)
        return df
    except Exception as exc:
        print(f"  - Could not parse CSV: {exc}")
        return None


def load_tsv_file(data: bytes) -> pd.DataFrame | None:
    """Load TSV data from bytes."""
    try:
        df = pd.read_csv(io.BytesIO(data), sep='\t', low_memory=False)
        return df
    except Exception as exc:
        print(f"  - Could not parse TSV: {exc}")
        return None


def load_xlsx_file(data: bytes) -> pd.DataFrame | None:
    """Load XLSX data from bytes."""
    try:
        df = pd.read_excel(io.BytesIO(data))
        return df
    except Exception as exc:
        print(f"  - Could not parse XLSX: {exc}")
        return None


def download_and_parse_data(url: str, timeout: int) -> pd.DataFrame | None:
    """Download and parse data from URL. Handles CSV, TSV, XLSX, and ZIP files."""
    file_data = download_file(url, timeout)
    if file_data is None:
        return None
    
    url_lower = url.lower()
    
    # Try based on file extension
    if url_lower.endswith('.csv'):
        df = load_csv_file(file_data)
        if df is not None:
            return df
    elif url_lower.endswith('.tsv'):
        df = load_tsv_file(file_data)
        if df is not None:
            return df
    elif url_lower.endswith('.xlsx'):
        df = load_xlsx_file(file_data)
        if df is not None:
            return df
    elif url_lower.endswith('.zip'):
        df = extract_data_from_zip(file_data)
        if df is not None:
            return df
    
    # Try all formats as fallback
    for loader in [load_csv_file, load_tsv_file, load_xlsx_file, extract_data_from_zip]:
        try:
            df = loader(file_data)
            if df is not None:
                return df
        except Exception:
            continue
    
    return None


def count_numeric_columns(df: pd.DataFrame) -> int:
    """Count numeric columns in DataFrame."""
    numeric_dtypes = df.select_dtypes(include=['number']).columns
    return len(numeric_dtypes)


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Infer structure and complexity tags from DataFrame."""
    numeric_col_count = count_numeric_columns(df)
    row_count = len(df)
    return {
        "univariate": numeric_col_count == 1,
        "bivariate": numeric_col_count == 2,
        "multivariate": numeric_col_count >= 3,
        "small-dataset": row_count < 20,
        "medium-dataset": 20 <= row_count < 100,
        "large-dataset": row_count >= 100,
    }


def process_file(path: Path, timeout: int, dry_run: bool) -> None:
    print(f"\nProcessing: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        print("  - Skipped (YAML root is not an object)")
        return

    # Extract download URL
    download_info = data.get("download", {})
    if isinstance(download_info, dict):
        download_url = download_info.get("url")
    else:
        download_url = None

    if not download_url:
        print("  - Skipped (no download URL found)")
        return

    # Download and parse data
    df = download_and_parse_data(download_url, timeout=timeout)
    if df is None:
        print("  - Skipped (could not download or parse data)")
        return

    # Infer tags
    tags = infer_tags(df)
    print(f"  - Numeric columns: {count_numeric_columns(df)}, Rows: {len(df)}")
    matched = [tag for tag, val in tags.items() if val]
    print(f"  - Tags matched: {matched}")

    if dry_run:
        return

    data["structure_complexity_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python structureandcomplexity.py all")
        print("  python structureandcomplexity.py usda-milk-production.yml")
        sys.exit(1)

    target = sys.argv[1]

    if target == "all":
        files = find_yaml_files(sources_dir=DEFAULT_SOURCES_DIR, one_file=None)
    else:
        # accept filename or path
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

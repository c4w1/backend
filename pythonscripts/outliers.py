#!/usr/bin/env python3
"""
Outliers Tagger

Tags:
  * contains-outliers:  ANY numeric column has values < Q1 - 1.5*IQR OR > Q3 + 1.5*IQR
  * no-outliers:        ALL numeric columns have no values outside Q1 - 1.5*IQR to Q3 + 1.5*IQR
  * extreme-outliers:   ANY numeric column has values < Q1 - 3*IQR OR > Q3 + 3*IQR

Usage:
    python outliers.py all
    python outliers.py usda-milk-production.yml
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


def check_column(series: pd.Series) -> dict[str, bool] | None:
    """Compute outlier results for a single numeric column using the IQR method."""
    values = series.dropna()
    if len(values) == 0:
        return None

    has_outliers = False
    has_extreme_outliers = False

    try:
        q1 = float(values.quantile(0.25))
        q3 = float(values.quantile(0.75))
        iqr = q3 - q1

        # contains-outliers: values < Q1 - 1.5*IQR OR > Q3 + 1.5*IQR
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        has_outliers = bool(((values < lower) | (values > upper)).any())

        # extreme-outliers: values < Q1 - 3*IQR OR > Q3 + 3*IQR
        extreme_lower = q1 - 3 * iqr
        extreme_upper = q3 + 3 * iqr
        has_extreme_outliers = bool(((values < extreme_lower) | (values > extreme_upper)).any())

    except Exception:
        pass

    return {
        "has_outliers": has_outliers,
        "has_extreme_outliers": has_extreme_outliers,
    }


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Analyze ALL numeric columns using the IQR method.
    contains-outliers and extreme-outliers are true if ANY column meets criteria.
    no-outliers requires ALL columns to have no outliers."""
    tags: dict[str, bool] = {
        "contains-outliers": False,
        "no-outliers": True,  # starts True; flipped False if any column has outliers
        "extreme-outliers": False,
    }

    numeric_cols = df.select_dtypes(include=["number"]).columns
    if len(numeric_cols) == 0:
        tags["no-outliers"] = False
        return tags

    for col in numeric_cols:
        result = check_column(df[col])
        if result is None:
            continue

        # contains-outliers: ANY numeric column has values < Q1 - 1.5*IQR OR > Q3 + 1.5*IQR
        if result["has_outliers"]:
            tags["contains-outliers"] = True

        # no-outliers: ALL columns must have no values outside Q1 - 1.5*IQR to Q3 + 1.5*IQR
        if result["has_outliers"]:
            tags["no-outliers"] = False

        # extreme-outliers: ANY numeric column has values < Q1 - 3*IQR OR > Q3 + 3*IQR
        if result["has_extreme_outliers"]:
            tags["extreme-outliers"] = True

    return tags


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

    numeric_cols = df.select_dtypes(include=["number"]).columns
    print(f"  - Numeric columns: {len(numeric_cols)}, Rows: {len(df)}")

    tags = infer_tags(df)
    matched = [tag for tag, val in tags.items() if val]
    print(f"  - Tags matched: {matched}")

    if dry_run:
        return

    data["outlier_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python outliers.py all")
        print("  python outliers.py usda-milk-production.yml")
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

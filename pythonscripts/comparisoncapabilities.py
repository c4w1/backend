#!/usr/bin/env python3
"""
Comparison Capabilities Tagger

Tags:
  * two-groups-present:          has a categorical variable with exactly 2 unique values
  * multiple-groups-present:     has a categorical variable with 3-10 unique values
  * groups-have-different-centers: IF categorical + numeric variables exist, ANOVA/t-test
                                   p-value < 0.05 for ANY (categorical, numeric) combination
  * groups-have-different-spreads: IF categorical + numeric variables exist, Levene's test
                                   p-value < 0.05 for ANY (categorical, numeric) combination
  * groups-overlap-substantially:  IF categorical + numeric variables exist, IQR ranges of
                                   any two groups overlap for ANY combination

Usage:
    python comparisoncapabilities.py all
    python comparisoncapabilities.py usda-milk-production.yml
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml
from scipy.stats import f_oneway, levene, ttest_ind


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


def check_cat_num_pair(
    cat_series: pd.Series, num_series: pd.Series
) -> dict[str, bool] | None:
    """Run group comparison tests for one (categorical, numeric) column pair."""
    combined = pd.DataFrame({"cat": cat_series, "num": num_series}).dropna()
    groups = [
        combined.loc[combined["cat"] == val, "num"].to_numpy(dtype=float)
        for val in combined["cat"].unique()
    ]
    groups = [g for g in groups if len(g) >= 2]
    if len(groups) < 2:
        return None

    different_centers = False
    different_spreads = False
    overlap = False

    # groups-have-different-centers: ANOVA or t-test p-value < 0.05
    try:
        if len(groups) == 2:
            _, p = ttest_ind(groups[0], groups[1])
        else:
            _, p = f_oneway(*groups)
        different_centers = bool(p < 0.05)
    except Exception:
        pass

    # groups-have-different-spreads: Levene's test p-value < 0.05
    try:
        _, p = levene(*groups)
        different_spreads = bool(p < 0.05)
    except Exception:
        pass

    # groups-overlap-substantially: IQR ranges of any two groups overlap
    try:
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                q1_a, q3_a = np.percentile(groups[i], [25, 75])
                q1_b, q3_b = np.percentile(groups[j], [25, 75])
                if max(q1_a, q1_b) < min(q3_a, q3_b):
                    overlap = True
                    break
            if overlap:
                break
    except Exception:
        pass

    return {
        "different_centers": different_centers,
        "different_spreads": different_spreads,
        "overlap": overlap,
    }


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Identify categorical and numeric columns, then evaluate comparison capabilities."""
    tags: dict[str, bool] = {
        "two-groups-present": False,
        "multiple-groups-present": False,
        "groups-have-different-centers": False,
        "groups-have-different-spreads": False,
        "groups-overlap-substantially": False,
    }

    # categorical: non-numeric dtype OR < 10 unique values
    cat_cols: list[str] = []
    for col in df.columns:
        n_unique = df[col].nunique()
        is_non_numeric = not pd.api.types.is_numeric_dtype(df[col])
        if is_non_numeric or n_unique < 10:
            # two-groups-present
            if n_unique == 2:
                tags["two-groups-present"] = True
            # multiple-groups-present
            if 3 <= n_unique <= 10:
                tags["multiple-groups-present"] = True
            if 2 <= n_unique <= 10:
                cat_cols.append(col)

    # numeric variables for analysis: numeric dtype with >= 10 unique values
    num_cols = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col]) and df[col].nunique() >= 10
    ]

    if not cat_cols or not num_cols:
        return tags

    for cat_col in cat_cols:
        for num_col in num_cols:
            result = check_cat_num_pair(df[cat_col], df[num_col])
            if result is None:
                continue

            # groups-have-different-centers
            if result["different_centers"]:
                tags["groups-have-different-centers"] = True

            # groups-have-different-spreads
            if result["different_spreads"]:
                tags["groups-have-different-spreads"] = True

            # groups-overlap-substantially
            if result["overlap"]:
                tags["groups-overlap-substantially"] = True

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

    cat_count = sum(
        1 for col in df.columns
        if not pd.api.types.is_numeric_dtype(df[col]) or df[col].nunique() < 10
    )
    num_count = sum(
        1 for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col]) and df[col].nunique() >= 10
    )
    print(f"  - Categorical columns: {cat_count}, Numeric columns: {num_count}, Rows: {len(df)}")

    tags = infer_tags(df)
    matched = [tag for tag, val in tags.items() if val]
    print(f"  - Tags matched: {matched}")

    if dry_run:
        return

    data["comparison_capabilities_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python comparisoncapabilities.py all")
        print("  python comparisoncapabilities.py usda-milk-production.yml")
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

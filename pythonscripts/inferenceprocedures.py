#!/usr/bin/env python3
"""
Inference Procedures Tagger

Tags:
  * good-for-one-sample-t-test:      at least 1 numeric column AND (approximately-normal OR large-enough-for-clt)
  * good-for-one-sample-z-test:      at least 1 numeric column AND (approximately-normal OR large-enough-for-clt)
  * good-for-one-proportion-z-test:  exactly 1 categorical column with exactly 2 unique values AND n >= 10
  * good-for-two-sample-t-test:      two-groups-present AND at least 1 numeric column AND each group n >= 5
                                     AND (approximately-normal OR large-enough-for-clt OR each group n >= 30)
  * good-for-two-sample-z-test:      two-groups-present AND at least 1 numeric column AND each group n >= 30
  * good-for-two-proportion-z-test:  grouping variable with 2 groups AND categorical outcome with 2 categories
                                     AND all cells in 2x2 contingency table >= 5
  * meets-equal-variance-assumption:    two-groups-present AND Levene's test p > 0.05 for ANY (cat, num) pair
  * violates-equal-variance-assumption: two-groups-present AND Levene's test p <= 0.05 for ANY (cat, num) pair

Usage:
    python inferenceprocedures.py all
    python inferenceprocedures.py usda-milk-production.yml
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
from scipy.stats import kurtosis, levene, shapiro, skew


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


def check_approximately_normal(values: np.ndarray) -> bool:
    """Shapiro-Wilk p > 0.05 OR (|skewness| < 0.5 AND 2 < Pearson kurtosis < 4)."""
    if len(values) == 0:
        return False
    try:
        skewness = float(skew(values))
        kurt = float(kurtosis(values, fisher=False))  # Pearson kurtosis, normal = 3
        _, sw_p = shapiro(values)
        return sw_p > 0.05 or (abs(skewness) < 0.5 and 2 < kurt < 4)
    except Exception:
        return False


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    tags: dict[str, bool] = {
        "good-for-one-sample-t-test": False,
        "good-for-one-sample-z-test": False,
        "good-for-one-proportion-z-test": False,
        "good-for-two-sample-t-test": False,
        "good-for-two-sample-z-test": False,
        "good-for-two-proportion-z-test": False,
        "meets-equal-variance-assumption": False,
        "violates-equal-variance-assumption": False,
    }

    n = len(df)

    # categorical: non-numeric OR < 10 unique values
    cat_cols: list[tuple[str, int]] = []
    for col in df.columns:
        n_unique = df[col].nunique()
        if not pd.api.types.is_numeric_dtype(df[col]) or n_unique < 10:
            cat_cols.append((col, n_unique))

    # numeric: numeric dtype AND >= 10 unique values
    num_cols: list[str] = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col]) and df[col].nunique() >= 10
    ]

    two_group_cats = [(col, nu) for col, nu in cat_cols if nu == 2]
    two_groups_present = len(two_group_cats) > 0

    # large-enough-for-clt: n >= 30
    large_enough_for_clt = n >= 30

    # approximately-normal: ANY numeric column meets normality criterion
    approximately_normal = any(
        check_approximately_normal(df[col].dropna().to_numpy(dtype=float))
        for col in num_cols
    )

    # good-for-one-sample-t-test
    if num_cols and (approximately_normal or large_enough_for_clt):
        tags["good-for-one-sample-t-test"] = True

    # good-for-one-sample-z-test
    if num_cols and (approximately_normal or large_enough_for_clt):
        tags["good-for-one-sample-z-test"] = True

    # good-for-one-proportion-z-test: exactly 1 categorical column with exactly 2 unique values AND n >= 10
    two_group_cat_count = sum(1 for _, nu in cat_cols if nu == 2)
    if two_group_cat_count == 1 and n >= 10:
        tags["good-for-one-proportion-z-test"] = True

    # tags requiring two-groups-present AND at least 1 numeric column
    if two_groups_present and num_cols:
        for cat_col, _ in two_group_cats:
            unique_vals = df[cat_col].dropna().unique()
            if len(unique_vals) != 2:
                continue

            mask_a = df[cat_col] == unique_vals[0]
            mask_b = df[cat_col] == unique_vals[1]
            n_a = int(mask_a.sum())
            n_b = int(mask_b.sum())

            each_group_ge_5 = n_a >= 5 and n_b >= 5
            each_group_ge_30 = n_a >= 30 and n_b >= 30

            # good-for-two-sample-t-test
            if each_group_ge_5 and (approximately_normal or large_enough_for_clt or each_group_ge_30):
                tags["good-for-two-sample-t-test"] = True

            # good-for-two-sample-z-test
            if each_group_ge_30:
                tags["good-for-two-sample-z-test"] = True

            # meets-equal-variance-assumption / violates-equal-variance-assumption
            for num_col in num_cols:
                vals_a = df.loc[mask_a, num_col].dropna().to_numpy(dtype=float)
                vals_b = df.loc[mask_b, num_col].dropna().to_numpy(dtype=float)
                if len(vals_a) >= 2 and len(vals_b) >= 2:
                    try:
                        _, p = levene(vals_a, vals_b)
                        # meets-equal-variance-assumption
                        if p > 0.05:
                            tags["meets-equal-variance-assumption"] = True
                        # violates-equal-variance-assumption
                        else:
                            tags["violates-equal-variance-assumption"] = True
                    except Exception:
                        pass

    # good-for-two-proportion-z-test
    grouping_vars = [col for col, nu in cat_cols if nu == 2]
    outcome_vars = [col for col, nu in cat_cols if nu == 2]
    for grp_col in grouping_vars:
        for out_col in outcome_vars:
            if grp_col == out_col:
                continue
            try:
                ct = pd.crosstab(df[grp_col], df[out_col])
                if ct.shape == (2, 2) and (ct >= 5).all().all():
                    tags["good-for-two-proportion-z-test"] = True
            except Exception:
                pass

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

    data["inference_procedures_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python inferenceprocedures.py all")
        print("  python inferenceprocedures.py usda-milk-production.yml")
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

#!/usr/bin/env python3
"""
ANOVA Tagger

Tags:
  * good-for-one-way-anova:          has a categorical variable with 3-10 unique values AND at
                                     least 1 numeric column AND each group n >= 3 AND
                                     (approximately-normal OR large-enough-for-clt)
  * good-for-two-way-anova:          has exactly 2 categorical variables AND at least 1 numeric
                                     column AND each cell in the 2-way cross-tabulation has n >= 2
  * good-for-chi-square-gof:         has exactly 1 categorical variable AND n >= 20 AND expected
                                     frequency in each category (n / k) >= 5
  * good-for-chi-square-independence: has at least 2 categorical variables AND all expected counts
                                     in the contingency table >= 5
  * good-for-paired-t-test:          has exactly 2 numeric columns AND n >= 10 AND (difference is
                                     approximately-normal OR n >= 30)

Usage:
    python anova.py all
    python anova.py usda-milk-production.yml
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
from scipy.stats import kurtosis, shapiro, skew


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
        "good-for-one-way-anova": False,
        "good-for-two-way-anova": False,
        "good-for-chi-square-gof": False,
        "good-for-chi-square-independence": False,
        "good-for-paired-t-test": False,
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

    # large-enough-for-clt: n >= 30
    large_enough_for_clt = n >= 30

    # approximately-normal: ANY numeric column meets normality criterion
    approximately_normal = any(
        check_approximately_normal(df[col].dropna().to_numpy(dtype=float))
        for col in num_cols
    )

    # good-for-one-way-anova: categorical variable with 3-10 groups AND each group n >= 3
    #                          AND (approximately-normal OR large-enough-for-clt)
    multi_group_cats = [(col, nu) for col, nu in cat_cols if 3 <= nu <= 10]
    for cat_col, _ in multi_group_cats:
        unique_vals = df[cat_col].dropna().unique()
        group_sizes = [int((df[cat_col] == val).sum()) for val in unique_vals]
        each_group_ge_3 = all(size >= 3 for size in group_sizes)
        if each_group_ge_3 and (approximately_normal or large_enough_for_clt):
            tags["good-for-one-way-anova"] = True
            break

    # good-for-two-way-anova: exactly 2 categorical variables AND each cell n >= 2
    if len(cat_cols) == 2:
        cat_col_a, _ = cat_cols[0]
        cat_col_b, _ = cat_cols[1]
        for num_col in num_cols:
            try:
                cell_counts = (
                    df[[cat_col_a, cat_col_b, num_col]]
                    .dropna()
                    .groupby([cat_col_a, cat_col_b])[num_col]
                    .count()
                )
                if (cell_counts >= 2).all():
                    tags["good-for-two-way-anova"] = True
                    break
            except Exception:
                pass

    # good-for-chi-square-gof: exactly 1 categorical variable AND n >= 20
    #                           AND expected frequency per category (n / k) >= 5
    if len(cat_cols) == 1 and n >= 20:
        _, k = cat_cols[0]
        if k > 0 and (n / k) >= 5:
            tags["good-for-chi-square-gof"] = True

    # good-for-chi-square-independence: at least 2 categorical variables
    #                                    AND all expected counts in contingency table >= 5
    if len(cat_cols) >= 2:
        from itertools import combinations as _combinations
        for (col_a, _), (col_b, _) in _combinations(cat_cols, 2):
            try:
                ct = pd.crosstab(df[col_a], df[col_b])
                row_totals = ct.sum(axis=1)
                col_totals = ct.sum(axis=0)
                total = ct.values.sum()
                if total == 0:
                    continue
                expected = row_totals.values[:, None] * col_totals.values[None, :] / total
                if (expected >= 5).all():
                    tags["good-for-chi-square-independence"] = True
                    break
            except Exception:
                pass

    # good-for-paired-t-test: exactly 2 numeric columns AND n >= 10
    #                          AND (difference is approximately-normal OR n >= 30)
    if len(num_cols) == 2 and n >= 10:
        try:
            pair_df = df[num_cols].dropna()
            diff = (pair_df.iloc[:, 0] - pair_df.iloc[:, 1]).to_numpy(dtype=float)
            diff_normal = check_approximately_normal(diff)
            if diff_normal or n >= 30:
                tags["good-for-paired-t-test"] = True
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

    data["anova_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python anova.py all")
        print("  python anova.py usda-milk-production.yml")
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

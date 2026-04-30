#!/usr/bin/env python3
"""
Regression Quality Tagger

Tags:
  * good-linear-fit:        ANY pair has linear regression R² > 0.7
  * moderate-linear-fit:    ANY pair has 0.4 < R² <= 0.7
  * poor-linear-fit:        ALL pairs have R² <= 0.4
  * has-influential-points: ANY pair has at least one point with Cook's distance > 4/n
  * regression-appropriate: ANY pair has R² > 0.3 AND no extreme violations in residual plot
                            (extreme violation = |Pearson r(fitted, |residuals|)| > 0.3)

Usage:
    python regressionquality.py all
    python regressionquality.py usda-milk-production.yml
"""

from __future__ import annotations

import io
import sys
import zipfile
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml
from scipy.stats import pearsonr


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


def check_pair(x: np.ndarray, y: np.ndarray) -> dict[str, bool | float] | None:
    """Compute regression quality metrics for a pair of numeric columns."""
    n = len(x)
    if n < 3:
        return None

    linear_r2: float | None = None
    has_influential = False
    regression_appropriate = False

    # linear_r2
    try:
        coeffs = np.polyfit(x, y, 1)
        y_pred = np.polyval(coeffs, x)
        residuals = y - y_pred
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        linear_r2 = 1.0 if ss_tot == 0 else 1 - ss_res / ss_tot
    except Exception:
        pass

    # has-influential-points: Cook's distance > 4/n
    try:
        if linear_r2 is not None and n > 2:
            x_mean = float(np.mean(x))
            x_ss = float(np.sum((x - x_mean) ** 2))
            if x_ss > 0:
                h = 1 / n + (x - x_mean) ** 2 / x_ss
                h = np.clip(h, 0, 0.9999)
                mse = ss_res / (n - 2)
                if mse > 0:
                    cooks_d = (residuals ** 2 / (2 * mse)) * (h / (1 - h) ** 2)
                    has_influential = bool(np.any(cooks_d > 4 / n))
    except Exception:
        pass

    # regression-appropriate: R² > 0.3 AND no extreme violations in residual plot
    # extreme violation defined as |Pearson r(fitted, |residuals|)| > 0.3
    try:
        if linear_r2 is not None and linear_r2 > 0.3:
            r_hetero, _ = pearsonr(y_pred, np.abs(residuals))
            no_extreme_violations = abs(r_hetero) <= 0.3
            regression_appropriate = no_extreme_violations
    except Exception:
        pass

    return {
        "linear_r2": linear_r2,
        "has_influential": has_influential,
        "regression_appropriate": regression_appropriate,
    }


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Analyze ALL pairs of numeric columns. Tags are true if ANY pair meets criteria,
    except poor-linear-fit which requires ALL pairs to have R² <= 0.4."""
    tags: dict[str, bool] = {
        "good-linear-fit": False,
        "moderate-linear-fit": False,
        "poor-linear-fit": True,  # starts True; flipped False if any pair has R² > 0.4
        "has-influential-points": False,
        "regression-appropriate": False,
    }

    numeric_cols = list(df.select_dtypes(include=["number"]).columns)
    if len(numeric_cols) < 2:
        tags["poor-linear-fit"] = False
        return tags

    for col_a, col_b in combinations(numeric_cols, 2):
        pair_df = df[[col_a, col_b]].dropna()
        if len(pair_df) < 3:
            continue

        x = pair_df[col_a].to_numpy(dtype=float)
        y = pair_df[col_b].to_numpy(dtype=float)

        result = check_pair(x, y)
        if result is None:
            continue

        r2 = result["linear_r2"]

        if r2 is not None:
            # good-linear-fit
            if r2 > 0.7:
                tags["good-linear-fit"] = True

            # moderate-linear-fit
            if 0.4 < r2 <= 0.7:
                tags["moderate-linear-fit"] = True

            # poor-linear-fit
            if r2 > 0.4:
                tags["poor-linear-fit"] = False

        # has-influential-points
        if result["has_influential"]:
            tags["has-influential-points"] = True

        # regression-appropriate
        if result["regression_appropriate"]:
            tags["regression-appropriate"] = True

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
    pairs = len(numeric_cols) * (len(numeric_cols) - 1) // 2
    print(f"  - Numeric columns: {len(numeric_cols)}, Pairs: {pairs}, Rows: {len(df)}")

    tags = infer_tags(df)
    matched = [tag for tag, val in tags.items() if val]
    print(f"  - Tags matched: {matched}")

    if dry_run:
        return

    data["regression_quality_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python regressionquality.py all")
        print("  python regressionquality.py usda-milk-production.yml")
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

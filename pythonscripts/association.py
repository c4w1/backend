#!/usr/bin/env python3
"""
Association Tagger

Tags:
  Correlation strength (any pair of numeric columns):
  * positive-correlation:  ANY pair has Pearson r > 0.1
  * negative-correlation:  ANY pair has Pearson r < -0.1
  * no-correlation:        ALL pairs have |r| <= 0.1
  * strong-correlation:    ANY pair has |r| > 0.7
  * moderate-correlation:  ANY pair has 0.3 < |r| <= 0.7
  * weak-correlation:      ANY pair has 0.1 < |r| <= 0.3

  Relationship type (any pair of numeric columns):
  * linear-relationship:    ANY pair has linear R2 > 0.5 AND (linear R2 / polynomial R2) > 0.9
  * nonlinear-relationship: ANY pair has polynomial R2 (degree 2) - linear R2 > 0.15
  * curved-relationship:    ANY pair has quadratic R2 > linear R2 by at least 0.15
  * exponential-pattern:    ANY pair has exponential R2 > linear R2 + 0.15

Usage:
    python association.py all
    python association.py usda-milk-production.yml
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


def r_squared(y_actual: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = np.sum((y_actual - y_pred) ** 2)
    ss_tot = np.sum((y_actual - np.mean(y_actual)) ** 2)
    if ss_tot == 0:
        return 1.0
    return float(1 - ss_res / ss_tot)


def check_pair(x: np.ndarray, y: np.ndarray) -> dict[str, float | None] | None:
    """Compute correlation and regression metrics for a pair of numeric columns."""
    if len(x) < 3:
        return None

    r: float | None = None
    linear_r2: float | None = None
    poly_r2: float | None = None
    exp_r2: float | None = None

    # r
    try:
        r_val, _ = pearsonr(x, y)
        r = float(r_val)
        # linear_r2
        linear_r2 = r ** 2
    except Exception:
        pass

    # poly_r2
    try:
        best_poly = None
        for x_dir, y_dir in [(x, y), (y, x)]:
            coeffs = np.polyfit(x_dir, y_dir, 2)
            y_pred = np.polyval(coeffs, x_dir)
            val = r_squared(y_dir, y_pred)
            if best_poly is None or val > best_poly:
                best_poly = val
        poly_r2 = best_poly
    except Exception:
        pass

    # exp_r2
    try:
        best_exp = None
        for x_dir, y_dir in [(x, y), (y, x)]:
            if np.all(y_dir > 0):
                log_y = np.log(y_dir)
                coeffs = np.polyfit(x_dir, log_y, 1)
                log_y_pred = np.polyval(coeffs, x_dir)
                val = r_squared(log_y, log_y_pred)
                if best_exp is None or val > best_exp:
                    best_exp = val
        exp_r2 = best_exp
    except Exception:
        pass

    return {"r": r, "linear_r2": linear_r2, "poly_r2": poly_r2, "exp_r2": exp_r2}


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Analyze ALL pairs of numeric columns. Tags are true if ANY pair meets criteria,
    except no-correlation which requires ALL pairs to have |r| <= 0.1."""
    tags: dict[str, bool] = {
        "positive-correlation": False,
        "negative-correlation": False,
        "no-correlation": True,  # starts True; flipped False if any pair has |r| > 0.1
        "strong-correlation": False,
        "moderate-correlation": False,
        "weak-correlation": False,
        "linear-relationship": False,
        "nonlinear-relationship": False,
        "curved-relationship": False,
        "exponential-pattern": False,
    }

    numeric_cols = list(df.select_dtypes(include=["number"]).columns)
    if len(numeric_cols) < 2:
        tags["no-correlation"] = False
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

        r = result["r"]
        linear_r2 = result["linear_r2"]
        poly_r2 = result["poly_r2"]
        exp_r2 = result["exp_r2"]

        if r is not None:
            # positive-correlation
            if r > 0.1:
                tags["positive-correlation"] = True

            # negative-correlation
            if r < -0.1:
                tags["negative-correlation"] = True

            # no-correlation
            if abs(r) > 0.1:
                tags["no-correlation"] = False

            # strong-correlation
            if abs(r) > 0.7:
                tags["strong-correlation"] = True

            # moderate-correlation
            if 0.3 < abs(r) <= 0.7:
                tags["moderate-correlation"] = True

            # weak-correlation
            if 0.1 < abs(r) <= 0.3:
                tags["weak-correlation"] = True

        if linear_r2 is not None and poly_r2 is not None:
            # linear-relationship
            if linear_r2 > 0.5 and poly_r2 > 0 and (linear_r2 / poly_r2) > 0.9:
                tags["linear-relationship"] = True

            # nonlinear-relationship
            if poly_r2 - linear_r2 > 0.15:
                tags["nonlinear-relationship"] = True

            # curved-relationship
            if poly_r2 - linear_r2 >= 0.15:
                tags["curved-relationship"] = True

        if linear_r2 is not None and exp_r2 is not None:
            # exponential-pattern
            if exp_r2 > linear_r2 + 0.15:
                tags["exponential-pattern"] = True

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

    data["association_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python association.py all")
        print("  python association.py usda-milk-production.yml")
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

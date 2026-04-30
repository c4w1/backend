#!/usr/bin/env python3
"""
Distribution Shape Tagger

Tags:
  * symmetric-distribution:     ANY numeric column has |skewness| < 0.5
  * right-skewed-distribution:  ANY numeric column has skewness > 0.5
  * left-skewed-distribution:   ANY numeric column has skewness < -0.5
  * approximately-normal:       ANY numeric column has Shapiro-Wilk p > 0.05
                                OR (|skewness| < 0.5 AND 2 < kurtosis < 4)
  * uniform-distribution:       ANY numeric column has std(bin counts) / mean(bin counts) < 0.3
  * bimodal-distribution:       ANY numeric column shows 2 peaks in kernel density estimate OR histogram

Usage:
    python distributionshape.py all
    python distributionshape.py usda-milk-production.yml
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
from scipy import stats
from scipy.signal import find_peaks
from scipy.stats import gaussian_kde


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
    """Compute distribution shape results for a single numeric column."""
    values = series.dropna().to_numpy(dtype=float)
    if len(values) == 0:
        return None

    skewness: float | None = None
    kurt: float | None = None
    sw_p: float = 0.0
    counts = None
    uniform = False
    bimodal = False

    # symmetric-distribution: |skewness| < 0.5
    # right-skewed-distribution: skewness > 0.5
    # left-skewed-distribution: skewness < -0.5
    # approximately-normal (partial): |skewness| < 0.5
    try:
        skewness = float(stats.skew(values))
    except Exception:
        pass

    # approximately-normal (partial): 2 < kurtosis < 4
    # Pearson kurtosis: normal distribution = 3, so range 2-4 is approximately normal
    try:
        kurt = float(stats.kurtosis(values, fisher=False))
    except Exception:
        pass

    # approximately-normal: Shapiro-Wilk p-value > 0.05
    try:
        _, sw_p = stats.shapiro(values)
    except Exception:
        pass

    # uniform-distribution: std dev of histogram bin counts / mean of bin counts < 0.3
    # counts also reused for bimodal-distribution (histogram peaks) below
    try:
        n_bins = min(max(10, len(values) // 10), 50)
        counts, _ = np.histogram(values, bins=n_bins)
        mean_counts = float(np.mean(counts))
        if mean_counts > 0:
            uniform = bool(counts.std() / mean_counts < 0.3)
    except Exception:
        counts = None

    # bimodal-distribution: 2 peaks in kernel density estimate
    try:
        kde = gaussian_kde(values)
        x = np.linspace(values.min(), values.max(), 200)
        density = kde(x)
        kde_peaks, _ = find_peaks(density)
        if len(kde_peaks) == 2:
            bimodal = True
    except Exception:
        pass

    # bimodal-distribution: 2 peaks in histogram (if KDE did not already match)
    if not bimodal and counts is not None:
        hist_peaks, _ = find_peaks(counts)
        if len(hist_peaks) == 2:
            bimodal = True

    return {
        # symmetric-distribution: ANY numeric column has |skewness| < 0.5
        "symmetric": skewness is not None and abs(skewness) < 0.5,
        # right-skewed-distribution: ANY numeric column has skewness > 0.5
        "right_skewed": skewness is not None and skewness > 0.5,
        # left-skewed-distribution: ANY numeric column has skewness < -0.5
        "left_skewed": skewness is not None and skewness < -0.5,
        # approximately-normal: Shapiro-Wilk p-value > 0.05 OR (|skewness| < 0.5 AND 2 < kurtosis < 4)
        "approximately_normal": (
            sw_p > 0.05
            or (skewness is not None and kurt is not None and abs(skewness) < 0.5 and 2 < kurt < 4)
        ),
        # uniform-distribution: std dev of histogram bin counts / mean of bin counts < 0.3
        "uniform": uniform,
        # bimodal-distribution: 2 peaks in kernel density estimate OR histogram
        "bimodal": bimodal,
    }


def infer_tags(df: pd.DataFrame) -> dict[str, bool]:
    """Analyze ALL numeric columns; a tag is true if ANY column meets the criteria."""
    tags: dict[str, bool] = {
        "symmetric-distribution": False,
        "right-skewed-distribution": False,
        "left-skewed-distribution": False,
        "approximately-normal": False,
        "uniform-distribution": False,
        "bimodal-distribution": False,
    }

    numeric_cols = df.select_dtypes(include=["number"]).columns
#change the false values to True if the condition is met
    for col in numeric_cols:
        result = check_column(df[col])
        if result is None:
            continue
        if result["symmetric"]:
            tags["symmetric-distribution"] = True
        if result["right_skewed"]:
            tags["right-skewed-distribution"] = True
        if result["left_skewed"]:
            tags["left-skewed-distribution"] = True
        if result["approximately_normal"]:
            tags["approximately-normal"] = True
        if result["uniform"]:
            tags["uniform-distribution"] = True
        if result["bimodal"]:
            tags["bimodal-distribution"] = True

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

    data["distribution_shape_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python distributionshape.py all")
        print("  python distributionshape.py usda-milk-production.yml")
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

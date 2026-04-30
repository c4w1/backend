#!/usr/bin/env python3
"""
YML File Variable Extractor Script

Reads a YML file from data/sources, and looks for the download.url field, downloads
the data file, analyzes the columns are writes two new fields to the YML file:
    variable_names - list of column names for frontend
    variable_report - more in depth analysis of each column for backend analysis for additional labeling

Usage:
  python variableextraction.py all - processes all YML files in data/sources
  python variableextraction.py usda-milk-production.yml - processes one specific YML file
  you do not need to specify the full path unless the file is not in data/sources

Requirements:
  ******pip install openpyxl pyyaml*****
  openpyxl is needed in order to support .xlsx files
  pyyaml is needed in order to read and write the YML files
  
"""

from __future__ import annotations

import csv
import glob
import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"


# download the file from the URL
def download_bytes(url: str) -> bytes:
    print(f"  Downloading: {url}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}) #to prevent error from being a bot
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
        print(f"  Downloaded {len(data):,} bytes")
        return data
    except Exception as e:
        raise RuntimeError(f"Download failed: {e}")


#parsing the data file CSV, TSV, XLSX, ZIP are supported, if ZIP it will look for the other file types inside
def is_zip(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def is_xlsx(data: bytes) -> bool:
    if not is_zip(data):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "xl/workbook.xml" in z.namelist()
    except Exception:
        return False


def pick_file_from_zip(data: bytes, preferred_exts: tuple = (".csv", ".tsv", ".xlsx")) -> tuple[bytes, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        print(f"  ZIP contents: {names}")
        for ext in preferred_exts:
            candidates = [
                n for n in names
                if n.lower().endswith(ext) and not os.path.basename(n).startswith(".")
            ]
            if candidates:
                chosen = candidates[0]
                print(f"  Extracting: {chosen}")
                return z.read(chosen), chosen
        raise RuntimeError(f"ZIP has no supported file. Contents: {names}")


def read_xlsx(data: bytes) -> tuple[list, list]:
    if not OPENPYXL_AVAILABLE:
        raise RuntimeError("openpyxl is required for .xlsx files. Run: pip install openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    print(f"  Reading sheet: '{ws.title}'")
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append([("" if v is None else str(v)) for v in row])
    wb.close()
    if not rows:
        raise RuntimeError("Sheet is empty.")
    return rows[0], rows[1:]


def detect_delimiter(text: str) -> str:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;")
        return dialect.delimiter
    except csv.Error:
        pass
    candidates = [",", "\t", ";", "|"]
    lines = [ln for ln in text.splitlines() if ln.strip()][:5]
    if not lines:
        return ","
    scores = {}
    for delim in candidates:
        counts = [line.count(delim) for line in lines]
        if all(c == counts[0] for c in counts) and counts[0] > 0:
            scores[delim] = counts[0]
    if scores:
        best = max(scores, key=lambda k: scores[k])
        print(f"  Delimiter detected (fallback): {repr(best)}")
        return best
    print("  Warning: could not detect delimiter, defaulting to comma.")
    return ","


def read_csv_bytes(data: bytes) -> tuple[list, list]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    delimiter = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = list(reader)
    while rows and all(c.strip() == "" for c in rows[-1]):
        rows.pop()
    if not rows:
        raise RuntimeError("File appears to be empty.")
    return rows[0], rows[1:]


def load_data(url: str) -> tuple[list, list]:
    raw = download_bytes(url)
    filename = url.split("?")[0].lower()
    if is_zip(raw) and not is_xlsx(raw):
        raw, filename = pick_file_from_zip(raw)
    if filename.endswith(".xlsx") or is_xlsx(raw):
        print("  Format: XLSX")
        return read_xlsx(raw)
    print("  Format: CSV/TSV")
    return read_csv_bytes(raw)


#After parsing the data, analyze the columns to create the variable report
def infer_type(values: list) -> str:
    non_empty = [v for v in values if str(v).strip() != ""]
    if not non_empty:
        return "empty"
    try:
        [int(str(v).replace(",", "")) for v in non_empty]
        return "integer"
    except ValueError:
        pass
    try:
        [float(str(v).replace(",", "")) for v in non_empty]
        return "float"
    except ValueError:
        pass
    bool_vals = {"true", "false", "yes", "no", "1", "0"}
    if all(str(v).strip().lower() in bool_vals for v in non_empty):
        return "boolean"
    return "string"


def analyze_columns(headers: list, data_rows: list, sample_size: int = 5) -> tuple[list, list]:
    """
    Returns:
        variable_names  — plain list of column name strings
        variable_report — list of dicts with per-column stats
    """
    row_count = len(data_rows)
    variable_names = []
    variable_report = []

    for col_idx, col_name in enumerate(headers):
        col_name = str(col_name).strip()
        if not col_name:
            continue

        values = [row[col_idx] if col_idx < len(row) else "" for row in data_rows]
        non_empty = [v for v in values if str(v).strip() != ""]
        unique_vals = list(dict.fromkeys(non_empty))
        col_type = infer_type(values)

        variable_names.append(col_name)

        report_entry = {
            "name": col_name,
            "type": col_type,
            "total_rows": row_count,
            "non_empty_count": len(non_empty),
            "missing_count": row_count - len(non_empty),
            "missing_pct": round((row_count - len(non_empty)) / row_count * 100, 2) if row_count else 0,
            "unique_count": len(unique_vals),
            "sample_values": unique_vals[:sample_size],
        }

        if col_type in ("integer", "float"):
            try:
                nums = [float(str(v).replace(",", "")) for v in non_empty]
                report_entry["min"] = min(nums)
                report_entry["max"] = max(nums)
                report_entry["mean"] = round(sum(nums) / len(nums), 4)
            except Exception:
                pass

        variable_report.append(report_entry)

    return variable_names, variable_report


#Write to the YML file
def load_yml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yml(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


#Process the YML file
def process_yml(yml_path: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"Processing: {yml_path}")
    print("=" * 60)

    doc = load_yml(yml_path)

    download_section = doc.get("download") or {}
    download_url = download_section.get("url") if isinstance(download_section, dict) else None

    if not download_url:
        print("  SKIPPED — no download.url field found.")
        return

    try:
        headers, data_rows = load_data(download_url)
    except RuntimeError as e:
        print(f"  ERROR loading data: {e}")
        return

    variable_names, variable_report = analyze_columns(headers, data_rows)

    doc["variable_names"] = variable_names
    doc["variable_report"] = variable_report

    save_yml(yml_path, doc)

    print(f"  Columns found : {len(variable_names)}")
    print(f"  Rows analyzed : {len(data_rows):,}")
    print(f"  YML updated   : {yml_path}")


def main() -> None:
    if not YAML_AVAILABLE:
        print("Error: pyyaml is not installed. Run: pip install pyyaml")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python variableextraction.py all")
        print("  python variableextraction.py usda-milk-production.yml")
        sys.exit(1)

    target = sys.argv[1]

#accept command line argument for all files or just one
    if target == "all":
        yml_files = sorted(
            glob.glob(str(DEFAULT_SOURCES_DIR / "*.yml")) +
            glob.glob(str(DEFAULT_SOURCES_DIR / "*.yaml"))
        )
        if not yml_files:
            print(f"No .yml files found in '{DEFAULT_SOURCES_DIR}'.")
            sys.exit(0)
        print(f"Found {len(yml_files)} YML file(s) in '{DEFAULT_SOURCES_DIR}'")
        for yml_path in yml_files:
            process_yml(yml_path)
    else:
        
        target_path = Path(target)
        if not target_path.is_absolute() and not target_path.exists():
            target_path = DEFAULT_SOURCES_DIR / target_path
        if not target_path.is_file():
            print(f"Error: '{target_path}' is not a file.")
            sys.exit(1)
        process_yml(str(target_path))

    print("\nDone.")


if __name__ == "__main__":
    main()

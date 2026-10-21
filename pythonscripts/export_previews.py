#!/usr/bin/env python3
"""
Write static Tier 1 preview files for every catalog source with a downloadable file.

The website's Data Preview page reads these instead of a preview server:

    data/exports/previews/index.json                 every source: previewed or why not
    data/exports/previews/<id>/manifest.json         what is available for one source
    data/exports/previews/<id>/variables.json        column names (and codes) with types
    data/exports/previews/<id>/sample.json           the first rows (up to 100)
    data/exports/previews/<id>/summary.json          per-column statistics over all rows
    data/exports/previews/<id>/county/<FIPS>.json    every row for one county, when the
                                                     data has a county column

Row files hold arrays in variables.json column order. Every file stays under
MAX_FILE_BYTES; a sample shrinks to fit, and a county slice that does not fit
is cut short and marked "truncated" (the source then counts as a Tier 2 candidate).

Sources come from the SQLite export (data/exports/catalog.json). Downloads are
cached in data/.cache/downloads/ and re-used until the URL changes or --refresh
is passed. County columns are detected from the data:
  1. a column named like "FIPS" holding 5-digit county codes,
  2. Census-style state and county code columns (STATE + COUNTY),
  3. a county name column plus a two-letter state column, matched by name
     against data/reference/geo-vocabulary.json.

Standard library only, so it runs anywhere Python does.

Usage:
    python pythonscripts/export_previews.py              # all sources
    python pythonscripts/export_previews.py <id> [<id>]  # only these sources
    python pythonscripts/export_previews.py --offline    # use cached downloads only
    python pythonscripts/export_previews.py --refresh    # download again
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
import statistics
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_CATALOG = ROOT / "data" / "exports" / "catalog.json"
DEFAULT_OUT_DIR = ROOT / "data" / "exports" / "previews"
DEFAULT_CACHE_DIR = ROOT / "data" / ".cache" / "downloads"
DEFAULT_VOCABULARY = ROOT / "data" / "reference" / "geo-vocabulary.json"

PREVIEW_VERSION = 1
# The website serves these files as-is; keep each one small.
MAX_FILE_BYTES = 250_000
SAMPLE_ROWS = 100
TOP_VALUES = 10
# Distinct values tracked per column before the count is reported as a lower bound.
UNIQUE_CAP = 10_000
# Source files larger than this are Tier 2 candidates (query the full file in the browser).
TIER2_SOURCE_BYTES = 5_000_000
# Share of rows that must resolve to a county before a column counts as the county column.
COUNTY_MATCH_SHARE = 0.8
DOWNLOAD_TIMEOUT_SECONDS = 300
USER_AGENT = "community-data-libraries-preview-export/1.0 (+https://github.com/community-data-libraries)"

XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class UnsupportedFormat(Exception):
    """The download is not a CSV, a zip with a CSV inside, or an .xlsx workbook."""


# ── Downloads ────────────────────────────────────────────────────────────────


def cache_path(cache_dir: Path, source_id: str, url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return cache_dir / f"{source_id}-{digest}.bin"


def fetch(url: str, dest: Path, *, offline: bool, refresh: bool) -> Path:
    if dest.exists() and not refresh:
        return dest
    if offline:
        raise FileNotFoundError(f"not cached (offline): {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    partial = dest.with_suffix(".part")
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    partial.replace(dest)
    return dest


# ── Reading tables ───────────────────────────────────────────────────────────


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


def read_csv_text(text: str) -> tuple[list[str], list[str] | None, list[list[str]]]:
    """Header, optional row of variable codes under it, and data rows (all strings)."""
    rows = [[clean(v) for v in row] for row in csv.reader(io.StringIO(text))]
    rows = [row for row in rows if any(row)]
    if not rows:
        return [], None, []
    header, body = rows[0], rows[1:]
    codes = None
    # Some files (County Health Rankings) put machine-readable variable codes in a second header row.
    if body and looks_like_code_row(header, body[0]):
        codes, body = body[0], body[1:]
    width = len(header)
    return header, codes, [pad(row, width) for row in body]


def looks_like_code_row(header: list[str], row: list[str]) -> bool:
    filled = [v for v in row if v]
    if len(filled) < max(2, len(header) // 2):
        return False
    return all(CODE_PATTERN.match(v) for v in filled) and row != header


def pad(row: list[str], width: int) -> list[str]:
    return (row + [""] * width)[:width]


def column_index(ref: str) -> int:
    """'C7' -> 2."""
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def format_number(text: str) -> str:
    """Excel stores 78.9 as '78.900000000000006'; show the shortest form."""
    try:
        number = float(text)
    except ValueError:
        return text
    if math.isfinite(number) and number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return repr(number)


def read_xlsx_grid(data: bytes) -> list[list[str]]:
    """First worksheet as a grid of strings, with merged cells filled across."""
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            for item in ET.fromstring(book.read("xl/sharedStrings.xml")).findall("m:si", XLSX_NS):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{XLSX_NS['m']}}}t")))
        sheet = sorted(n for n in book.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
        root = ET.fromstring(book.read(sheet))

    cells: dict[tuple[int, int], str] = {}
    for row in root.findall(".//m:sheetData/m:row", XLSX_NS):
        r = int(row.get("r")) - 1
        for cell in row.findall("m:c", XLSX_NS):
            kind = cell.get("t")
            raw = cell.find("m:v", XLSX_NS)
            value = raw.text if raw is not None and raw.text is not None else ""
            if kind == "s" and value:
                value = shared[int(value)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{{{XLSX_NS['m']}}}t"))
            elif kind in (None, "n") and value:
                value = format_number(value)
            cells[(r, column_index(cell.get("r")))] = " ".join(clean(value).split())

    for merge in root.findall(".//m:mergeCells/m:mergeCell", XLSX_NS):
        first, last = merge.get("ref").split(":")
        r1, r2 = int(re.sub(r"[A-Z]", "", first)) - 1, int(re.sub(r"[A-Z]", "", last)) - 1
        c1, c2 = column_index(first), column_index(last)
        value = cells.get((r1, c1), "")
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1) and r == r1:
                    cells[(r, c)] = value

    if not cells:
        return []
    height = max(r for r, _ in cells) + 1
    width = max(c for _, c in cells) + 1
    return [[cells.get((r, c), "") for c in range(width)] for r in range(height)]


def is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def read_xlsx(data: bytes) -> tuple[list[str], None, list[list[str]]]:
    """
    Census-style tables: title rows, then one or more header rows (merged across
    columns), then data rows, then footnotes. The first row whose other cells are
    mostly numbers starts the data; the filled rows above it are the header.
    """
    grid = read_xlsx_grid(data)
    start = None
    for i, row in enumerate(grid):
        rest = [v for v in row[1:] if v]
        # A data row has a label in its first cell and mostly numbers after it
        # (a header row of years has no label, so it is not mistaken for data).
        if row and row[0] and len(rest) >= max(1, (len(row) - 1) / 2) and sum(is_number(v) for v in rest) >= len(rest) * 0.6:
            start = i
            break
    if start is None:
        raise UnsupportedFormat("no numeric table found in the first worksheet")

    header_rows = []
    for row in reversed(grid[:start]):
        if len({v for v in row if v}) <= 1 and header_rows:
            break  # a title row (one value, possibly merged across the width) above the header block
        if any(row):
            header_rows.insert(0, row)
    width = len(grid[start])
    header = []
    for c in range(width):
        parts: list[str] = []
        for row in header_rows:
            value = row[c] if c < len(row) else ""
            if value and value not in parts:
                parts.append(value)
        header.append(" / ".join(parts) or f"Column {c + 1}")

    body = []
    for row in grid[start:]:
        if sum(1 for v in row if v) < 2:
            break  # footnotes
        body.append(pad([v.lstrip(".") if i == 0 else v for i, v in enumerate(row)], width))
    return header, None, body


def read_table(data: bytes) -> tuple[str, list[str], list[str] | None, list[list[str]]]:
    """Detect the format and return (format, header, codes, rows)."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "xl/workbook.xml" in names:
                return ("xlsx", *read_xlsx(data))
            csvs = sorted((n for n in names if n.lower().endswith((".csv", ".txt"))), key=lambda n: -archive.getinfo(n).file_size)
            if not csvs:
                kinds = ", ".join(sorted({Path(n).suffix.lower() or n for n in names}))
                raise UnsupportedFormat(f"zip contains no CSV ({kinds})")
            return ("zip/csv", *read_csv_text(decode_text(archive.read(csvs[0]))))
    head = data[:2048].lower()
    if b"<html" in head or b"<!doctype html" in head:
        raise UnsupportedFormat("the download URL returned a web page, not a data file")
    return ("csv", *read_csv_text(decode_text(data)))


# ── Columns and statistics ───────────────────────────────────────────────────


def column_type(values: Iterable[str]) -> str:
    seen = False
    for value in values:
        if not value:
            continue
        seen = True
        if not is_number(value):
            return "text"
    return "number" if seen else "empty"


def rounded(number: float) -> float | int:
    if number == int(number) and abs(number) < 1e15:
        return int(number)
    return float(f"{number:.6g}")


def summarize_column(name: str, kind: str, values: list[str], top: int) -> dict:
    filled = [v for v in values if v]
    distinct = Counter()
    for v in filled:
        if v in distinct or len(distinct) < UNIQUE_CAP:
            distinct[v] += 1
    summary: dict[str, Any] = {
        "name": name,
        "type": kind,
        "count": len(filled),
        "missing": len(values) - len(filled),
        "unique": len(distinct),
    }
    if len(distinct) >= UNIQUE_CAP:
        summary["unique_at_least"] = True
    if kind == "number" and filled:
        numbers = [float(v) for v in filled]
        summary.update(
            min=rounded(min(numbers)),
            max=rounded(max(numbers)),
            mean=rounded(statistics.fmean(numbers)),
            median=rounded(statistics.median(numbers)),
        )
    elif kind == "text" and top:
        summary["top"] = [[value, count] for value, count in sorted(distinct.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    return summary


# ── Counties ─────────────────────────────────────────────────────────────────


def normalize_header(name: str) -> str:
    """'8. ST' -> 'st', 'State FIPS Code' -> 'statefipscode'."""
    name = re.sub(r"^\s*\d+\.\s*", "", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


COUNTY_SUFFIXES = (
    " city and borough",
    " planning region",
    " census area",
    " municipality",
    " borough",
    " county",
    " parish",
)


def normalize_place(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\bsaint\b", "st", text)
    for suffix in COUNTY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return re.sub(r"[^a-z0-9]", "", text)


def load_vocabulary(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    counties = {c["geoid"]: c for c in data["counties"]}
    by_name: dict[tuple[str, str], str] = {}
    for c in data["counties"]:
        key = (c["state"], normalize_place(c["name"]))
        # "Richmond city" and "Richmond County" are different places; keep the county
        # for the bare name and let the city match "richmondcity".
        if key not in by_name or c["name"].endswith("County"):
            by_name[key] = c["geoid"]
        if c["name"].endswith(" city"):
            by_name[(c["state"], re.sub(r"[^a-z0-9]", "", c["name"].lower()))] = c["geoid"]
    return {"counties": counties, "by_name": by_name}


def detect_county(header: list[str], rows: list[list[str]], vocabulary: dict) -> dict | None:
    """
    Find how each row maps to a county. Returns
    {"method", "columns", "fips": [geoid or None per row]} or None.
    """
    if not rows:
        return None
    counties = vocabulary["counties"]
    keys = [normalize_header(h) for h in header]

    def accept(method: str, columns: list[str], fips: list[str | None]) -> dict | None:
        matched = sum(1 for f in fips if f)
        return {"method": method, "columns": columns, "fips": fips} if matched >= COUNTY_MATCH_SHARE * len(rows) else None

    # 1. A FIPS column with 5-digit county codes (state and national rows simply do not match).
    for i, key in enumerate(keys):
        if "fips" not in key:
            continue
        fips = []
        for row in rows:
            value = row[i]
            value = value.zfill(5) if value.isdigit() and len(value) in (4, 5) else ""
            fips.append(value if value in counties else None)
        if sum(1 for f in fips if f) >= 0.5 * len(rows):
            return {"method": "fips_column", "columns": [header[i]], "fips": fips}

    # 2. Census-style STATE + COUNTY code columns ("000" is the state total row).
    state_keys = ("state", "statefp", "statefips", "statecode", "statefipscode")
    county_keys = ("county", "countyfp", "countyfips", "countycode", "countyfipscode")
    s = next((i for i, k in enumerate(keys) if k in state_keys and column_type(r[i] for r in rows) == "number"), None)
    c = next((i for i, k in enumerate(keys) if k in county_keys and column_type(r[i] for r in rows) == "number"), None)
    if s is not None and c is not None:
        fips = []
        for row in rows:
            code = row[s].zfill(2) + row[c].zfill(3)
            fips.append(code if code in counties else None)
        if sum(1 for f in fips if f) >= 0.5 * len(rows):
            return {"method": "state_county_codes", "columns": [header[s], header[c]], "fips": fips}

    # 3. County names plus two-letter state codes, matched against the vocabulary.
    usps_keys = ("st", "state", "stusps", "stateabbr", "stateabbreviation", "statecode")
    s = next(
        (i for i, k in enumerate(keys) if k in usps_keys and all(len(r[i]) == 2 and r[i].isalpha() for r in rows if r[i])),
        None,
    )
    c = next(
        (i for i, k in enumerate(keys) if k in ("county", "countyname", "cntyname", "ctyname") and column_type(r[i] for r in rows) == "text"),
        None,
    )
    if s is not None and c is not None:
        by_name = vocabulary["by_name"]
        fips = [by_name.get((row[s].upper(), normalize_place(row[c]))) for row in rows]
        return accept("county_name", [header[c], header[s]], fips)
    return None


# ── Writing ──────────────────────────────────────────────────────────────────


def encode(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def fit_rows(rows: list[list[str]], wrap) -> tuple[bytes, int]:
    """Largest prefix of rows whose encoded file fits MAX_FILE_BYTES (binary search)."""
    data = encode(wrap(rows, len(rows)))
    if len(data) <= MAX_FILE_BYTES:
        return data, len(rows)
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(encode(wrap(rows[:mid], mid))) <= MAX_FILE_BYTES:
            lo = mid
        else:
            hi = mid - 1
    return encode(wrap(rows[:lo], lo)), lo


def write_file(path: Path, data: bytes) -> int:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{path} would be {len(data)} bytes (limit {MAX_FILE_BYTES})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def build_preview(source: dict, data: bytes, out_dir: Path, vocabulary: dict) -> dict:
    """Write one source's preview files and return its manifest."""
    source_id = source["id"]
    fmt, header, codes, rows = read_table(data)
    if not header or not rows:
        raise UnsupportedFormat("no rows found")

    target = out_dir / source_id
    if target.exists():
        shutil.rmtree(target)
    files: dict[str, int] = {}

    kinds = [column_type(row[i] for row in rows) for i in range(len(header))]
    variables = [
        {"name": name, **({"code": codes[i]} if codes and i < len(codes) and codes[i] else {}), "type": kinds[i]}
        for i, name in enumerate(header)
    ]
    files["variables.json"] = write_file(target / "variables.json", encode({"columns": variables}))

    summary_data = None
    for top in (TOP_VALUES, 5, 0):
        columns = [summarize_column(name, kinds[i], [row[i] for row in rows], top) for i, name in enumerate(header)]
        summary_data = encode({"rows": len(rows), "columns": columns})
        if len(summary_data) <= MAX_FILE_BYTES:
            break
    files["summary.json"] = write_file(target / "summary.json", summary_data)

    sample, sample_rows = fit_rows(
        rows[:SAMPLE_ROWS], lambda part, n: {"rows": part, "rows_included": n, "rows_total": len(rows)}
    )
    files["sample.json"] = write_file(target / "sample.json", sample)

    county_info = None
    truncated: list[str] = []
    detected = detect_county(header, rows, vocabulary)
    if detected:
        groups: dict[str, list[list[str]]] = {}
        for row, fips in zip(rows, detected["fips"]):
            if fips:
                groups.setdefault(fips, []).append(row)
        for fips in sorted(groups):
            county_rows = groups[fips]
            payload, kept = fit_rows(
                county_rows,
                lambda part, n, f=fips, total=len(county_rows): {
                    "fips": f,
                    "rows": part,
                    "rows_included": n,
                    "rows_total": total,
                    "truncated": n < total,
                },
            )
            if kept < len(county_rows):
                truncated.append(fips)
            write_file(target / "county" / f"{fips}.json", payload)
        county_info = {
            "method": detected["method"],
            "columns": detected["columns"],
            "rows_matched": sum(len(g) for g in groups.values()),
            "rows_unmatched": len(rows) - sum(len(g) for g in groups.values()),
            "counties": sorted(groups),
            "truncated": truncated,
        }

    manifest = {
        "version": PREVIEW_VERSION,
        "id": source_id,
        "title": source.get("title"),
        "download_url": source["download"]["url"],
        "format": fmt,
        "source_bytes": len(data),
        "rows": len(rows),
        "columns": len(header),
        "sample_rows": sample_rows,
        "files": files,
        "county": county_info,
        "tier2": len(data) > TIER2_SOURCE_BYTES or bool(truncated),
    }
    # Compact: every preview visit loads the manifest, and its county list can run to 3,000+ codes.
    write_file(target / "manifest.json", encode(manifest))
    return manifest


def load_sources(catalog_path: Path) -> list[dict]:
    return json.loads(catalog_path.read_text(encoding="utf-8"))["sources"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("ids", nargs="*", help="only these source ids (default: all)")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--offline", action="store_true", help="only use cached downloads")
    parser.add_argument("--refresh", action="store_true", help="download again even if cached")
    args = parser.parse_args(argv)

    sources = load_sources(args.catalog)
    known = {s["id"] for s in sources}
    unknown = [i for i in args.ids if i not in known]
    if unknown:
        print(f"Unknown source id(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    vocabulary = load_vocabulary(args.vocabulary)
    args.out.mkdir(parents=True, exist_ok=True)

    index_path = args.out / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() and args.ids else {"sources": {}}
    entries = index["sources"]
    failures = 0
    for source in sources:
        source_id = source["id"]
        if args.ids and source_id not in args.ids:
            continue
        url = (source.get("download") or {}).get("url")
        if not url:
            entries[source_id] = {"status": "none", "reason": "no direct download"}
            shutil.rmtree(args.out / source_id, ignore_errors=True)
            print(f"- {source_id}: no direct download, skipped")
            continue
        try:
            path = fetch(url, cache_path(args.cache, source_id, url), offline=args.offline, refresh=args.refresh)
            manifest = build_preview(source, path.read_bytes(), args.out, vocabulary)
        except UnsupportedFormat as err:
            entries[source_id] = {"status": "none", "reason": f"unsupported format: {err}"}
            shutil.rmtree(args.out / source_id, ignore_errors=True)
            print(f"- {source_id}: {err}")
            continue
        except Exception as err:  # noqa: BLE001 - report every source, then fail at the end
            failures += 1
            print(f"! {source_id}: {err}", file=sys.stderr)
            if source_id in entries:
                continue  # keep the last good preview
            entries[source_id] = {"status": "none", "reason": "export failed"}
            continue
        county = manifest["county"]
        entries[source_id] = {
            "status": "ok",
            "rows": manifest["rows"],
            "counties": len(county["counties"]) if county else 0,
            "tier2": manifest["tier2"],
        }
        print(
            f"+ {source_id}: {manifest['rows']} rows, {manifest['columns']} columns, sample {manifest['sample_rows']}"
            + (f", {len(county['counties'])} county files via {county['method']}" if county else ", no county column")
            + (f", {len(county['truncated'])} truncated" if county and county["truncated"] else "")
            + (" [tier2]" if manifest["tier2"] else "")
        )

    # Drop folders for sources that left the catalog.
    for stale in sorted(p for p in args.out.iterdir() if p.is_dir() and p.name not in known):
        shutil.rmtree(stale)
    index = {"version": PREVIEW_VERSION, "sources": dict(sorted((k, v) for k, v in entries.items() if k in known))}
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

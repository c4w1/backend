#!/usr/bin/env python3
"""
rules:
  who   — derived from provider.name + provider.agency
  how   — inferred from keywords in title/notes (collection method)
  where — inferred from filters + title keywords (geographic scope)
  why   — left blank (requires manual entry; too domain-specific to infer)
  when  — extracted from year patterns in title; enriched with filter.year info

Usage:
  python detect_five_ws.py                  # process all source files
  python detect_five_ws.py --dry-run        # preview without saving
  python detect_five_ws.py --file <path>    # single file
  python detect_five_ws.py --overwrite      # replace existing detected values
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

SOURCES_DIR = Path(__file__).parent.parent / "data" / "sources"

FIVE_WS = ["who", "how", "where", "why", "when"]

# Detectors 

def detect_who(data: dict) -> str:
    provider = data.get("provider") or {}
    name = (provider.get("name") or "").strip()
    agency = (provider.get("agency") or "").strip()
    if not name:
        return ""
    if agency and agency != name:
        return f"{name} ({agency})"
    return name


def detect_how(data: dict) -> str:
    title = (data.get("title") or "").lower()
    notes = (data.get("notes") or "").lower()
    desc  = (data.get("download", {}) or {}).get("description", "").lower()
    combined = f"{title} {notes} {desc}"

    
    title_notes = f"{title} {notes}"

    title_rules = [
        (r"\bcitizen sci|community report|observ", "Citizen science / community observation submissions"),
        (r"\bemission report|tri program|toxics release", "Facility-level self-reported emissions data (regulatory program)"),
        (r"\badministrative record", "Administrative records"),
        (r"\bdecennial census", "Decennial census enumeration"),
        (r"\bestimate|popest", "Statistical estimation from administrative records and surveys"),
        (r"\bsurvey", "Survey-based data collection"),
        (r"\bmonitoring", "Continuous monitoring / sensor network"),
        (r"\bexplorer\b", "Interactive web tool -- select filters then download"),
    ]

    for pattern, label in title_rules:
        if re.search(pattern, title_notes):
            return label

    
    if re.search(r"\bexplorer|interactive", desc):
        return "Interactive web tool -- select filters then download"

    return ""


def detect_where(data: dict) -> str:
    filters = data.get("filters") or {}
    title   = (data.get("title") or "").lower()

    levels = []
    if filters.get("county"):
        levels.append("county")
    if filters.get("state"):
        levels.append("state")
    if filters.get("zipcode"):
        levels.append("ZIP code")

    if levels:
        return "Filterable to " + ", ".join(levels) + " level"

    # No filter block — infer from title
    national_kw = r"\bnational\b|\bu\.s\.|\bunited states\b"
    if re.search(national_kw, title):
        return "National"
    if "state" in title:
        return "State-level"
    if "county" in title:
        return "County-level"
    return ""


def detect_when(data: dict) -> str:
    title   = (data.get("title") or "")
    dl_url  = (data.get("download", {}) or {}).get("url", "") or ""
    filters = data.get("filters") or {}

    # Extract all 4-digit years from title to find range
    year_pat = r'\b(20\d{2}|19\d{2})\b'
    all_years = re.findall(year_pat, title)

    if len(all_years) >= 2:
        result = f"{all_years[0]}-{all_years[-1]}"
    elif len(all_years) == 1:
        result = all_years[0]
    else:
        # Fall back to year in download URL
        url_match = re.search(r'(20\d{2}|19\d{2})', dl_url)
        result = url_match.group(1) if url_match else ""

    # Annotate update cadence if year-filterable
    if filters.get("year"):
        suffix = ", updated annually (filterable by year)"
        result = (result + suffix) if result else "Multiple years; filterable by year"

    return result


def detect_why(_data: dict) -> str:
    #why will always be null, needs manual entry
    return ""


DETECTORS = {
    "who":   detect_who,
    "how":   detect_how,
    "where": detect_where,
    "why":   detect_why,
    "when":  detect_when,
}


def yaml_scalar(value: str) -> str:
    """Return a safe YAML scalar for a plain string value."""
    if not value:
        return "null"
    # yaml.dump produces "<value>\n...\n" — take only the first line
    dumped = yaml.dump(value, default_flow_style=True, allow_unicode=True)
    return dumped.split("\n")[0].strip()


def process_file(filepath: Path, dry_run: bool = False, overwrite: bool = False) -> dict[str, str]:
    """
    Detect and (optionally) write 5W fields for one source YAML file.
    Returns a dict of {field: detected_value} for ALL five fields.
    """
    text = filepath.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}

    detected = {field: DETECTORS[field](data) for field in FIVE_WS}

    # Determine which fields actually need writing
    to_write: dict[str, str] = {}
    for field in FIVE_WS:
        existing = data.get(field)
        already_set = existing is not None and existing != ""
        if already_set and not overwrite:
            detected[field] = existing  # report existing value
        else:
            to_write[field] = detected[field]

    if to_write and not dry_run:
        _append_five_ws(filepath, text, data, to_write)

    return detected


def _append_five_ws(filepath: Path, original_text: str, data: dict, fields: dict[str, str]) -> None:
    """Append 5W fields to the YAML file, preserving original content."""
    has_any = any(field in data for field in FIVE_WS)

    lines: list[str] = []

    if not has_any:
        # First time — add a labeled section header
        if not original_text.endswith("\n"):
            lines.append("\n")
        lines.append("\n# ── Five Ws ──────────────────────────────────────────────────────────────────\n")

    for field in FIVE_WS:
        if field not in fields:
            continue
        value = fields[field]
        lines.append(f"{field}: {yaml_scalar(value)}\n")

    with open(filepath, "a", encoding="utf-8") as f:
        f.writelines(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def report(field: str, value: str, data: dict, overwrite: bool) -> str:
    existing = data.get(field)
    already_set = existing is not None and existing != ""
    if already_set and not overwrite:
        return f"  * {field:<6}  {existing}  (existing)"
    sym = "+" if value else "-"
    return f"  {sym} {field:<6}  {value or '(blank - manual entry needed)'}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-detect Who/How/Where/Why/When fields in CDL source YAML files."
    )
    parser.add_argument("--dry-run",   action="store_true", help="Preview changes without saving")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing detected values")
    parser.add_argument("--file",      help="Process a single file instead of all sources")
    args = parser.parse_args()

    if args.file:
        files = [Path(args.file).resolve()]
    else:
        files = sorted(SOURCES_DIR.glob("*.yml"))

    if not files:
        sys.exit(f"No .yml files found in {SOURCES_DIR}")

    tag = "[DRY RUN] " if args.dry_run else ""
    total_written = 0
    total_blank   = 0

    for filepath in files:
        text = filepath.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        print(f"\n{tag}{filepath.name}")

        detected = process_file(filepath, dry_run=args.dry_run, overwrite=args.overwrite)

        for field in FIVE_WS:
            print(report(field, detected.get(field, ""), data, args.overwrite))
            if detected.get(field):
                total_written += 1
            else:
                total_blank += 1

    print(f"\n{'-'*60}")
    action = "Would write" if args.dry_run else "Wrote"
    print(f"{action} {total_written} values  |  {total_blank} fields left blank (manual entry needed)")
    if args.dry_run:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()

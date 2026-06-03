#!/usr/bin/env python3
"""
Determines geographic coverage levels for each dataset and writes a
geographic_coverage block back to the YAML file.

Detection uses four signals, applied lowest-to-highest confidence so that
more reliable sources override less reliable ones:

  1. Title / notes keywords  (weakest — broad text matching)
  2. Provider name           (agency-level defaults from known data publishers)
  3. Download URL patterns   (Census/EPA/eBird API URL structure)
  4. variable_names columns  (actual column headers extracted by variableextraction.py)
  5. filters field           (explicit human-coded flags — authoritative for keys it covers)

Census Bureau datasets also receive url_template entries showing the Census
API URL pattern for each supported geography. Other providers use bulk
downloads without a geographic query API, so url_template is omitted for them.

Usage:
    python geographic_coverage.py all
    python geographic_coverage.py census-annual-pop-by-age-sex-2025.yml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"

# ── Level keys ───────────────────────────────────────────────────────────────

ALL_LEVELS = ["state_fips", "fips_code", "zip_code", "school_district", "tract", "tribal_area"]


def _blank_levels() -> dict[str, bool]:
    return {k: False for k in ALL_LEVELS}


# ── Provider defaults ────────────────────────────────────────────────────────
# Keyed by lowercase substring of provider.name.

PROVIDER_DEFAULTS: dict[str, dict[str, bool]] = {
    "census": {
        "state_fips": True,
        "fips_code": True,
        "zip_code": True,
        "school_district": False,
        "tract": True,
        "tribal_area": False,
    },
    "ebird": {
        "state_fips": True,
        "fips_code": True,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "epa": {
        "state_fips": True,
        "fips_code": True,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "environmental protection": {
        "state_fips": True,
        "fips_code": True,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "fbi": {
        "state_fips": True,
        "fips_code": False,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "federal bureau of investigation": {
        "state_fips": True,
        "fips_code": False,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "usda": {
        "state_fips": True,
        "fips_code": False,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "department of agriculture": {
        "state_fips": True,
        "fips_code": False,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "nces": {
        "state_fips": True,
        "fips_code": False,
        "zip_code": False,
        "school_district": True,
        "tract": False,
        "tribal_area": False,
    },
    "national center for education": {
        "state_fips": True,
        "fips_code": False,
        "zip_code": False,
        "school_district": True,
        "tract": False,
        "tribal_area": False,
    },
    "bureau of labor": {
        "state_fips": True,
        "fips_code": True,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
    "bls": {
        "state_fips": True,
        "fips_code": True,
        "zip_code": False,
        "school_district": False,
        "tract": False,
        "tribal_area": False,
    },
}

# ── Variable name patterns ───────────────────────────────────────────────────
# Column names that indicate a geographic identifier is in the data.
# Patterns are matched case-insensitively against each variable name.

VARIABLE_PATTERNS: dict[str, list[str]] = {
    "state_fips": [
        r"\bstate\b", r"\bstate_fips\b", r"\bstatefp\b", r"\bstusab\b",
        r"\bstate_code\b", r"\bstate_abbr\b", r"\bstate_id\b", r"\bsubnational1\b",
    ],
    "fips_code": [
        r"\bcounty\b", r"\bcounty_fips\b", r"\bcountyfp\b", r"\bfips\b",
        r"\bcounty_code\b", r"\bgeoid\b", r"\bcounty_id\b", r"\bsubnational2\b",
    ],
    "zip_code": [
        r"\bzip\b", r"\bzipcode\b", r"\bzip_code\b", r"\bzcta\b",
        r"\bzcta5\b", r"\bzip5\b", r"\bpostalcode\b", r"\bpostal_code\b",
    ],
    "tract": [
        r"\btract\b", r"\btractce\b", r"\btract_code\b", r"\bcensus_tract\b",
    ],
    "school_district": [
        r"\bleaid\b", r"\blea_id\b", r"\bschool_district\b",
        r"\bdistrict_id\b", r"\bnces_id\b", r"\bsdlea\b",
    ],
    "tribal_area": [
        r"\baiannhce\b", r"\btribal\b", r"\btribe\b",
        r"\breservation\b", r"\btribal_area\b",
    ],
}

# ── Title / notes keyword patterns ───────────────────────────────────────────

TITLE_PATTERNS: dict[str, list[str]] = {
    "state_fips":      [r"\bstate\b", r"\bstatewide\b", r"\bby state\b"],
    "fips_code":       [r"\bcounty\b", r"\bcounties\b", r"\bby county\b", r"\bfips\b"],
    "zip_code":        [r"\bzip\b", r"\bzip code\b", r"\bzcta\b", r"\bpostal\b"],
    "tract":           [r"\bcensus tract\b", r"\btract.level\b"],
    "school_district": [r"\bschool district\b", r"\blea\b"],
    "tribal_area":     [r"\btribal\b", r"\breservation\b", r"\bnative american\b", r"\bamerican indian\b"],
}

# ── Census API URL templates ─────────────────────────────────────────────────
# Placeholders: {year}, {dataset}, {variables}, {state_fips}, {county_fips},
# {zip_code}, {api_key} — all replaced at query time by the community library.

CENSUS_URL_TEMPLATES: dict[str, str] = {
    "state": (
        "https://api.census.gov/data/{year}/{dataset}"
        "?get={variables}&for=state:{state_fips}&key={api_key}"
    ),
    "county": (
        "https://api.census.gov/data/{year}/{dataset}"
        "?get={variables}&for=county:{county_fips}&in=state:{state_fips}&key={api_key}"
    ),
    "zip": (
        "https://api.census.gov/data/{year}/{dataset}"
        "?get={variables}&for=zip%20code%20tabulation%20area:{zip_code}&key={api_key}"
    ),
    "tract": (
        "https://api.census.gov/data/{year}/{dataset}"
        "?get={variables}&for=tract:*&in=state:{state_fips}%20county:{county_fips}&key={api_key}"
    ),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _or_merge(base: dict[str, bool], other: dict[str, bool]) -> dict[str, bool]:
    """Return a new dict where True wins for each key."""
    return {k: base.get(k, False) or other.get(k, False) for k in ALL_LEVELS}


def _provider_name(source: dict[str, Any]) -> str:
    provider = source.get("provider", {})
    if isinstance(provider, dict):
        return (provider.get("name") or "").lower()
    return str(provider).lower()


def _all_urls(source: dict[str, Any]) -> str:
    """Concatenate all URL-like strings in the source for pattern matching."""
    parts: list[str] = []
    download = source.get("download", {})
    if isinstance(download, dict):
        parts.append(download.get("url") or "")
    elif isinstance(download, str):
        parts.append(download)
    provider = source.get("provider", {})
    if isinstance(provider, dict):
        parts.append(provider.get("url") or "")
        parts.append(provider.get("downloadurl") or "")
    return " ".join(parts).lower()


# ── Detection functions ───────────────────────────────────────────────────────

def _from_title(source: dict[str, Any]) -> dict[str, bool]:
    text = " ".join([
        str(source.get("title", "")),
        str(source.get("description", "")),
        str(source.get("notes", "")),
    ]).lower()
    return {
        level: any(re.search(p, text) for p in patterns)
        for level, patterns in TITLE_PATTERNS.items()
    }


def _from_provider(source: dict[str, Any]) -> dict[str, bool] | None:
    name = _provider_name(source)
    for key, defaults in PROVIDER_DEFAULTS.items():
        if key in name:
            return dict(defaults)
    return None


def _from_url(source: dict[str, Any]) -> dict[str, bool] | None:
    url = _all_urls(source)

    if "api.census.gov" in url:
        levels = _blank_levels()
        levels["state_fips"] = True
        levels["fips_code"] = True
        levels["zip_code"] = "zip%20code" in url or "zcta" in url
        levels["tract"] = "tract" in url
        return levels

    if "aqs.epa.gov" in url or "api.epa.gov" in url or "epa.gov" in url:
        levels = _blank_levels()
        levels["state_fips"] = True
        levels["fips_code"] = True
        return levels

    if "ebird.org" in url:
        levels = _blank_levels()
        levels["state_fips"] = True
        levels["fips_code"] = True
        return levels

    return None


def _from_variables(source: dict[str, Any]) -> dict[str, bool] | None:
    var_names: list[Any] = source.get("variable_names", []) or []
    if not var_names:
        return None

    levels = _blank_levels()
    for var in var_names:
        var_lower = str(var).lower()
        for level, patterns in VARIABLE_PATTERNS.items():
            if any(re.search(p, var_lower) for p in patterns):
                levels[level] = True

    return levels


def _apply_filters_field(levels: dict[str, bool], source: dict[str, Any]) -> dict[str, bool]:
    """
    The filters field explicitly lists what the data source supports.
    Apply it as an authoritative override for any keys it mentions.
    """
    filters = source.get("filters") or {}
    result = dict(levels)

    if "state" in filters:
        result["state_fips"] = bool(filters["state"])
    if "county" in filters:
        result["fips_code"] = bool(filters["county"])

    zip_val = filters.get("zipcode") or filters.get("zip_code") or filters.get("zip")
    if any(k in filters for k in ("zipcode", "zip_code", "zip")):
        result["zip_code"] = bool(zip_val)

    if "tract" in filters:
        result["tract"] = bool(filters["tract"])
    if "school_district" in filters:
        result["school_district"] = bool(filters["school_district"])
    if "tribal_area" in filters:
        result["tribal_area"] = bool(filters["tribal_area"])

    return result


# ── URL template generation ───────────────────────────────────────────────────

def _url_templates(source: dict[str, Any], levels: dict[str, bool]) -> dict[str, str] | None:
    """Only Census Bureau datasets get url_template entries."""
    if "census" not in _provider_name(source):
        return None

    templates: dict[str, str] = {}
    if levels["state_fips"]:
        templates["state"] = CENSUS_URL_TEMPLATES["state"]
    if levels["fips_code"]:
        templates["county"] = CENSUS_URL_TEMPLATES["county"]
    if levels["zip_code"]:
        templates["zip"] = CENSUS_URL_TEMPLATES["zip"]
    if levels["tract"]:
        templates["tract"] = CENSUS_URL_TEMPLATES["tract"]

    return templates or None


# ── Main computation ──────────────────────────────────────────────────────────

def compute_geographic_coverage(source: dict[str, Any]) -> dict[str, Any]:
    # Layer 1: title keywords (weakest)
    levels = _from_title(source)

    # Layer 2: provider defaults
    provider_levels = _from_provider(source)
    if provider_levels is not None:
        levels = _or_merge(levels, provider_levels)

    # Layer 3: URL structure
    url_levels = _from_url(source)
    if url_levels is not None:
        levels = _or_merge(levels, url_levels)

    # Layer 4: variable column names (concrete evidence from the actual data)
    var_levels = _from_variables(source)
    if var_levels is not None:
        levels = _or_merge(levels, var_levels)

    # Layer 5: filters field (authoritative override for keys it explicitly covers)
    levels = _apply_filters_field(levels, source)

    coverage: dict[str, Any] = {"available_levels": levels}

    templates = _url_templates(source, levels)
    if templates:
        coverage["url_template"] = templates

    return coverage


# ── File processing ───────────────────────────────────────────────────────────

def find_yaml_files(sources_dir: Path, one_file: str | None) -> list[Path]:
    if one_file:
        file_path = Path(one_file)
        return [file_path] if file_path.exists() else []
    return sorted(sources_dir.glob("*.yml"))


def process_file(path: Path) -> None:
    print(f"\nProcessing: {path.name}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        print("  - Skipped (YAML root is not a mapping)")
        return

    coverage = compute_geographic_coverage(data)
    data["geographic_coverage"] = coverage

    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=False)

    levels = coverage["available_levels"]
    active = [k for k, v in levels.items() if v]
    has_templates = "url_template" in coverage
    print(f"  - Levels: {active if active else 'none detected'}"
          + (" + url_template" if has_templates else ""))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python geographic_coverage.py all")
        print("  python geographic_coverage.py census-annual-pop-by-age-sex-2025.yml")
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
        process_file(path)

    print(f"\nDone. {len(files)} file(s) updated.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Auto-detects community geographic identifiers from a ZIP code or county FIPS.

Uses Census Bureau APIs and OMB relationship files — no API key required.
Relationship files are downloaded once and cached in data/.cache/.

Usage:
    python community_identifiers.py --fips 17167 --name "Springfield"
    python community_identifiers.py --zip 62701 --name "Springfield"
    python community_identifiers.py --state IL --county "Sangamon" --name "Springfield"
    python community_identifiers.py --fips 17167 --name "Springfield" --output my-community.yml
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

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
COMMUNITIES_DIR = SCRIPT_DIR.parent / "data" / "communities"
CACHE_DIR = SCRIPT_DIR.parent / "data" / ".cache"

CENSUS_API_BASE = "https://api.census.gov/data"
ACS_YEAR = "2022"

# ── State lookup tables ───────────────────────────────────────────────────────

STATE_FIPS_TO_ABBR: dict[str, str] = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA",
    "08": "CO", "09": "CT", "10": "DE", "11": "DC", "12": "FL",
    "13": "GA", "15": "HI", "16": "ID", "17": "IL", "18": "IN",
    "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME",
    "24": "MD", "25": "MA", "26": "MI", "27": "MN", "28": "MS",
    "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI",
    "45": "SC", "46": "SD", "47": "TN", "48": "TX", "49": "UT",
    "50": "VT", "51": "VA", "53": "WA", "54": "WV", "55": "WI",
    "56": "WY", "72": "PR", "78": "VI",
}

STATE_ABBR_TO_FIPS: dict[str, str] = {v: k for k, v in STATE_FIPS_TO_ABBR.items()}

STATE_NAME_TO_FIPS: dict[str, str] = {
    "alabama": "01", "alaska": "02", "arizona": "04", "arkansas": "05",
    "california": "06", "colorado": "08", "connecticut": "09", "delaware": "10",
    "district of columbia": "11", "florida": "12", "georgia": "13", "hawaii": "15",
    "idaho": "16", "illinois": "17", "indiana": "18", "iowa": "19",
    "kansas": "20", "kentucky": "21", "louisiana": "22", "maine": "23",
    "maryland": "24", "massachusetts": "25", "michigan": "26", "minnesota": "27",
    "mississippi": "28", "missouri": "29", "montana": "30", "nebraska": "31",
    "nevada": "32", "new hampshire": "33", "new jersey": "34", "new mexico": "35",
    "new york": "36", "north carolina": "37", "north dakota": "38", "ohio": "39",
    "oklahoma": "40", "oregon": "41", "pennsylvania": "42", "rhode island": "44",
    "south carolina": "45", "south dakota": "46", "tennessee": "47", "texas": "48",
    "utah": "49", "vermont": "50", "virginia": "51", "washington": "53",
    "west virginia": "54", "wisconsin": "55", "wyoming": "56",
    "puerto rico": "72", "virgin islands": "78",
}

# ── Relationship file URLs (Census Bureau, no account required) ───────────────

ZCTA_COUNTY_REL_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
    "tab20_zcta520_county20_natl.txt"
)
TRACT_PUMA_REL_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/"
    "2020_Census_Tract_to_2020_PUMA.txt"
)
CBSA_DELINEATION_URL = (
    "https://www2.census.gov/programs-surveys/metro-micro/geographies/"
    "reference-files/2023/delineation-files/list1_2023.xls"
)

# Identifiers that cannot be auto-detected and require manual lookup
MANUAL_FIELDS = [
    "place_fips (Place FIPS — incorporated cities/towns only)",
    "cdp_name (Census Designated Place — unincorporated communities only)",
    "mcd_code (Minor Civil Division — used in ~20 states)",
    "community_name_official (as registered with state/county)",
    "community_name_common (names used by residents)",
    "tribal_fips (federally recognized tribes only)",
    "aiannh_code (American Indian/Alaska Native/Native Hawaiian areas only)",
    "tribal_name_official (from Federal Register)",
    "reservation_trust_land (type of tribal area)",
    "otsa_code (Oklahoma tribal jurisdictions only)",
    "tdsa_code (tribal areas without reservations only)",
    "anvsa_code (Alaska Native villages only)",
    "sdaisa_code (state-designated tribal areas only)",
    "bia_agency_code (Bureau of Indian Affairs)",
    "ihs_service_area (Indian Health Service)",
    "urban_area_code",
    "congressional_district",
    "state_legislative_district_upper",
    "state_legislative_district_lower",
    "fire_protection_district",
    "ems_district",
    "fsa_county_office_code",
    "electric_utility_territory",
    "water_district_code",
    "hrr_code (Hospital Referral Region)",
    "hsa_code (Hospital Service Area)",
    "wia_labor_market_area",
    "dma_code (Nielsen media market)",
    "noaa_climate_division",
    "public_health_region",
    "edd_code (Economic Development District)",
]


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _fetch_bytes(url: str) -> bytes:
    print(f"  Downloading: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _fetch_text(url: str) -> str:
    return _fetch_bytes(url).decode("utf-8")


def _fetch_json(url: str) -> Any:
    return json.loads(_fetch_text(url))


def _cached_text(url: str, filename: str) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / filename
    if path.exists():
        print(f"  Cache hit: {filename}")
        return path.read_text(encoding="utf-8")
    text = _fetch_text(url)
    path.write_text(text, encoding="utf-8")
    return text


def _cached_bytes(url: str, filename: str) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / filename
    if path.exists():
        print(f"  Cache hit: {filename}")
        return path.read_bytes()
    data = _fetch_bytes(url)
    path.write_bytes(data)
    return data


# ── Census API helper ─────────────────────────────────────────────────────────

def _census_api(path: str) -> list[list[str]]:
    url = f"{CENSUS_API_BASE}/{path}"
    try:
        rows = _fetch_json(url)
        return rows[1:]  # skip header row
    except Exception as exc:
        print(f"  Census API error: {exc}")
        return []


# ── Step 1: Resolve input to county FIPS ─────────────────────────────────────

def _resolve_state_fips(state: str) -> str:
    s = state.strip()
    if re.fullmatch(r"\d{2}", s):
        return s
    if s.upper() in STATE_ABBR_TO_FIPS:
        return STATE_ABBR_TO_FIPS[s.upper()]
    if s.lower() in STATE_NAME_TO_FIPS:
        return STATE_NAME_TO_FIPS[s.lower()]
    raise ValueError(f"Unknown state: {state!r}")


def _county_name_to_fips(state_fips: str, county_name: str) -> str:
    print(f"\nLooking up county '{county_name}' in state {state_fips}...")
    rows = _census_api(
        f"{ACS_YEAR}/acs/acs5?get=NAME&for=county:*&in=state:{state_fips}"
    )
    name_lower = county_name.lower().strip()
    for row in rows:
        api_name, state_col, county_col = row[0], row[1], row[2]
        if name_lower in api_name.lower():
            fips = state_col + county_col
            print(f"  Matched: {api_name} → {fips}")
            return fips
    raise ValueError(f"County '{county_name}' not found in state {state_fips}")


def _zip_to_county_fips(zip_code: str) -> str:
    """
    Finds the primary county FIPS for a ZIP using the Census ZCTA-to-county
    relationship file. When a ZIP spans multiple counties, picks the one with
    the largest land area overlap.
    """
    print(f"\nResolving ZIP {zip_code} to county FIPS...")
    text = _cached_text(ZCTA_COUNTY_REL_URL, "zcta_county_rel.txt")

    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    matches: list[tuple[str, int]] = []
    for row in reader:
        zcta = row.get("GEOID_ZCTA5_20", "").strip().zfill(5)
        if zcta == zip_code.strip().zfill(5):
            county = row.get("GEOID_COUNTY_20", "").strip().zfill(5)
            area = int(row.get("AREALAND_PART", "0") or "0")
            matches.append((county, area))

    if not matches:
        raise ValueError(f"ZIP {zip_code} not found in ZCTA-county relationship file")

    matches.sort(key=lambda x: x[1], reverse=True)
    primary = matches[0][0]

    if len(matches) > 1:
        all_counties = [m[0] for m in matches]
        print(f"  ZIP spans {len(matches)} counties. Primary (by land area): {primary}")
        print(f"  All overlapping counties: {all_counties}")
    else:
        print(f"  County FIPS: {primary}")

    return primary


def resolve_to_county_fips(args: argparse.Namespace) -> str:
    if args.fips:
        fips = args.fips.strip().zfill(5)
        if not re.fullmatch(r"\d{5}", fips):
            raise ValueError(f"Invalid county FIPS: {args.fips!r}")
        print(f"Using county FIPS: {fips}")
        return fips
    if args.zip_code:
        return _zip_to_county_fips(args.zip_code)
    if args.state and args.county:
        state_fips = _resolve_state_fips(args.state)
        return _county_name_to_fips(state_fips, args.county)
    raise ValueError("Provide --fips, --zip, or both --state and --county")


# ── Step 2: Fetch each identifier ────────────────────────────────────────────

def fetch_county_name(state_fips: str, county_fips: str) -> str:
    print(f"\nFetching county name...")
    rows = _census_api(
        f"{ACS_YEAR}/acs/acs5?get=NAME&for=county:{county_fips}&in=state:{state_fips}"
    )
    name = rows[0][0] if rows else ""
    print(f"  {name}")
    return name


def fetch_cbsa(county_geoid: str) -> dict[str, str | None]:
    """Looks up CBSA and CSA codes from the OMB delineation file."""
    empty: dict[str, str | None] = {
        "cbsa_code": None, "cbsa_name": None, "cbsa_type": None,
        "csa_code": None, "csa_name": None,
    }

    if not OPENPYXL_AVAILABLE:
        print("\nSkipping CBSA lookup — openpyxl not installed (pip install openpyxl)")
        return empty

    print(f"\nFetching CBSA for county {county_geoid}...")
    data = _cached_bytes(CBSA_DELINEATION_URL, "cbsa_delineation_2023.xls")

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    all_rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # The delineation file has 2 title rows before the column headers
    header_idx = next(
        (i for i, row in enumerate(all_rows) if any("CBSA Code" in str(c) for c in row if c)),
        None,
    )
    if header_idx is None:
        print("  Could not locate header row in CBSA delineation file")
        return empty

    headers = [str(c).strip() if c else "" for c in all_rows[header_idx]]

    def _col(row_vals: tuple, fragment: str) -> str:
        try:
            idx = next(i for i, h in enumerate(headers) if fragment.lower() in h.lower())
            v = row_vals[idx]
            if v is None:
                return ""
            if isinstance(v, float) and v.is_integer():
                return str(int(v))
            return str(v).strip()
        except StopIteration:
            return ""

    state_fips = county_geoid[:2]
    county_fips = county_geoid[2:]

    for row in all_rows[header_idx + 1:]:
        if not any(row):
            continue
        if _col(row, "FIPS State Code").zfill(2) != state_fips:
            continue
        if _col(row, "FIPS County Code").zfill(3) != county_fips:
            continue

        result: dict[str, str | None] = {
            "cbsa_code": _col(row, "CBSA Code") or None,
            "cbsa_name": _col(row, "CBSA Title") or None,
            "cbsa_type": _col(row, "Metropolitan/Micropolitan") or None,
            "csa_code": _col(row, "CSA Code") or None,
            "csa_name": _col(row, "CSA Title") or None,
        }
        print(f"  CBSA: {result['cbsa_name']} ({result['cbsa_code']}) — {result['cbsa_type']}")
        return result

    print(f"  County {county_geoid} is not in any CBSA (rural area)")
    return empty


def fetch_tracts(state_fips: str, county_fips: str) -> list[str]:
    print(f"\nFetching census tracts...")
    rows = _census_api(
        f"{ACS_YEAR}/acs/acs5?get=NAME"
        f"&for=tract:*&in=state:{state_fips}%20county:{county_fips}"
    )
    # Census API returns [NAME, state, county, tract]
    tracts = sorted(r[1] + r[2] + r[3] for r in rows)
    print(f"  Found {len(tracts)} census tracts")
    return tracts


def fetch_block_groups(state_fips: str, county_fips: str) -> list[str]:
    print(f"\nFetching block groups...")
    rows = _census_api(
        f"{ACS_YEAR}/acs/acs5?get=NAME"
        f"&for=block%20group:*&in=state:{state_fips}%20county:{county_fips}%20tract:*"
    )
    # Census API returns [NAME, state, county, tract, block_group]
    block_groups = sorted(r[1] + r[2] + r[3] + r[4] for r in rows)
    print(f"  Found {len(block_groups)} block groups")
    return block_groups


def fetch_zctas(county_geoid: str) -> list[str]:
    """Finds all ZCTAs overlapping the county from the relationship file."""
    print(f"\nFetching ZCTAs for county {county_geoid}...")
    text = _cached_text(ZCTA_COUNTY_REL_URL, "zcta_county_rel.txt")

    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    zctas: set[str] = set()
    for row in reader:
        county = row.get("GEOID_COUNTY_20", "").strip().zfill(5)
        if county == county_geoid:
            zcta = row.get("GEOID_ZCTA5_20", "").strip().zfill(5)
            if zcta:
                zctas.add(zcta)

    result = sorted(zctas)
    print(f"  Found {len(result)} ZCTAs")
    return result


def fetch_pumas(state_fips: str, county_fips: str) -> list[str]:
    """Finds all PUMAs containing tracts from this county."""
    print(f"\nFetching PUMA codes...")
    text = _cached_text(TRACT_PUMA_REL_URL, "tract_puma_rel.txt")

    reader = csv.DictReader(io.StringIO(text))
    pumas: set[str] = set()
    for row in reader:
        if row.get("STATEFP", "").zfill(2) == state_fips and \
           row.get("COUNTYFP", "").zfill(3) == county_fips:
            puma = row.get("PUMA5CE", "").strip().zfill(5)
            if puma:
                pumas.add(state_fips + puma)

    result = sorted(pumas)
    print(f"  Found {len(result)} PUMA code(s)")
    return result


def fetch_school_districts(state_fips: str) -> list[dict[str, str]]:
    """
    Fetches unified school districts for the state. The Census API does not
    support filtering school districts by county, so all state districts are
    returned — the user should filter to those serving their community.
    """
    print(f"\nFetching school districts (unified, state-wide)...")
    geo = urllib.parse.quote("school district (unified):*")
    rows = _census_api(
        f"{ACS_YEAR}/acs/acs5?get=NAME&for={geo}&in=state:{state_fips}"
    )
    # Census API returns [NAME, state, school_district_code]
    districts = sorted(
        [{"name": r[0], "geoid": r[1] + r[2]} for r in rows],
        key=lambda d: d["name"],
    )
    print(f"  Found {len(districts)} unified school district(s) in state")
    return districts


# ── Step 3: Assemble and write the community profile ─────────────────────────

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_profile(
    community_name: str,
    county_geoid: str,
    county_name: str,
    cbsa: dict[str, str | None],
    tracts: list[str],
    block_groups: list[str],
    zctas: list[str],
    pumas: list[str],
    school_districts: list[dict[str, str]],
) -> dict[str, Any]:
    state_fips = county_geoid[:2]
    county_fips = county_geoid[2:]

    return {
        "community": {
            "name": community_name,
            "state_fips": state_fips,
            "state_abbr": STATE_FIPS_TO_ABBR.get(state_fips, ""),
            "county_fips": county_fips,
            "county_geoid": county_geoid,
            "county_name": county_name,
        },
        "census_geography": {
            "geoid": county_geoid,
            "census_tracts": tracts,
            "block_groups": block_groups,
            "zcta_codes": zctas,
            "puma_codes": pumas,
        },
        "regional": {
            "cbsa_code": cbsa["cbsa_code"],
            "cbsa_name": cbsa["cbsa_name"],
            "cbsa_type": cbsa["cbsa_type"],
            "csa_code": cbsa["csa_code"],
            "csa_name": cbsa["csa_name"],
        },
        "education": {
            "unified_school_districts": school_districts,
            "note": (
                "All unified school districts in the state are listed. "
                "Verify which ones serve this community."
            ),
        },
        "auto_detected": True,
        "auto_detected_date": str(date.today()),
        "manual_fields_needed": MANUAL_FIELDS,
    }


def write_profile(profile: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    print(f"\nProfile written to: {output_path}")


def print_summary(profile: dict[str, Any]) -> None:
    c = profile["community"]
    r = profile["regional"]
    cg = profile["census_geography"]
    ed = profile["education"]

    print("\n── Auto-detected identifiers ───────────────────────────────────")
    print(f"  State FIPS:       {c['state_fips']} ({c['state_abbr']})")
    print(f"  County GEOID:     {c['county_geoid']}  ({c['county_name']})")
    if r["cbsa_code"]:
        print(f"  CBSA:             {r['cbsa_code']} — {r['cbsa_name']} ({r['cbsa_type']})")
        if r["csa_code"]:
            print(f"  CSA:              {r['csa_code']} — {r['csa_name']}")
    else:
        print(f"  CBSA:             Not in a CBSA (rural area)")
    print(f"  Census tracts:    {len(cg['census_tracts'])}")
    print(f"  Block groups:     {len(cg['block_groups'])}")
    print(f"  ZCTAs:            {len(cg['zcta_codes'])}")
    print(f"  PUMA code(s):     {', '.join(cg['puma_codes']) or 'none found'}")
    print(f"  School districts: {len(ed['unified_school_districts'])} in state (filter manually)")

    print("\n── Still needs manual entry ─────────────────────────────────────")
    for field in profile["manual_fields_needed"]:
        print(f"  - {field}")


# ── Argument parsing and main ─────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-detect community geographic identifiers."
    )
    parser.add_argument("--name", required=True, help="Community name (e.g. 'Springfield')")
    parser.add_argument("--fips", help="5-digit county FIPS code (e.g. 17167)")
    parser.add_argument("--zip", dest="zip_code", help="5-digit ZIP code")
    parser.add_argument("--state", help="State abbreviation, name, or 2-digit FIPS")
    parser.add_argument("--county", help="County name (e.g. 'Sangamon')")
    parser.add_argument("--output", help="Output YAML path (default: data/communities/<name>.yml)")
    return parser.parse_args()


def main() -> None:
    if not YAML_AVAILABLE:
        print("Error: pyyaml not installed. Run: pip install pyyaml")
        sys.exit(1)

    args = parse_args()

    county_geoid = resolve_to_county_fips(args)
    state_fips = county_geoid[:2]
    county_fips = county_geoid[2:]

    county_name = fetch_county_name(state_fips, county_fips)
    cbsa = fetch_cbsa(county_geoid)
    tracts = fetch_tracts(state_fips, county_fips)
    block_groups = fetch_block_groups(state_fips, county_fips)
    zctas = fetch_zctas(county_geoid)
    pumas = fetch_pumas(state_fips, county_fips)
    school_districts = fetch_school_districts(state_fips)

    profile = build_profile(
        community_name=args.name,
        county_geoid=county_geoid,
        county_name=county_name,
        cbsa=cbsa,
        tracts=tracts,
        block_groups=block_groups,
        zctas=zctas,
        pumas=pumas,
        school_districts=school_districts,
    )

    output_path = (
        Path(args.output) if args.output
        else COMMUNITIES_DIR / f"{_slug(args.name)}.yml"
    )
    write_profile(profile, output_path)
    print_summary(profile)


if __name__ == "__main__":
    main()

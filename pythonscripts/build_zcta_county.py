#!/usr/bin/env python3
"""
Build data/reference/zcta-county.json: the counties each ZIP Code Tabulation Area (ZCTA) lies in.

A source with `coverage: [{level: zip, zcta: "37919"}]` is matched to community libraries
through this file (export_catalog.py adds each ZCTA's counties to the catalog export). ZCTAs
are the Census Bureau's areas for ZIP codes; USPS ZIP codes are mail routes with no boundary,
so a USPS ZIP must be converted to its ZCTA before it goes in `coverage`.

Sources (U.S. Census Bureau relationship files, public domain):
  - 2020 ZCTA to 2020 county, national:
    https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt
  - 2022 Connecticut county subdivision to 2020 ZCTA. Connecticut's counties became 9 planning
    regions in 2022, and the website's county list (geo-vocabulary.json) uses the regions, so
    Connecticut ZCTAs are matched through the towns in each region:
    https://www2.census.gov/geo/docs/maps-data/data/rel2022/acs22_cousub22_zcta520_st09.txt

A ZCTA counts toward a county when that county holds at least MIN_LAND_SHARE of the ZCTA's
land area. Smaller overlaps are slivers left by boundaries that don't line up. Counties not in
geo-vocabulary.json (Puerto Rico and the island areas) are left out.

Usage:
    python build_zcta_county.py                 # download from census.gov
    python build_zcta_county.py --from <dir>    # read the two files downloaded by hand
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DEFAULT_OUT = ROOT / "data" / "reference" / "zcta-county.json"
DEFAULT_VOCABULARY = ROOT / "data" / "reference" / "geo-vocabulary.json"

NATIONAL_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt"
)
CONNECTICUT_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2022/acs22_cousub22_zcta520_st09.txt"
)
ZCTA_VINTAGE = 2020
MIN_LAND_SHARE = 0.01


def read_text(name_or_url: str, from_dir: Path | None) -> str:
    if from_dir is not None:
        return (from_dir / name_or_url.rsplit("/", 1)[-1]).read_text(encoding="utf-8-sig")
    with urllib.request.urlopen(name_or_url, timeout=300) as response:
        return response.read().decode("utf-8-sig")


def overlaps(text: str, county_of_part) -> dict[str, dict[str, tuple[int, int]]]:
    """{zcta: {county: (land_part, water_part)}} summed over the rows of one relationship file.

    `county_of_part(row)` gives the county GEOID for a row, or None to skip the row.
    """
    result: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        zcta = row["GEOID_ZCTA5_20"]
        county = county_of_part(row)
        if not zcta or not county:
            continue
        part = result[zcta][county]
        part[0] += int(row["AREALAND_PART"] or 0)
        part[1] += int(row["AREAWATER_PART"] or 0)
    return {z: {c: (p[0], p[1]) for c, p in parts.items()} for z, parts in result.items()}


def counties_by_share(parts: dict[str, tuple[int, int]], min_share: float) -> list[str]:
    """Counties holding at least `min_share` of the ZCTA, largest first.

    Shares are of land area; a ZCTA with no land (rare) uses total area instead.
    """
    land_total = sum(land for land, _ in parts.values())
    if land_total > 0:
        shares = {county: land / land_total for county, (land, _) in parts.items()}
    else:
        total = sum(land + water for land, water in parts.values()) or 1
        shares = {county: (land + water) / total for county, (land, water) in parts.items()}
    kept = [county for county, share in shares.items() if share >= min_share]
    return sorted(kept, key=lambda county: (-shares[county], county))


def build(national: str, connecticut: str, known_counties: set[str], min_share: float = MIN_LAND_SHARE) -> dict[str, list[str]]:
    by_zcta = overlaps(national, lambda row: row["GEOID_COUNTY_20"])
    # Connecticut: replace the 2020 counties with 2022 planning regions (first 5 digits of the town GEOID).
    ct = overlaps(connecticut, lambda row: row["GEOID_COUSUB_22"][:5])
    for zcta in list(by_zcta):
        if any(county.startswith("09") for county in by_zcta[zcta]):
            by_zcta[zcta] = {c: p for c, p in by_zcta[zcta].items() if not c.startswith("09")}
    for zcta, parts in ct.items():
        by_zcta.setdefault(zcta, {}).update(parts)

    result: dict[str, list[str]] = {}
    for zcta, parts in by_zcta.items():
        counties = [c for c in counties_by_share(parts, min_share) if c in known_counties]
        if counties:
            result[zcta] = counties
    return dict(sorted(result.items()))


def render(zctas: dict[str, list[str]], min_share: float = MIN_LAND_SHARE) -> str:
    """One ZCTA per line so a new vintage gives a readable diff."""
    header = {
        "vintage": ZCTA_VINTAGE,
        "minLandShare": min_share,
        "source": [NATIONAL_URL, CONNECTICUT_URL],
        "note": "Written by pythonscripts/build_zcta_county.py; don't edit by hand. "
        "Counties for each ZCTA, largest land share first.",
    }
    lines = [json.dumps(header, ensure_ascii=False)[:-1] + ',\n  "zctas": {']
    items = [f'    "{zcta}": {json.dumps(counties)}' for zcta, counties in zctas.items()]
    lines.append(",\n".join(items))
    lines.append("  }\n}\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="from_dir", type=Path, help="directory with the two files downloaded by hand")
    parser.add_argument("--vocabulary", type=Path, default=DEFAULT_VOCABULARY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    vocabulary = json.loads(args.vocabulary.read_text(encoding="utf-8"))
    known = {county["geoid"] for county in vocabulary["counties"]}
    try:
        national = read_text(NATIONAL_URL, args.from_dir)
        connecticut = read_text(CONNECTICUT_URL, args.from_dir)
    except OSError as exc:
        sys.exit(f"Could not read the Census relationship files: {exc}\nDownload them by hand and use --from <dir>.")

    zctas = build(national, connecticut, known)
    args.out.write_text(render(zctas), encoding="utf-8", newline="\n")
    print(f"Wrote {len(zctas)} ZCTAs -> {args.out}")


if __name__ == "__main__":
    main()

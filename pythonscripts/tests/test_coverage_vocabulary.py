"""Every source's coverage codes must be real Census states, counties, tracts' counties and ZCTAs.

data/reference/geo-vocabulary.json is the state and county list the website's
/maps page draws (Census cartographic boundary files, 2025). It is written by the
web repo's `npm run geo:boundaries -- --backend ../backend`; do not edit it by hand.
data/reference/zcta-county.json lists the ZCTAs and is written by build_zcta_county.py.
A tract is checked by its county (its first 5 digits).
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "data" / "sources"
VOCABULARY_PATH = ROOT / "data" / "reference" / "geo-vocabulary.json"
ZCTA_PATH = ROOT / "data" / "reference" / "zcta-county.json"


def load_vocabulary(path=VOCABULARY_PATH, zcta_path=ZCTA_PATH):
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "states": {s["geoid"] for s in data["states"]},
        "counties": {c["geoid"] for c in data["counties"]},
        "zctas": set(json.loads(zcta_path.read_text(encoding="utf-8"))["zctas"]),
    }


def unknown_coverage(coverage, vocabulary):
    """Coverage entries (YAML form: level plus fips, or zcta for zip) not in the vocabulary."""
    unknown = []
    for entry in coverage or []:
        level = entry.get("level")
        key = "zcta" if level == "zip" else "fips"
        code = str(entry.get(key, ""))
        if level == "nation":
            continue
        if level == "state" and code in vocabulary["states"]:
            continue
        if level == "county" and code in vocabulary["counties"]:
            continue
        if level == "tract" and len(code) == 11 and code[:5] in vocabulary["counties"]:
            continue
        if level == "zip" and code in vocabulary["zctas"]:
            continue
        unknown.append(f"{level} {code or f'(no {key})'}")
    return unknown


def test_vocabulary_has_states_and_counties():
    vocabulary = load_vocabulary()
    assert len(vocabulary["states"]) == 51
    assert "47093" in vocabulary["counties"]


def test_unknown_coverage_is_reported():
    vocabulary = {"states": {"47"}, "counties": {"47093"}, "zctas": {"37919"}}
    coverage = [
        {"level": "nation"},
        {"level": "state", "fips": "47"},
        {"level": "county", "fips": "47093"},
        {"level": "tract", "fips": "47093004600"},
        {"level": "zip", "zcta": "37919"},
        {"level": "state", "fips": "99"},
        {"level": "county", "fips": "09001"},
        {"level": "county"},
        {"level": "tract", "fips": "09001010100"},
        {"level": "zip", "zcta": "00601"},
        {"level": "zip"},
    ]
    assert unknown_coverage(coverage, vocabulary) == [
        "state 99",
        "county 09001",
        "county (no fips)",
        "tract 09001010100",
        "zip 00601",
        "zip (no zcta)",
    ]


@pytest.mark.parametrize("path", sorted(SOURCES_DIR.glob("*.yml")), ids=lambda p: p.name)
def test_source_coverage_uses_known_fips(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = unknown_coverage(data.get("coverage"), load_vocabulary())
    assert not unknown, f"{path.name}: coverage not in the Census vocabulary: {unknown}"

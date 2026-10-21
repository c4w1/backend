"""Every source's coverage FIPS codes must be real Census states and counties.

data/reference/geo-vocabulary.json is the state and county list the website's
/maps page draws (Census cartographic boundary files, 2025). It is written by the
web repo's `npm run geo:boundaries -- --backend ../backend`; do not edit it by hand.
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


def load_vocabulary(path=VOCABULARY_PATH):
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "states": {s["geoid"] for s in data["states"]},
        "counties": {c["geoid"] for c in data["counties"]},
    }


def unknown_coverage(coverage, vocabulary):
    """Coverage entries (YAML form: level plus optional fips) not in the vocabulary."""
    unknown = []
    for entry in coverage or []:
        level, fips = entry.get("level"), str(entry.get("fips", ""))
        if level == "nation":
            continue
        if level == "state" and fips in vocabulary["states"]:
            continue
        if level == "county" and fips in vocabulary["counties"]:
            continue
        unknown.append(f"{level} {fips or '(no fips)'}")
    return unknown


def test_vocabulary_has_states_and_counties():
    vocabulary = load_vocabulary()
    assert len(vocabulary["states"]) == 51
    assert "47093" in vocabulary["counties"]


def test_unknown_coverage_is_reported():
    vocabulary = {"states": {"47"}, "counties": {"47093"}}
    coverage = [
        {"level": "nation"},
        {"level": "state", "fips": "47"},
        {"level": "county", "fips": "47093"},
        {"level": "state", "fips": "99"},
        {"level": "county", "fips": "09001"},
        {"level": "county"},
    ]
    assert unknown_coverage(coverage, vocabulary) == ["state 99", "county 09001", "county (no fips)"]


@pytest.mark.parametrize("path", sorted(SOURCES_DIR.glob("*.yml")), ids=lambda p: p.name)
def test_source_coverage_uses_known_fips(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = unknown_coverage(data.get("coverage"), load_vocabulary())
    assert not unknown, f"{path.name}: coverage not in the Census vocabulary: {unknown}"

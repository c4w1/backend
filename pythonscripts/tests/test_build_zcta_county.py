"""Tests for build_zcta_county.py and the committed data/reference/zcta-county.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_zcta_county as build  # noqa: E402

HEADER_NATIONAL = "GEOID_ZCTA5_20|GEOID_COUNTY_20|AREALAND_PART|AREAWATER_PART"
HEADER_CT = "GEOID_COUSUB_22|GEOID_ZCTA5_20|AREALAND_PART|AREAWATER_PART"


def table(header: str, *rows: str) -> str:
    return "\n".join([header, *rows]) + "\n"


def test_a_zcta_lists_every_county_above_the_sliver_share_largest_first():
    national = table(
        HEADER_NATIONAL,
        "37801|47009|700|0",
        "37801|47123|295|0",
        "37801|47105|5|0",  # 0.5%: a sliver, dropped
        "|47093|100|0",  # county land in no ZCTA
    )
    known = {"47009", "47123", "47105", "47093"}
    assert build.build(national, table(HEADER_CT), known) == {"37801": ["47009", "47123"]}


def test_connecticut_uses_planning_regions():
    national = table(HEADER_NATIONAL, "06103|09003|100|0", "06103|09007|1|0")
    connecticut = table(HEADER_CT, "0911037070|06103|90|0", "0912012345|06103|10|0")
    known = {"09110", "09120"}
    assert build.build(national, connecticut, known) == {"06103": ["09110", "09120"]}


def test_counties_outside_the_vocabulary_are_left_out():
    national = table(HEADER_NATIONAL, "00601|72001|100|0", "96799|60010|100|0", "37919|47093|100|0")
    assert build.build(national, table(HEADER_CT), {"47093"}) == {"37919": ["47093"]}


def test_a_water_only_zcta_uses_total_area():
    assert build.counties_by_share({"a": (0, 90), "b": (0, 10)}, 0.01) == ["a", "b"]


def test_render_is_valid_json():
    text = build.render({"37919": ["47093"], "37801": ["47009", "47123"]})
    data = json.loads(text)
    assert data["zctas"]["37801"] == ["47009", "47123"]
    assert data["minLandShare"] == build.MIN_LAND_SHARE


def test_committed_crosswalk_matches_the_vocabulary():
    root = Path(__file__).resolve().parents[2]
    zctas = json.loads((root / "data" / "reference" / "zcta-county.json").read_text(encoding="utf-8"))["zctas"]
    vocabulary = json.loads((root / "data" / "reference" / "geo-vocabulary.json").read_text(encoding="utf-8"))
    counties = {c["geoid"] for c in vocabulary["counties"]}
    assert len(zctas) > 33000
    assert zctas["37919"] == ["47093"]
    assert not {c for listed in zctas.values() for c in listed} - counties
    # Connecticut is matched to its planning regions, never the old counties.
    assert all(not c.startswith("090") for listed in zctas.values() for c in listed)

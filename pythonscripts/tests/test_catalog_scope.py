"""The catalog is local data (web#42): the rule, and every source checked against it.

A source is national-level when its filters show no state, county or ZIP breakdown.
National *coverage* (a county file for every U.S. county) is fine.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_scope import FILTERS_PENDING_REVIEW, is_national_level  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "data" / "sources"


def load_sources():
    docs = (yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(SOURCES_DIR.glob("*.yml")))
    return {doc["id"]: doc for doc in docs}


def test_national_only_is_national_level():
    assert is_national_level({"year": True, "state": False, "county": False, "zipcode": False})


@pytest.mark.parametrize("level", ["state", "county", "zipcode"])
def test_any_one_local_breakdown_is_kept(level):
    filters = {"year": False, "state": False, "county": False, "zipcode": False, level: True}
    assert not is_national_level(filters)


@pytest.mark.parametrize("filters", [None, {}])
def test_missing_filters_are_refused(filters):
    with pytest.raises(ValueError, match="no filters"):
        is_national_level(filters)


@pytest.mark.parametrize("source_id,doc", sorted(load_sources().items()))
def test_source_has_filters_unless_pending_review(source_id, doc):
    assert doc.get("filters") or source_id in FILTERS_PENDING_REVIEW, f"{source_id}: add a filters block"


def test_pending_review_entries_are_still_needed():
    sources = load_sources()
    resolved = {s for s in FILTERS_PENDING_REVIEW if s not in sources or sources[s].get("filters")}
    assert not resolved, f"remove from FILTERS_PENDING_REVIEW: {', '.join(sorted(resolved))}"


@pytest.mark.parametrize("source_id,doc", sorted(load_sources().items()))
def test_granularity_agrees_with_filters(source_id, doc):
    if not doc.get("filters"):
        pytest.skip("no filters to compare")
    national = is_national_level(doc["filters"])
    assert (doc.get("geographic_granularity") == "nation") == national, (
        f"{source_id}: geographic_granularity {doc.get('geographic_granularity')!r} and filters disagree"
    )

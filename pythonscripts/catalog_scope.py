"""What the catalog leaves out: CDL is a library of local data (web#42).

A source is national-level when its hand-coded `filters` show no state, county or
ZIP breakdown. National-level sources stay in the YAML and in SQLite, so changing a
filter value reverses the decision; export_catalog.py and the preview API skip them.
lib/catalogScope.mjs is the same rule for the API and the website.

National *coverage* is a different thing: a county file for every U.S. county has
state and county filters and stays in the catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

BREAKDOWN_FILTERS = ("state", "county", "zipcode")

# These have no filters yet and stay in the catalog, flagged, until someone decides
# their values. Any other source without filters stops the import and the export.
# A test fails once an entry is no longer needed.
FILTERS_PENDING_REVIEW = {
    "knox-county-health-data",
    "knox-county-property-assessor",
    "knox-county-schools",
    "knoxville-open-data",
}


def is_national_level(filters: Mapping[str, Any] | None) -> bool:
    """True when `filters` show no local breakdown. Refuses to judge missing filters."""
    if not filters:
        raise ValueError("no filters, so the source can't be checked against the local-data rule")
    return not any(filters.get(name) for name in BREAKDOWN_FILTERS)


def missing_filters_message(source_ids: list[str]) -> str:
    return (
        f"No filters block: {', '.join(sorted(source_ids))}. Every source needs one to be checked "
        "against the local-data rule (web#42); add it to the YAML, based on the actual file."
    )

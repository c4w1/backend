"""The catalog is local data: no source may be national-only (web#42).

A source's geographic_granularity is the finest level one row describes. `nation`
means the data has no state or smaller breakdown, so it doesn't belong in the
catalog. National *coverage* (a county file for every U.S. county) is fine.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT =Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "data" / "sources"


def load_sources():
    return {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(SOURCES_DIR.glob("*.yml"))}


@pytest.mark.parametrize("name,doc", sorted(load_sources().items()))
def test_source_is_state_level_or_smaller(name, doc):
    assert doc.get("geographic_granularity") != "nation", (
        f"{name}: national-only data is out of scope; delete the YAML or record its finer granularity"
    )

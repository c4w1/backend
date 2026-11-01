"""Each source's declared download.format must match what export_previews.py detected.

The website shows download.format on the Data Preview page (web#43). The preview
exporter opens every download and records its format in previews/index.json, so a
wrong or missing declaration shows up here instead of on the site.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from import_sources_to_sqlite import DOWNLOAD_FORMATS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "data" / "sources"
INDEX_PATH = ROOT / "data" / "exports" / "previews" / "index.json"


def load_sources():
    docs = (yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(SOURCES_DIR.glob("*.yml")))
    return {doc["id"]: doc for doc in docs}


def detected_formats():
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))["sources"]
    return {source_id: entry["format"] for source_id, entry in index.items() if entry.get("format")}


@pytest.mark.parametrize("source_id,detected", sorted(detected_formats().items()))
def test_declared_format_matches_the_download(source_id, detected):
    download = load_sources()[source_id].get("download") or {}
    assert download.get("format") == detected, (
        f"{source_id}: set download.format to {detected!r} (export_previews.py detected it)"
    )


def test_detected_formats_are_in_the_vocabulary():
    unknown = {s: f for s, f in detected_formats().items() if f not in DOWNLOAD_FORMATS}
    assert not unknown, f"add to DOWNLOAD_FORMATS or fix the source: {unknown}"

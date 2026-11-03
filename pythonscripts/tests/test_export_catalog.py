"""Tests for export_catalog.py. Run: python3 -m unittest discover -s pythonscripts/tests"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from contextlib import closing
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import export_catalog  # noqa: E402
import import_sources_to_sqlite as importer  # noqa: E402

SCHEMA = SCRIPTS.parent / "database" / "schema.sql"


class ExportCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "sources.db"
        yml = self.dir / "a.yml"
        yml.write_text(textwrap.dedent("""
            id: a-source
            title: A Source
            description: Card text.
            provider: {name: Agency, url: "https://example.gov"}
            filters: {year: false, state: true, county: true, zipcode: false}
            download: {url: "https://example.gov/a.zip", format: zip/xlsx}
            sensitive: false
            student_suitability: not_suitable
            geographic_granularity: county
            coverage: [{level: county, fips: "47093"}, {level: nation}]
            location: {latitude: 35.99, longitude: -83.94, label: Knox}
            description_tags: [education, housing]
            pedagogical_tags: {civic-education: true}
        """), encoding="utf-8")
        plain = self.dir / "b.yml"
        plain.write_text("id: b-source\ntitle: B Source\nfilters: {zipcode: true}\n", encoding="utf-8")
        importer.import_files([yml, plain], self.db, SCHEMA)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_export_shape(self) -> None:
        export = export_catalog.build_export(self.db)
        self.assertEqual(export["sourceCount"], 2)
        a, b = export["sources"]
        self.assertEqual(a["id"], "a-source")
        self.assertEqual(a["coverage"], [{"level": "county", "geoid": "47093"}, {"level": "nation", "geoid": "US"}])
        self.assertEqual(a["location"], {"latitude": 35.99, "longitude": -83.94, "label": "Knox"})
        self.assertEqual(a["student_suitability"], "not_suitable")
        self.assertEqual(a["geographic_granularity"], "county")
        self.assertEqual(a["download"], {"url": "https://example.gov/a.zip", "format": "zip/xlsx"})
        self.assertNotIn("download", b)
        self.assertEqual(a["description_tags"], ["education", "housing"])
        self.assertEqual(a["analysis_tags"], {"pedagogical_tags": {"civic-education": True}})
        self.assertIs(a["sensitive"], False)
        self.assertEqual(b["student_suitability"], "unreviewed")
        self.assertNotIn("coverage", b)
        self.assertNotIn("location", b)

    def test_export_is_deterministic(self) -> None:
        first = export_catalog.render(export_catalog.build_export(self.db))
        second = export_catalog.render(export_catalog.build_export(self.db))
        self.assertEqual(first, second)
        self.assertNotIn("imported_at", first)
        json.loads(first)

    def add_source(self, body: str) -> None:
        path = self.dir / "extra.yml"
        path.write_text(body, encoding="utf-8")
        importer.import_files([path], self.db, SCHEMA)

    def test_national_level_sources_are_left_out_but_stay_in_sqlite(self) -> None:
        self.add_source("id: us-totals\ntitle: US\nfilters: {year: true, state: false, county: false, zipcode: false}\n")
        export = export_catalog.build_export(self.db)
        self.assertEqual([s["id"] for s in export["sources"]], ["a-source", "b-source"])
        self.assertEqual(export["sourceCount"], 2)
        self.assertEqual(export["excludedNationalLevel"], ["us-totals"])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT id FROM sources WHERE id = 'us-totals'").fetchone(), ("us-totals",))

    def test_a_source_without_filters_stops_the_export(self) -> None:
        self.add_source("id: no-filters\ntitle: N\n")
        with self.assertRaisesRegex(ValueError, "no-filters"):
            export_catalog.build_export(self.db)


if __name__ == "__main__":
    unittest.main()

"""Tests for import_sources_to_sqlite.py. Run: python3 -m unittest discover pythonscripts/tests"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import import_sources_to_sqlite as importer  # noqa: E402

SCHEMA = SCRIPTS.parent / "database" / "schema.sql"


class ImportSourcesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "sources.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, body: str) -> Path:
        path = self.dir / name
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def run_import(self, *paths: Path) -> sqlite3.Connection:
        importer.import_files(list(paths), self.db, SCHEMA)
        return sqlite3.connect(self.db)

    def test_legacy_yaml_imports_with_defaults(self) -> None:
        conn = self.run_import(self.write("old.yml", """
            id: old-source
            title: Old Source
            sensitive: true
        """))
        row = conn.execute(
            "SELECT student_suitability, geographic_granularity, description FROM sources"
        ).fetchone()
        self.assertEqual(row, ("unreviewed", None, None))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_coverage").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_location").fetchone()[0], 0)

    def test_new_fields_are_stored(self) -> None:
        conn = self.run_import(self.write("knox.yml", """
            id: knox-health
            title: Knox Health
            description: County health indicators.
            about: |
              ## What's Included
              - Outcomes
            published_date: 2024-09-15
            geographic_granularity: county
            student_suitability: suitable
            coverage:
              - level: nation
              - level: state
                fips: "47"
              - level: county
                fips: "47093"
            location:
              latitude: 35.99
              longitude: -83.94
              label: Knox County, TN
        """))
        row = conn.execute(
            "SELECT description, about, published_date, geographic_granularity, student_suitability"
            " FROM sources"
        ).fetchone()
        self.assertEqual(row[0], "County health indicators.")
        self.assertIn("What's Included", row[1])
        self.assertEqual(row[2:], ("2024-09-15", "county", "suitable"))
        coverage = conn.execute(
            "SELECT level, geoid FROM source_coverage ORDER BY level, geoid"
        ).fetchall()
        self.assertEqual(coverage, [("county", "47093"), ("nation", "US"), ("state", "47")])
        self.assertEqual(
            conn.execute("SELECT latitude, longitude, label FROM source_location").fetchone(),
            (35.99, -83.94, "Knox County, TN"),
        )

    def test_invalid_values_skip_the_source(self) -> None:
        bad = [
            ("bad-suit.yml", "student_suitability: maybe"),
            ("bad-gran.yml", "geographic_granularity: planet"),
            ("bad-fips.yml", "coverage:\n  - level: county\n    fips: '4793'"),
            ("bad-loc.yml", "location:\n  latitude: 200\n  longitude: 0"),
        ]
        paths = [self.write(name, f"id: {name[:-4]}\ntitle: T\n{extra}\n") for name, extra in bad]
        conn = self.run_import(*paths)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 0)

    def test_reimport_replaces_children(self) -> None:
        path = self.write("s.yml", "id: s\ntitle: S\ncoverage:\n  - level: nation\n")
        self.run_import(path).close()
        path.write_text("id: s\ntitle: S\ncoverage:\n  - level: state\n    fips: '47'\n", encoding="utf-8")
        conn = self.run_import(path)
        self.assertEqual(conn.execute("SELECT level, geoid FROM source_coverage").fetchall(), [("state", "47")])

    def test_existing_database_gets_new_columns(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE sources (id TEXT PRIMARY KEY, yml_filename TEXT NOT NULL, version TEXT,"
            " title TEXT, notes TEXT, provider_name TEXT, provider_agency TEXT, website_url TEXT,"
            " download_url TEXT, download_description TEXT, download_file_size TEXT,"
            " requires_account INTEGER NOT NULL DEFAULT 0, sensitive INTEGER NOT NULL DEFAULT 0,"
            " pipeline_ready INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()
        conn = self.run_import(self.write("s.yml", "id: s\ntitle: S\nstudent_suitability: not_suitable\n"))
        self.assertEqual(
            conn.execute("SELECT student_suitability FROM sources").fetchone(), ("not_suitable",)
        )


if __name__ == "__main__":
    unittest.main()

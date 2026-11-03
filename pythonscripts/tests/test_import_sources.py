"""Tests for import_sources_to_sqlite.py. Run: python3 -m unittest discover pythonscripts/tests"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import textwrap
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPTS =Path(__file__).resolve().parents[1]
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

    def test_national_level_sources_stay_in_sqlite(self) -> None:
        path = self.write("us.yml", "id: us-totals\ntitle: US\ngeographic_granularity: nation\nfilters: {year: true}\n")
        self.assertEqual(importer.import_files([path], self.db, SCHEMA), [])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT id FROM sources").fetchall(), [("us-totals",)])

    def test_a_source_without_filters_is_reported(self) -> None:
        path = self.write("n.yml", "id: no-filters\ntitle: N\n")
        self.assertEqual(importer.import_files([path], self.db, SCHEMA), ["no-filters"])

    def test_full_import_fails_on_missing_filters(self) -> None:
        self.write("n.yml", "id: no-filters\ntitle: N\n")
        with patch.object(importer, "DEFAULT_SOURCES_DIR", self.dir), patch.object(importer, "DEFAULT_DB_PATH", self.db):
            with patch.object(sys, "argv", ["import", "all"]), self.assertRaises(SystemExit) as stopped:
                importer.main()
        self.assertEqual(stopped.exception.code, 1)

    def test_national_coverage_with_local_granularity_imports(self) -> None:
        path = self.write("c.yml", "id: c\ntitle: C\ngeographic_granularity: county\ncoverage:\n  - level: nation\n")
        importer.import_files([path], self.db, SCHEMA)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT id, geographic_granularity FROM sources").fetchall(), [("c", "county")])

    def test_download_format_is_stored(self) -> None:
        path = self.write("z.yml", "id: z\ntitle: Z\ndownload:\n  url: https://example.gov/z.zip\n  format: ZIP/CSV\n")
        importer.import_files([path], self.db, SCHEMA)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT download_format FROM sources").fetchone(), ("zip/csv",))

    def test_invalid_download_format_skips_the_source(self) -> None:
        paths = [
            self.write("pdf.yml", "id: pdf\ntitle: P\ndownload:\n  url: https://example.gov/p.pdf\n  format: pdf\n"),
            self.write("nourl.yml", "id: nourl\ntitle: N\ndownload:\n  format: csv\n"),
        ]
        importer.import_files(paths, self.db, SCHEMA)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 0)

    def test_reimport_replaces_children(self) -> None:
        path = self.write("s.yml", "id: s\ntitle: S\ncoverage:\n  - level: nation\n")
        self.run_import(path).close()
        path.write_text("id: s\ntitle: S\ncoverage:\n  - level: state\n    fips: '47'\n", encoding="utf-8")
        conn = self.run_import(path)
        self.assertEqual(conn.execute("SELECT level, geoid FROM source_coverage").fetchall(), [("state", "47")])

    def test_full_import_removes_sources_whose_yaml_is_gone(self) -> None:
        keep = self.write("keep.yml", "id: keep\ntitle: Keep\ncoverage:\n  - level: nation\n")
        gone = self.write("gone.yml", "id: gone\ntitle: Gone\ncoverage:\n  - level: nation\n")
        importer.import_files([keep, gone], self.db, SCHEMA, prune=True)
        gone.unlink()
        importer.import_files([keep], self.db, SCHEMA, prune=True)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT id FROM sources").fetchall(), [("keep",)])
            self.assertEqual(conn.execute("SELECT source_id FROM source_coverage").fetchall(), [("keep",)])

    def test_full_import_removes_sources_that_no_longer_import(self) -> None:
        path = self.write("s.yml", "id: s\ntitle: S\n")
        importer.import_files([path], self.db, SCHEMA, prune=True)
        path.write_text("id: s\ntitle: S\ngeographic_granularity: planet\n", encoding="utf-8")
        importer.import_files([path], self.db, SCHEMA, prune=True)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 0)

    def test_single_file_import_keeps_other_sources(self) -> None:
        first = self.write("a.yml", "id: a\ntitle: A\n")
        second = self.write("b.yml", "id: b\ntitle: B\n")
        importer.import_files([first, second], self.db, SCHEMA, prune=True)
        importer.import_files([first], self.db, SCHEMA)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT id FROM sources ORDER BY id").fetchall(), [("a",), ("b",)])

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

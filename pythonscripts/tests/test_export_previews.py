"""Tests for export_previews.py. Run: python -m pytest pythonscripts/tests/test_export_previews.py"""

from __future__ import annotations

import ast
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import export_previews as ep  # noqa: E402

VOCABULARY = {
    "vintage": 2025,
    "states": [{"geoid": "47", "name": "Tennessee", "usps": "TN"}, {"geoid": "51", "name": "Virginia", "usps": "VA"}],
    "counties": [
        {"geoid": "47093", "name": "Knox County", "state": "TN"},
        {"geoid": "47037", "name": "Davidson County", "state": "TN"},
        {"geoid": "47055", "name": "DeKalb County", "state": "TN"},
        {"geoid": "51159", "name": "Richmond County", "state": "VA"},
        {"geoid": "51760", "name": "Richmond city", "state": "VA"},
    ],
}

FIPS_CSV = (
    "State FIPS Code,County FIPS Code,5-digit FIPS Code,Name,Rate\n"
    "statecode,countycode,fipscode,county,v001_rawvalue\n"
    "47,000,47000,Tennessee,7.5\n"
    "47,093,47093,Knox County,6.25\n"
    "47,037,47037,Davidson County,8\n"
)


def xlsx_bytes() -> bytes:
    """A Census-style table: merged title, two header rows (the second is years), data, a footnote."""
    strings = ["Table 1. Population", "Area", "Estimate", "Tennessee", "Knox County", "Source: Census"]
    shared = "".join(f"<si><t>{s}</t></si>" for s in strings)

    def cell(ref, value, kind=None):
        if value is None:
            return ""
        attr = f' t="{kind}"' if kind else ""
        return f'<c r="{ref}"{attr}><v>{value}</v></c>'

    rows = [
        (1, [cell("A1", 0, "s")]),
        (2, [cell("A2", 1, "s"), cell("B2", 2, "s")]),
        (3, [cell("B3", 2020), cell("C3", 2021)]),
        (4, [cell("A4", 3, "s"), cell("B4", "6910840"), cell("C4", "78.900000000000006")]),
        (5, [cell("A5", 4, "s"), cell("B5", "478971"), cell("C5", "80.5")]),
        (6, [cell("A6", 5, "s")]),
    ]
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(f'<row r="{r}">{"".join(cells)}</row>' for r, cells in rows)
        + '</sheetData><mergeCells count="2"><mergeCell ref="A1:C1"/><mergeCell ref="B2:C2"/></mergeCells></worksheet>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("xl/workbook.xml", "<workbook/>")
        book.writestr("xl/sharedStrings.xml", f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>')
        book.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


class ReadTableTest(unittest.TestCase):
    def test_csv_with_a_row_of_variable_codes(self):
        fmt, header, codes, rows = ep.read_table(FIPS_CSV.encode("utf-8"))
        self.assertEqual(fmt, "csv")
        self.assertEqual(header[2], "5-digit FIPS Code")
        self.assertEqual(codes[2], "fipscode")
        self.assertEqual(len(rows), 3)

    def test_cp1252_csv(self):
        fmt, header, codes, rows = ep.read_table("NAME,POP\nDoña Ana County,1\n".encode("cp1252"))
        self.assertEqual(rows[0][0], "Doña Ana County")
        self.assertIsNone(codes)

    def test_zip_with_csv(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "x")
            archive.writestr("data/table.csv", "A,B\n1,2\n3,4\n")
        fmt, header, _, rows = ep.read_table(buffer.getvalue())
        self.assertEqual((fmt, header, rows), ("zip/csv", ["A", "B"], [["1", "2"], ["3", "4"]]))

    def test_zip_without_a_table_is_unsupported(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("database.accdb", b"\x00\x01")
        with self.assertRaisesRegex(ep.UnsupportedFormat, "accdb"):
            ep.read_table(buffer.getvalue())

    def test_html_page_is_unsupported(self):
        with self.assertRaises(ep.UnsupportedFormat):
            ep.read_table(b"<!DOCTYPE html><html><body>Pick a state</body></html>")

    def test_census_style_xlsx(self):
        fmt, header, codes, rows = ep.read_table(xlsx_bytes())
        self.assertEqual(fmt, "xlsx")
        self.assertEqual(header, ["Area", "Estimate / 2020", "Estimate / 2021"])
        self.assertEqual(rows, [["Tennessee", "6910840", "78.9"], ["Knox County", "478971", "80.5"]])


class CountyDetectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "vocabulary.json"
        path.write_text(json.dumps(VOCABULARY), encoding="utf-8")
        self.vocabulary = ep.load_vocabulary(path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fips_column(self):
        _, header, _, rows = ep.read_table(FIPS_CSV.encode("utf-8"))
        found = ep.detect_county(header, rows, self.vocabulary)
        self.assertEqual(found["method"], "fips_column")
        self.assertEqual(found["fips"], [None, "47093", "47037"])

    def test_census_state_and_county_codes(self):
        header = ["SUMLEV", "STATE", "COUNTY", "CTYNAME"]
        rows = [["040", "47", "000", "Tennessee"], ["050", "47", "93", "Knox County"], ["050", "47", "037", "Davidson County"]]
        found = ep.detect_county(header, rows, self.vocabulary)
        self.assertEqual(found["method"], "state_county_codes")
        self.assertEqual(found["fips"], [None, "47093", "47037"])

    def test_county_names_with_state_codes(self):
        header = ["1. YEAR", "7. COUNTY", "8. ST"]
        rows = [["2024", "KNOX", "TN"], ["2024", "DE KALB", "TN"], ["2024", "RICHMOND CITY", "VA"], ["2024", "Richmond", "VA"], ["2024", "KNOX", "TN"]]
        found = ep.detect_county(header, rows, self.vocabulary)
        self.assertEqual(found["method"], "county_name")
        self.assertEqual(found["fips"], ["47093", "47055", "51760", "51159", "47093"])

    def test_no_county_column(self):
        self.assertIsNone(ep.detect_county(["Year", "Value"], [["2020", "1"]], self.vocabulary))

    def test_names_that_mostly_do_not_match_are_ignored(self):
        header = ["County", "State"]
        rows = [["Atlantis", "TN"], ["Knox", "TN"], ["Lemuria", "TN"]]
        self.assertIsNone(ep.detect_county(header, rows, self.vocabulary))


class BuildPreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "vocabulary.json").write_text(json.dumps(VOCABULARY), encoding="utf-8")
        self.vocabulary = ep.load_vocabulary(self.dir / "vocabulary.json")
        self.source = {"id": "chr-test", "title": "CHR test", "download": {"url": "https://example.gov/chr.csv"}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_variables_sample_summary_and_county_files(self):
        out = self.dir / "previews"
        manifest = ep.build_preview(self.source, FIPS_CSV.encode("utf-8"), out, self.vocabulary)
        target = out / "chr-test"
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["county", "manifest.json", "sample.json", "summary.json", "variables.json"])
        self.assertEqual(sorted(p.name for p in (target / "county").iterdir()), ["47037.json", "47093.json"])
        knox = json.loads((target / "county" / "47093.json").read_text(encoding="utf-8"))
        self.assertEqual(knox, {"fips": "47093", "rows": [["47", "093", "47093", "Knox County", "6.25"]], "rows_included": 1, "rows_total": 1, "truncated": False})
        variables = json.loads((target / "variables.json").read_text(encoding="utf-8"))["columns"]
        self.assertEqual(variables[4], {"name": "Rate", "code": "v001_rawvalue", "type": "number"})
        summary = json.loads((target / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["rows"], 3)
        self.assertEqual({k: summary["columns"][4][k] for k in ("min", "max", "mean", "median")}, {"min": 6.25, "max": 8, "mean": 7.25, "median": 7.5})
        self.assertEqual(summary["columns"][3]["top"][0], ["Davidson County", 1])
        self.assertEqual(manifest["county"]["method"], "fips_column")
        self.assertEqual(manifest["county"]["rows_unmatched"], 1)
        self.assertFalse(manifest["tier2"])

    def test_files_stay_under_the_budget(self):
        rows = "".join(f"47,093,47093,Knox County,{i}.{'5' * 40}\n" for i in range(400))
        data = ("State,County,5-digit FIPS Code,Name,Value\n" + rows).encode("utf-8")
        with mock.patch.object(ep, "MAX_FILE_BYTES", 4_000):
            manifest = ep.build_preview(self.source, data, self.dir / "previews", self.vocabulary)
            target = self.dir / "previews" / "chr-test"
            sizes = [p.stat().st_size for p in target.rglob("*.json")]
        self.assertTrue(all(size <= 4_000 for size in sizes), sizes)
        self.assertLess(manifest["sample_rows"], 100)
        knox = json.loads((target / "county" / "47093.json").read_text(encoding="utf-8"))
        self.assertTrue(knox["truncated"])
        self.assertEqual(knox["rows_total"], 400)
        self.assertEqual(manifest["county"]["truncated"], ["47093"])
        self.assertTrue(manifest["tier2"])

    def test_output_is_deterministic(self):
        out = self.dir / "previews"
        ep.build_preview(self.source, FIPS_CSV.encode("utf-8"), out, self.vocabulary)
        first = {p.relative_to(out): p.read_bytes() for p in out.rglob("*.json")}
        ep.build_preview(self.source, FIPS_CSV.encode("utf-8"), out, self.vocabulary)
        second = {p.relative_to(out): p.read_bytes() for p in out.rglob("*.json")}
        self.assertEqual(first, second)
        self.assertTrue(all(b"\r\n" not in data for data in first.values()))


class MainTest(unittest.TestCase):
    def test_index_records_previews_and_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vocabulary.json").write_text(json.dumps(VOCABULARY), encoding="utf-8")
            catalog = {
                "sources": [
                    {"id": "has-file", "title": "Has file", "download": {"url": "https://example.gov/a.csv"}},
                    {"id": "page-only", "title": "Page only", "download": {"description": "pick a state"}},
                    {"id": "access-db", "title": "Access", "download": {"url": "https://example.gov/b.zip"}},
                ]
            }
            (root / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
            cache = root / "cache"
            ep.cache_path(cache, "has-file", "https://example.gov/a.csv").parent.mkdir(parents=True)
            ep.cache_path(cache, "has-file", "https://example.gov/a.csv").write_bytes(FIPS_CSV.encode("utf-8"))
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("db.accdb", b"\x00")
            ep.cache_path(cache, "access-db", "https://example.gov/b.zip").write_bytes(buffer.getvalue())
            (root / "out" / "removed-source").mkdir(parents=True)

            args = ["--catalog", str(root / "catalog.json"), "--out", str(root / "out"), "--cache", str(cache), "--vocabulary", str(root / "vocabulary.json"), "--offline"]
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(ep.main(args), 0)
            index = json.loads((root / "out" / "index.json").read_text(encoding="utf-8"))["sources"]
            self.assertEqual(index["has-file"], {"status": "ok", "rows": 3, "counties": 2, "tier2": False})
            self.assertEqual(index["page-only"], {"status": "none", "reason": "no direct download"})
            self.assertEqual(index["access-db"]["status"], "none")
            self.assertIn("accdb", index["access-db"]["reason"])
            self.assertFalse((root / "out" / "removed-source").exists())


class StandardLibraryOnlyTest(unittest.TestCase):
    def test_imports_are_standard_library(self):
        tree = ast.parse((SCRIPTS / "export_previews.py").read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules.add(node.module.split(".")[0])
        self.assertEqual(modules - set(sys.stdlib_module_names) - {"__future__"}, set())


if __name__ == "__main__":
    unittest.main()

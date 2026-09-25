#!/usr/bin/env python3
"""
Export the SQLite catalog (database/sources.db) to data/exports/catalog.json.

The website builds from this export, so SQLite (not the YAML) is what the
site shows. Run after importing:

    python import_sources_to_sqlite.py all
    python export_catalog.py

Output is deterministic (sorted, no timestamps), so re-running without data
changes produces no diff. Use --check in CI to fail when the committed export
is out of date with the database.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = SCRIPT_DIR.parent / "database" / "sources.db"
DEFAULT_OUT_PATH = SCRIPT_DIR.parent / "data" / "exports" / "catalog.json"
EXPORT_VERSION = 1


def _drop_empty(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if value not in (None, "", [], {})}


def _source_record(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    source_id = row["id"]

    filters = conn.execute(
        "SELECT year, state, county, zipcode FROM source_filters WHERE source_id = ?", (source_id,)
    ).fetchone()

    variables = [
        r["variable_name"]
        for r in conn.execute(
            "SELECT variable_name FROM source_variables"
            " WHERE source_id = ? AND visibility = 'frontend' ORDER BY sort_order",
            (source_id,),
        )
    ]

    description_rows = conn.execute(
        "SELECT tag_name, active FROM description_tags WHERE source_id = ? ORDER BY id",
        (source_id,),
    ).fetchall()
    description_tags = [r["tag_name"] for r in description_rows if r["active"]]

    analysis_tags: dict[str, dict[str, bool]] = {}
    for r in conn.execute(
        "SELECT tag_set, tag_name, value FROM analysis_tags"
        " WHERE source_id = ? ORDER BY tag_set, id",
        (source_id,),
    ):
        analysis_tags.setdefault(r["tag_set"], {})[r["tag_name"]] = bool(r["value"])

    coverage = [
        {"level": r["level"], "geoid": r["geoid"]}
        for r in conn.execute(
            "SELECT level, geoid FROM source_coverage WHERE source_id = ? ORDER BY level, geoid",
            (source_id,),
        )
    ]

    location_row = conn.execute(
        "SELECT latitude, longitude, label FROM source_location WHERE source_id = ?", (source_id,)
    ).fetchone()

    record = {
        "id": source_id,
        "title": row["title"],
        "version": row["version"],
        "description": row["description"],
        "about": row["about"],
        "notes": row["notes"],
        "published_date": row["published_date"],
        "provider": _drop_empty(
            {"name": row["provider_name"], "agency": row["provider_agency"], "url": row["website_url"]}
        ),
        "download": _drop_empty(
            {
                "url": row["download_url"],
                "description": row["download_description"],
                "file_size": row["download_file_size"],
            }
        ),
        "requires_account": bool(row["requires_account"]),
        "sensitive": bool(row["sensitive"]),
        "pipeline_ready": bool(row["pipeline_ready"]),
        "student_suitability": row["student_suitability"],
        "geographic_granularity": row["geographic_granularity"],
        "coverage": coverage,
        "location": _drop_empty(dict(location_row)) if location_row else None,
        "filters": {k: bool(filters[k]) for k in ("year", "state", "county", "zipcode")}
        if filters
        else None,
        "variables": variables,
        "description_tags": description_tags,
        "analysis_tags": analysis_tags,
    }
    # Keep booleans (False is meaningful); drop only missing/empty values.
    return {k: v for k, v in record.items() if isinstance(v, bool) or v not in (None, "", [], {})}


def build_export(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
        sources = [_source_record(conn, row) for row in rows]
    finally:
        conn.close()
    return {"exportVersion": EXPORT_VERSION, "sourceCount": len(sources), "sources": sources}


def render(export: dict[str, Any]) -> str:
    return json.dumps(export, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    parser.add_argument("--check", action="store_true", help="fail if --out differs from the database")
    args = parser.parse_args()

    if not args.db.exists():
        sys.exit(f"Database not found: {args.db}\nRun: python import_sources_to_sqlite.py all")

    text = render(build_export(args.db))

    if args.check:
        current = args.out.read_text(encoding="utf-8").replace("\r\n", "\n") if args.out.exists() else ""
        if current != text:
            sys.exit(f"{args.out} is out of date. Run: python export_catalog.py")
        print(f"{args.out} is up to date.")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"Exported {build_export(args.db)['sourceCount']} source(s) -> {args.out}")


if __name__ == "__main__":
    main()

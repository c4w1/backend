#!/usr/bin/env python3
"""
Import YAML source files from data/sources into database/sources.db.

Usage:
    python import_sources_to_sqlite.py all
    python import_sources_to_sqlite.py census-county-estimates-all-2025.yml
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"
DEFAULT_DB_PATH = SCRIPT_DIR.parent / "database" / "sources.db"
DEFAULT_SCHEMA_PATH = SCRIPT_DIR.parent / "database" / "schema.sql"


def find_source_files(sources_dir: Path, one_file: str | None) -> list[Path]:
    if one_file:
        path = Path(one_file)
        if not path.is_absolute():
            candidate = sources_dir / path
            if candidate.exists():
                path = candidate
        return [path] if path.is_file() else []

    files: list[Path] = []
    for pattern in ("*.yml", "*.yaml"):
        files.extend(sources_dir.glob(pattern))
    for path in sorted(sources_dir.iterdir()):
        if path.is_file() and path.suffix == "":
            files.append(path)
    return sorted(set(files))


def apply_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    conn.executescript(schema_path.read_text(encoding="utf-8"))


def as_bool(value: Any) -> int | None:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "1"):
            return 1
        if lowered in ("false", "no", "0"):
            return 0
    return None


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_description_tags(tags: Any) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    if isinstance(tags, list):
        for item in tags:
            name = as_text(item)
            if name:
                rows.append((name, 1))
    elif isinstance(tags, dict):
        for key, value in tags.items():
            name = as_text(key)
            if not name:
                continue
            active = as_bool(value)
            rows.append((name, active if active is not None else 1))
    return rows


def load_analysis_tags(doc: dict[str, Any]) -> list[tuple[str, str, int]]:
    rows: list[tuple[str, str, int]] = []
    for key, value in doc.items():
        if key == "description_tags" or not key.endswith("_tags"):
            continue
        if not isinstance(value, dict):
            continue
        for tag_name, tag_value in value.items():
            name = as_text(tag_name)
            if not name:
                continue
            active = as_bool(tag_value)
            if active is None:
                continue
            rows.append((key, name, active))
    return rows


def load_frontend_variables(doc: dict[str, Any]) -> list[str]:
    variables = doc.get("variables")
    if variables is None:
        variables = doc.get("variable_names")
    if not isinstance(variables, list):
        return []
    names: list[str] = []
    for item in variables:
        name = as_text(item)
        if name:
            names.append(name)
    return names


def load_variable_report(doc: dict[str, Any]) -> list[dict[str, Any]]:
    report = doc.get("variable_report")
    if not isinstance(report, list):
        return []
    return [entry for entry in report if isinstance(entry, dict)]


def delete_source_children(conn: sqlite3.Connection, source_id: str) -> None:
    for table in (
        "source_filters",
        "source_variables",
        "variable_report",
        "description_tags",
        "analysis_tags",
    ):
        conn.execute(f"DELETE FROM {table} WHERE source_id = ?", (source_id,))


def import_source(
    conn: sqlite3.Connection,
    path: Path,
    imported_at: str,
) -> tuple[str, bool]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError("YAML root is not an object")

    source_id = as_text(doc.get("id")) or path.stem
    provider = doc.get("provider") if isinstance(doc.get("provider"), dict) else {}
    download = doc.get("download") if isinstance(doc.get("download"), dict) else {}
    filters = doc.get("filters") if isinstance(doc.get("filters"), dict) else None

    website_url = as_text(provider.get("url"))
    download_url = as_text(download.get("url"))
    pipeline_ready = 1 if website_url and download_url else 0

    delete_source_children(conn, source_id)

    conn.execute(
        """
        INSERT OR REPLACE INTO sources (
            id, yml_filename, version, title, notes,
            provider_name, provider_agency, website_url,
            download_url, download_description, download_file_size,
            requires_account, sensitive, pipeline_ready, imported_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            path.name,
            as_text(doc.get("version")),
            as_text(doc.get("title")),
            as_text(doc.get("notes")),
            as_text(provider.get("name")),
            as_text(provider.get("agency")),
            website_url,
            download_url,
            as_text(download.get("description")),
            as_text(download.get("file_size")),
            1 if doc.get("requires_account") else 0,
            1 if doc.get("sensitive") else 0,
            pipeline_ready,
            imported_at,
        ),
    )

    if filters:
        conn.execute(
            """
            INSERT OR REPLACE INTO source_filters (source_id, year, state, county, zipcode)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_id,
                as_bool(filters.get("year")),
                as_bool(filters.get("state")),
                as_bool(filters.get("county")),
                as_bool(filters.get("zipcode")),
            ),
        )

    for sort_order, variable_name in enumerate(load_frontend_variables(doc)):
        conn.execute(
            """
            INSERT INTO source_variables (source_id, variable_name, sort_order, visibility)
            VALUES (?, ?, ?, 'frontend')
            """,
            (source_id, variable_name, sort_order),
        )

    for entry in load_variable_report(doc):
        sample_values = entry.get("sample_values")
        conn.execute(
            """
            INSERT INTO variable_report (
                source_id, name, type, total_rows, non_empty_count, missing_count,
                missing_pct, unique_count, min_value, max_value, mean_value, sample_values
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                as_text(entry.get("name")),
                as_text(entry.get("type")),
                entry.get("total_rows"),
                entry.get("non_empty_count"),
                entry.get("missing_count"),
                entry.get("missing_pct"),
                entry.get("unique_count"),
                entry.get("min"),
                entry.get("max"),
                entry.get("mean"),
                json.dumps(sample_values) if sample_values is not None else None,
            ),
        )

    for tag_name, active in load_description_tags(doc.get("description_tags")):
        conn.execute(
            """
            INSERT INTO description_tags (source_id, tag_name, active)
            VALUES (?, ?, ?)
            """,
            (source_id, tag_name, active),
        )

    for tag_set, tag_name, value in load_analysis_tags(doc):
        conn.execute(
            """
            INSERT INTO analysis_tags (source_id, tag_set, tag_name, value)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, tag_set, tag_name, value),
        )

    return source_id, bool(pipeline_ready)


def import_files(paths: list[Path], db_path: Path, schema_path: Path) -> None:
    if not paths:
        print("No YAML files found.")
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    imported_at = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(db_path)
    try:
        apply_schema(conn, schema_path)

        imported = 0
        pipeline_ready_count = 0
        skipped: list[str] = []

        for path in paths:
            try:
                source_id, ready = import_source(conn, path, imported_at)
                imported += 1
                if ready:
                    pipeline_ready_count += 1
                print(f"  Imported: {source_id} ({path.name})")
            except Exception as exc:
                skipped.append(f"{path.name}: {exc}")
                print(f"  Skipped: {path.name} — {exc}")

        conn.commit()

        incomplete = imported - pipeline_ready_count
        print()
        print(f"Imported: {imported}")
        print(f"Pipeline-ready (website + download URL): {pipeline_ready_count}")
        print(f"Incomplete (missing website or download URL): {incomplete}")
        if skipped:
            print(f"Skipped: {len(skipped)}")
            for item in skipped:
                print(f"  - {item}")
        print(f"Database: {db_path}")
    finally:
        conn.close()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python import_sources_to_sqlite.py all")
        print("  python import_sources_to_sqlite.py census-county-estimates-all-2025.yml")
        sys.exit(1)

    target = sys.argv[1]
    one_file = None if target == "all" else target
    paths = find_source_files(DEFAULT_SOURCES_DIR, one_file)

    if target != "all" and not paths:
        print(f"Error: file not found: {target}")
        sys.exit(1)

    print(f"Found {len(paths)} file(s)")
    import_files(paths, DEFAULT_DB_PATH, DEFAULT_SCHEMA_PATH)
    print("Done.")


if __name__ == "__main__":
    main()

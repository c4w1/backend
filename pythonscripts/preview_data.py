#!/usr/bin/env python3
"""
Pandas-powered preview, chart, and filter endpoints for the Node backend.

Reads JSON from stdin:
  {"action": "preview"|"chart"|"filters", "source": {...}, "options": {...}}

Writes JSON to stdout.
"""

from __future__ import annotations

import io
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from variableextraction import load_data  # noqa: E402


@contextmanager
def suppress_stdout():
    """load_data prints download progress to stdout, which breaks JSON responses."""
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

GEO_COLUMNS = {
    "state": ["STATE", "STATEFP", "STATE_CODE", "ST"],
    "stateName": ["STNAME", "STATE_NAME", "STATENAME"],
    "county": ["COUNTY", "COUNTYFP", "COUNTY_CODE"],
    "countyName": ["CTYNAME", "COUNTY_NAME", "COUNTYNAME"],
}


def find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    upper_map = {str(c).upper(): c for c in df.columns}
    for candidate in candidates:
        if candidate in upper_map:
            return str(upper_map[candidate])
    return None


def matches_filter(series: pd.Series, value: str) -> pd.Series:
    target = str(value).strip()
    if not target:
        return pd.Series(True, index=series.index)
    as_str = series.astype(str).str.strip()
    match = as_str == target
    match |= as_str.str.lower() == target.lower()
    return match


def load_dataframe(source: dict[str, Any]) -> pd.DataFrame:
    download = source.get("download") or {}
    url = download.get("url")
    if not url:
        raise ValueError("Source has no download URL")
    with suppress_stdout():
        headers, rows = load_data(url)
    df = pd.DataFrame(rows, columns=headers)
    return df


def get_available_filters(df: pd.DataFrame, source: dict[str, Any]) -> dict[str, bool]:
    filters = source.get("filters") or {}
    has_state = bool(find_column(df, GEO_COLUMNS["state"]) or find_column(df, GEO_COLUMNS["stateName"]))
    has_county = bool(find_column(df, GEO_COLUMNS["county"]) or find_column(df, GEO_COLUMNS["countyName"]))
    return {
        "state": bool(filters.get("state")) or has_state,
        "county": bool(filters.get("county")) or has_county,
        "zipcode": bool(filters.get("zipcode")),
        "year": bool(filters.get("year")),
    }


def filter_dataframe(df: pd.DataFrame, state: str | None, county: str | None) -> pd.DataFrame:
    result = df
    state_col = find_column(df, GEO_COLUMNS["state"])
    state_name_col = find_column(df, GEO_COLUMNS["stateName"])
    county_col = find_column(df, GEO_COLUMNS["county"])
    county_name_col = find_column(df, GEO_COLUMNS["countyName"])

    if state:
        mask = pd.Series(False, index=result.index)
        if state_col is not None:
            mask |= matches_filter(result[state_col], state)
        if state_name_col is not None:
            mask |= matches_filter(result[state_name_col], state)
        result = result[mask]

    if county:
        mask = pd.Series(False, index=result.index)
        if county_col is not None:
            mask |= matches_filter(result[county_col], county)
        if county_name_col is not None:
            mask |= matches_filter(result[county_name_col], county)
        result = result[mask]

    return result


def select_preview_columns(
    source: dict[str, Any], df: pd.DataFrame, columns_override: list[str] | None
) -> list[str]:
    if columns_override:
        return [c for c in columns_override if c in df.columns]
    variables = source.get("variables") or source.get("variable_names") or []
    selected = [v for v in variables if v in df.columns]
    if selected:
        return selected[:8]
    return list(df.columns[:8])


def default_focal_variable(source: dict[str, Any], df: pd.DataFrame) -> str | None:
    report = source.get("variable_report") or []
    for entry in report:
        if entry.get("type") in ("integer", "float") and entry.get("name") in df.columns:
            return entry["name"]
    for name in ["POPESTIMATE2025", "POPESTIMATE2024", "VALUE", "TOTAL"]:
        if name in df.columns:
            return name
    variables = source.get("variables") or source.get("variable_names") or []
    for name in variables:
        if name in df.columns:
            return name
    return str(df.columns[-1]) if len(df.columns) else None


def cell_value(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_preview(source: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    state = options.get("state")
    county = options.get("county")
    limit = int(options.get("limit", 25))
    offset = int(options.get("offset", 0))
    columns_raw = options.get("columns")
    columns_override = (
        [c.strip() for c in columns_raw.split(",") if c.strip()] if columns_raw else None
    )

    df = load_dataframe(source)
    filtered = filter_dataframe(df, state, county)
    columns = select_preview_columns(source, df, columns_override)
    page = filtered.iloc[offset : offset + limit]

    rows = [[cell_value(row[col]) for col in columns] for _, row in page.iterrows()]

    return {
        "id": source.get("id"),
        "columns": columns,
        "rows": rows,
        "total": int(len(filtered)),
        "filters": {"state": state, "county": county},
        "availableFilters": get_available_filters(df, source),
        "engine": "pandas",
    }


def build_chart(source: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    state = options.get("state")
    county = options.get("county")
    limit = int(options.get("limit", 50))
    variable = options.get("variable")

    df = load_dataframe(source)
    filtered = filter_dataframe(df, state, county)
    focal = variable or default_focal_variable(source, df)
    if not focal or focal not in filtered.columns:
        raise ValueError(f"Focal variable not found: {focal}")

    label_col = (
        find_column(filtered, GEO_COLUMNS["countyName"])
        or find_column(filtered, GEO_COLUMNS["stateName"])
        or find_column(filtered, GEO_COLUMNS["county"])
        or str(filtered.columns[0])
    )

    numeric = pd.to_numeric(
        filtered[focal].astype(str).str.replace(",", "", regex=False), errors="coerce"
    )
    chart_df = filtered.assign(_value=numeric, _label=filtered[label_col].astype(str).str.strip())
    chart_df = chart_df[chart_df["_value"].notna()].sort_values("_value", ascending=False).head(limit)

    series = [
        {"label": row["_label"] or f"Row {i + 1}", "value": float(row["_value"])}
        for i, (_, row) in enumerate(chart_df.iterrows())
    ]

    return {
        "variable": focal,
        "label": focal.replace("_", " "),
        "series": series,
        "engine": "pandas",
    }


def list_filter_options(
    source: dict[str, Any], filter_type: str, options: dict[str, Any]
) -> list[dict[str, str]]:
    df = load_dataframe(source)
    state = options.get("state")

    if filter_type == "state":
        code_col = find_column(df, GEO_COLUMNS["state"])
        name_col = find_column(df, GEO_COLUMNS["stateName"])
        seen: dict[str, str] = {}
        for _, row in df.iterrows():
            code = str(row[code_col]).strip() if code_col else ""
            name = str(row[name_col]).strip() if name_col else ""
            if code and name:
                seen[code] = name
            elif name:
                seen[name] = name
            elif code:
                seen[code] = code
        return sorted(
            [{"value": v, "label": lbl} for v, lbl in seen.items()],
            key=lambda x: x["label"],
        )

    if filter_type == "county":
        filtered = filter_dataframe(df, state, None)
        code_col = find_column(df, GEO_COLUMNS["county"])
        name_col = find_column(df, GEO_COLUMNS["countyName"])
        seen: dict[str, str] = {}
        for _, row in filtered.iterrows():
            code = str(row[code_col]).strip() if code_col else ""
            name = str(row[name_col]).strip() if name_col else ""
            if code and name:
                seen[code] = name
            elif name:
                seen[name] = name
            elif code:
                seen[code] = code
        return sorted(
            [{"value": v, "label": lbl} for v, lbl in seen.items()],
            key=lambda x: x["label"],
        )

    return []


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        action = payload.get("action")
        source = payload.get("source") or {}
        options = payload.get("options") or {}

        if action == "preview":
            result = build_preview(source, options)
        elif action == "chart":
            result = build_chart(source, options)
        elif action == "filters":
            result = {
                "id": source.get("id"),
                "type": options.get("type", "state"),
                "options": list_filter_options(source, options.get("type", "state"), options),
                "engine": "pandas",
            }
        else:
            raise ValueError(f"Unknown action: {action}")

        json.dump(result, sys.stdout)
    except Exception as exc:
        json.dump({"error": str(exc)}, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()

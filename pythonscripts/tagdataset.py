#!/usr/bin/env python3
"""
Simple dataset tagger.

What it does:
- Reads one or more YAML files from backend/data/sources
- Uses the requests library to fetch text content from any URLs found in the YAML
- Collects text from YAML fields + URL page content
- Generates simple keyword-based description tags
- Writes tags back to each file as: description_tags: [..]

Usage:
    python tagdataset.py all
    python tagdataset.py usda-milk-production.yml
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import requests
import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"


# Keyword map, update as needed
# words on the left are the tags that will be applied if any of the words on the right are found in the text
TAG_RULES = {
    "Agriculture": ["agriculture", "farm", "crop", "usda", "rural", "livestock", "commodity", "milk", "dairy"],
    "Business & Finance": ["business", "finance", "financial", "bank", "loan", "investment", "credit", "revenue", "naics"],
    "Children & Families": ["children", "child", "families", "family", "youth", "infant", "maternal", "household"],
    "Economy": ["economy", "economic", "gdp", "income", "poverty", "inflation", "establishment", "earnings"],
    "Election": ["election", "voter", "voting", "ballot", "precinct", "turnout", "campaign"],
    "PreK-12 Education": ["school", "district", "prek", "k-12", "k12", "student", "teacher", "nces", "enrollment"],
    "Energy & Environment": ["energy", "environment", "epa", "air quality", "water", "pollution", "emission", "toxic", "climate"],
    "Government Administration": ["government", "administration", "agency", "public", "bureau", "municipal", "state", "federal"],
    "Health & Social Services": ["health", "public health", "cdc", "disease", "mortality", "diabetes", "social services", "medicaid"],
    "Higher Education": ["higher education", "college", "university", "postsecondary", "campus", "degree"],
    "Historic Preservation": ["historic", "preservation", "heritage", "landmark", "register", "archaeology"],
    "Housing & Buildings": ["housing", "rent", "mortgage", "homeowner", "vacancy", "residential", "building", "permit"],
    "Labor & Workforce Development": ["labor", "workforce", "employment", "unemployment", "wage", "occupation", "payroll", "bls"],
    "Law & Public Safety": ["law", "crime", "public safety", "fbi", "arrest", "offense", "law enforcement", "nibrs"],
    "Parks & Recreation": ["park", "parks", "recreation", "trail", "greenway", "playground", "open space"],
    "Population Data": ["population", "census", "acs", "demographic", "household", "age", "sex", "county", "resident"],
    "Public Utilities": ["utility", "utilities", "electric", "gas", "water utility", "sewer", "wastewater", "grid"],
    "Restaurant and Food Service": ["restaurant", "food service", "food", "dining", "inspection", "kitchen", "meal"],
    "Transportation": ["transportation", "transit", "traffic", "road", "highway", "bridge", "rail", "airport", "mobility"],
}

def find_yaml_files(sources_dir: Path, one_file: str | None) -> list[Path]:
    if one_file:
        file_path = Path(one_file)
        return [file_path] if file_path.exists() else []
    return sorted(sources_dir.glob("*.yml"))


def extract_urls(node: Any) -> list[str]:
    """Recursively find URL-like values in a YAML object."""
    urls: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and ("url" in str(key).lower() or "link" in str(key).lower()):
                if value.startswith("http://") or value.startswith("https://"):
                    urls.append(value)
            urls.extend(extract_urls(value))
    elif isinstance(node, list):
        for item in node:
            urls.extend(extract_urls(item))
    return sorted(set(urls))


def html_to_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_url_text(url: str, timeout: int, max_chars: int) -> str:
    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        return html_to_text(response.text)[:max_chars]
    except Exception as exc:
        print(f"  - Could not fetch {url}: {exc}")
        return ""


def build_text_blob(data: dict[str, Any], timeout: int, max_url_text: int) -> str:
    parts: list[str] = []

    for key in ["title", "short_title", "description"]:
        value = data.get(key)
        if isinstance(value, str):
            parts.append(value)

    provider = data.get("provider", {})
    if isinstance(provider, dict):
        for key in ["name", "agency"]:
            value = provider.get(key)
            if isinstance(value, str):
                parts.append(value)

    for url in extract_urls(data):
        parts.append(fetch_url_text(url, timeout=timeout, max_chars=max_url_text))

    return "\n".join(parts).lower()


def infer_tags(text: str) -> dict[str, bool]:
    return {tag: any(word in text for word in keywords) for tag, keywords in TAG_RULES.items()}


def process_file(path: Path, timeout: int, max_url_text: int, dry_run: bool) -> None:
    print(f"\nProcessing: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        print("  - Skipped (YAML root is not an object)")
        return

    text_blob = build_text_blob(data, timeout=timeout, max_url_text=max_url_text)
    tags = infer_tags(text_blob)

    matched = [tag for tag, val in tags.items() if val]
    print(f"  - Tags matched: {matched}")

    if dry_run:
        return

    data["description_tags"] = tags

    with path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, sort_keys=False, allow_unicode=False)

    print("  - Updated file")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python tagdataset.py all")
        print("  python tagdataset.py usda-milk-production.yml")
        sys.exit(1)

    target = sys.argv[1]

    if target == "all":
        files = find_yaml_files(sources_dir=DEFAULT_SOURCES_DIR, one_file=None)
    else:
        #accept filename or path
        target_path = Path(target)
        if not target_path.is_absolute() and not target_path.exists():
            target_path = DEFAULT_SOURCES_DIR / target_path
        files = find_yaml_files(sources_dir=DEFAULT_SOURCES_DIR, one_file=str(target_path))

    if not files:
        print("No YAML files found.")
        return

    for path in files:
        process_file(path=path, timeout=10, max_url_text=4000, dry_run=False)


if __name__ == "__main__":
    main()

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
    "Agriculture": ["agricultur", "farm", "crop", "usda", "soil", "plant species", "fruit", "vegetable", "livestock", "meal", "produce ", "organic", "insect", 
                    "aquacultur", "poultry", "food distribut", "nutrition", "plant industr", "plant ship"],
    "Business & Finance": ["business", "financial", "vendor", "commerc", "econom", "enterprise", "industry", "issued license" "license application", 
                           "domain registration", "retail", "mobile service", "storefront", "dealer", "gross receipt", "ppp loan", " bank", "insurance"],
    "Children & Families": ["children", "child", "families", "foster", "visitation", "homemaker", "pediatr", "parent", "mother", "father", "infant", "birth", 
                            "maternal", "paternal", "family"],
    "Economy": ["economy", "economic", "gdp", "income", "poverty", " wage"],
    "Elections & Politics": ["election", "voter", "voting", "ballot", "campaign finance", "lobbying", "lobbyist", "campaign consultant", "ballot", 
                             "contributions", " poll", "legislative", "congress", "representative"],
    "PreK-12 Education": ["prek", "pre-k", "k-12", "public school", "school progress", "elementary school", "middle school", "high school", "school attendance", 
                          "school grounds", "early learning", "private school", "school profile", "school district", "all school", "doe "],
    "Energy & Environment": ["energy", "environment", "epa", "air quality", "pollution", "emission", "asbestos", "fuel", "habitat", "recycling", "electricity", 
                             " tree", "conservation", "hazard zone", "sea level", "flood risk", "weather", " gas", "water bod", "bodies of water", 
                             "energy consumption", "soil", "canopy", "flood hazard", "water quality", " plant", " green", "efficien", "rainfall", 
                             "water protection"],
    "Government Administration": ["govern", "administration", "agency", "city employ", "budget", "contractor", "revenue", "spending", "contract opportunit", 
                                  "investments", "bonds", "bid opportunit", " tax", "audited financial", "annual financial report", "city department", "funds", 
                                  "civil service", "mayor", "expenditure", "legislative", "congress", "representative", "department of state", 
                                  "secretary of state", "treasury", "state pension", "city pension", "municipalit", "public asset", "agency and department", 
                                  "department name"],
    "Health & Social Services": ["health", "cdc", "disease", "covid", "hospital", "death", "vaccin", "resilienc", "overdose", "fatality", "injury", 
                                 "substance use", "rodent", "pest", "case count", "animal", "dog ", "medic", "snap", "immunization", "birth", "mortality rate", 
                                 "infant", "ambulatory", "acute care", "acute-care", "virus", "viral", "social service", "mosquito", "poison", "syndrom", "mold", 
                                 "blood", "naloxone", "patient", "nutrition", "disabled", "senior", "assisted living", "long-term care", "care facilit"],
    "Higher Education": ["higher education", "college", "university", "postsecondary", "post-secondary", "post secondary", "campus", "state school", 
                         "undergraduate", "technical school", "technical institution", "career institution", "career and technical", "scholar", "tuition", 
                         "admissions"],
    "Historic Preservation": ["historic", "preservation", "heritage", "landmark", "endangered building"],
    "Housing & Buildings": ["building", "permits", "vacant", " house", "housing", "elevation", "dwelling", "unit preapproval", "propert", "eviction", "assessor", 
                            "plumbing", "inspection complaint", "code enforcement", " rent", "landlord", "fire inspection", "fire violation", 
                            "development permit", "condominium", "electrical inspection", "housing code", " structure", "zoning", "community development", 
                            "housing development", "architect", "homeless", "unhoused", "homeowner", "mortgage", "occupancy", "encampment", "zoning", "land use", 
                            "foreclos", "construct"],
    "Labor & Workforce Development": ["labor", "workforce", "employment", "unemployment",  "job ", "jobs", "workplace", "worker", " coops", "apprenticeship", 
                                      "pension", "career", "occupation", " wage", "hour compliance"],
    "Law & Public Safety": ["legal", "crime", "public safety", "fbi", "arrest", "offense", "police", "violence", "victim", "crime", "fire station", "homicide", 
                            "casualt", "offender", "internal affairs", "involved officer", "officer-involved", "enforcement", "dispatch", "injur", 
                            "district attorney", "sheriff", "jail", "prison", "bookings", "graffiti", "fatality", "compliance", "collision", "prostitut", 
                            "service incident", "calls for service", "drug", "assault", "marijuana", "cannabis", "burglar", "resistance", "trafficking", 
                            "carcera", "corrections", "homeland", "security", "ethics", "traffic stop", "parole", "gun", "patrol", "crash"],
    "Parks & Recreation": ["parks", "recreation", "trail", "greenway", "playground", "park district", "forest preserve", "park facilit", "swimming pool", 
                           "public pool", "park building", "fishing lake", "film", "parklet", "librar", "public art", " art ", " arts ", "entertainment", 
                           " dog", "activities", "play area", "zoo ", "zoos", "touris"],
    "Population Data": ["population", "census", "acs", "demographic", "household", "poverty","ethnicity", "race", "gender", "birth", "mortality rate"],
    "Public Utilities": ["utility", "utilities", "electric", "water utility", "natural gas", "plumbing", "public bathroom", "public restroom", "city facilit", 
                         "pavement", "paving", "curb", "public water", "water fountain", "public works", "water quality", "trash", "tree removal", "street tree", 
                         "tree recycling", "storm drain", "streetlight", "main break", "parcel", "sewer", "wastewater", "waste collect", "broadband", 
                         "connectivity"],
    "Restaurant and Food Service": ["restaurant", "food service", "food", "dining", "full-service", "liquor license", "serve alcohol", "bars"],
    "Transportation": ["transportation", "transit", "traffic", "road", "highway", "bridge", "vehicle", "streets", "trips", " rail", " bus", "congestion", 
                       "parking", "ridership", "pedestrian", "bicycle", "bike", "cyclist", " road", "light camera", "taxi", "scooter", "sidewalk", "commuter", 
                       "street closure", "speed limit", "traveled", "motor", "stops", "right of way", "right-of-way", "pavement", "paving", "intersection", 
                       "cab " "cabs ", "airport", "passenger", "commute", "air cargo", "airplane", "flight", "lanes", "striping", " walk", "walking", "railroad", 
                       "park ride", "park and ride", "truck", "rest area", "aviation", "crash", "collision"]
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

#!/usr/bin/env python3
"""
Simple dataset tagger.

What it does:
- Reads one or more YAML files from backend/data/sources
- Builds a text blob from each dataset's title, description, download description,
  and provider fields
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

import yaml

import manual_tags


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"


# Keyword map, update as needed
# words on the left are the tags that will be applied if any of the words on the right are found in the text
TAG_RULES = {
    "Agriculture": ["agricultur", "farm", "crop", "usda", "soil", "plant species", "fruit", "vegetable",
                    "livestock", "produce ", "aquacultur", "poultry", "food distribut", "nutrition",
                    "plant industr", "plant ship"],
    "Business & Economy": ["business", "financial", "vendor", "commerc", "econom", "issued license",
                           "license application", "domain registration", "retail", "mobile service",
                           "storefront", "dealer", "gross receipt", "ppp loan", "bank", "insurance",
                           "gdp", "income", "wage"],
    "Children & Families": ["children", "child", "families", "foster", "visitation", "homemaker",
                            "pediatr", "parent", "mother", "father", "infant", "birth", "maternal",
                            "paternal", "family"],
    "Elections & Politics": ["election", "voter", "voting", "ballot", "campaign finance", "lobbying",
                             "lobbyist", "campaign consultant", "contributions", "legislative",
                             "congress", "representative"],
    "PreK-12 Education": ["prek", "pre-k", "k-12", "public school", "school progress", "elementary school",
                          "middle school", "high school", "school attendance", "school grounds",
                          "early learning", "private school", "school profile", "school district",
                          "all school"],
    "Energy & Environment": ["epa", "air quality", "pollution", "emission", "asbestos", "fuel", "habitat",
                             "recycling", "electricity", "tree", "conservation", "hazard zone", "sea level",
                             "flood risk", "weather", "gas", "water bod", "bodies of water",
                             "energy consumption", "soil", "canopy", "flood hazard", "water quality",
                             "rainfall", "water protection"],
    "Government Administration": ["govern", "administration", "agency", "city employ", "budget", "contractor",
                                  "revenue", "spending", "contract opportunit", "investments", "bonds",
                                  "bid opportunit", "tax", "audited financial", "annual financial report",
                                  "city department", "civil service", "mayor", "expenditure", "legislative",
                                  "congress", "representative", "department of state", "secretary of state",
                                  "treasury", "state pension", "city pension", "municipalit", "public asset",
                                  "agency and department", "department name"],
    "Health & Social Services": ["health", "cdc", "disease", "covid", "hospital", "death", "vaccin",
                                 "resilienc", "overdose", "fatality", "injury", "substance use", "rodent",
                                 "pest", "case count", "animal", "dog", "medic", "snap", "immunization",
                                 "birth", "mortality rate", "infant", "ambulatory", "acute care",
                                 "acute-care", "virus", "viral", "social service", "mosquito", "poison",
                                 "syndrom", "mold", "blood", "naloxone", "patient", "nutrition", "disabled",
                                 "senior", "assisted living", "long-term care", "care facilit"],
    "Higher Education": ["higher education", "college", "university", "postsecondary", "post-secondary",
                         "post secondary", "campus", "state school", "undergraduate", "technical school",
                         "technical institution", "career institution", "career and technical", "scholar",
                         "tuition", "admissions"],
    "Historic Preservation": ["historic", "preservation", "heritage", "landmark", "endangered building"],
    "Housing & Buildings": ["building", "permits", "vacant", "house", "housing", "elevation", "dwelling",
                            "unit preapproval", "propert", "eviction", "assessor", "plumbing",
                            "inspection complaint", "code enforcement", "rent", "landlord", "fire inspection",
                            "fire violation", "development permit", "condominium", "electrical inspection",
                            "housing code", "zoning", "community development", "housing development",
                            "architect", "homeless", "unhoused", "homeowner", "mortgage", "occupancy",
                            "encampment", "land use", "foreclos", "construct"],
    "Labor & Workforce Development": ["labor", "workforce", "employment", "unemployment", "job ", "jobs",
                                      "workplace", "worker", "coops", "apprenticeship", "pension", "career",
                                      "occupation", "wage", "hour compliance"],
    "Law & Public Safety": ["legal", "crime", "public safety", "fbi", "arrest", "offense", "police",
                            "violence", "victim", "fire station", "homicide", "casualt", "offender",
                            "internal affairs", "involved officer", "officer-involved", "enforcement",
                            "dispatch", "injur", "district attorney", "sheriff", "jail", "prison", "bookings",
                            "graffiti", "fatality", "collision", "prostitut", "service incident",
                            "calls for service", "drug", "assault", "marijuana", "cannabis", "burglar",
                            "resistance", "trafficking", "carcera", "corrections", "homeland", "ethics",
                            "traffic stop", "parole", "gun", "patrol", "crash"],
    "Parks & Recreation": ["parks", "recreation", "trail", "greenway", "playground", "park district",
                           "forest preserve", "park facilit", "swimming pool", "public pool", "park building",
                           "fishing lake", "film", "parklet", "librar", "public art", "entertainment", "dog",
                           "activities", "play area", "zoo ", "zoos", "touris"],
    "Population Data": ["population", "census", "acs", "demographic", "household", "poverty", "ethnicity",
                        "race", "gender", "birth", "mortality rate", "income", "gdp"],
    "Public Utilities": ["utility", "utilities", "electric", "water utility", "natural gas", "plumbing",
                         "public bathroom", "public restroom", "city facilit", "pavement", "paving", "curb",
                         "public water", "water fountain", "public works", "water quality", "trash",
                         "tree removal", "street tree", "tree recycling", "storm drain", "streetlight",
                         "main break", "parcel", "sewer", "wastewater", "waste collect", "broadband",
                         "connectivity"],
    "Restaurant and Food Service": ["restaurant", "food service", "food", "dining", "full-service",
                                    "liquor license", "serve alcohol", "bars"],
    "Transportation": ["transportation", "transit", "traffic", "road", "highway", "bridge", "vehicle",
                       "streets", "trips", "rail", "bus", "congestion", "parking", "ridership", "pedestrian",
                       "bicycle", "bike", "cyclist", "light camera", "taxi", "scooter", "sidewalk", "commuter",
                       "street closure", "speed limit", "traveled", "motor", "stops", "right of way",
                       "right-of-way", "pavement", "paving", "intersection", "airport",
                       "passenger", "commute", "air cargo", "airplane", "flight", "lanes", "striping", "walk",
                       "walking", "railroad", "park ride", "park and ride", "truck", "rest area", "aviation",
                       "crash", "collision"],
    "Wildlife & Nature": ["bird", "wildlife", "species", "ebird", "ornitholog", "habitat", "biodiversity",
                          "flora", "fauna"],
}


def find_yaml_files(sources_dir: Path, one_file: str | None) -> list[Path]:
    if one_file:
        file_path = Path(one_file)
        return [file_path] if file_path.exists() else []
    return sorted(sources_dir.glob("*.yml"))


def build_text_blob(data: dict[str, Any]) -> str:
    parts: list[str] = []

    for key in ["title", "short_title", "description"]:
        value = data.get(key)
        if isinstance(value, str):
            parts.append(value)

    download = data.get("download", {})
    if isinstance(download, dict):
        value = download.get("description")
        if isinstance(value, str):
            parts.append(value)

    provider = data.get("provider", {})
    if isinstance(provider, dict):
        for key in ["name", "agency"]:
            value = provider.get(key)
            if isinstance(value, str):
                parts.append(value)

    return "\n".join(parts).lower()


def infer_tags(text: str) -> list[str]:
    matched: list[str] = []
    for tag, keywords in TAG_RULES.items():
        if any(re.search(r"\b" + re.escape(keyword), text) for keyword in keywords):
            matched.append(tag)
    return sorted(matched)


def process_file(path: Path, dry_run: bool) -> None:
    print(f"\nProcessing: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        print("  - Skipped (YAML root is not an object)")
        return

    if manual_tags.is_manual(data, "description_tags"):
        print("  - Skipped (description_tags set by hand; see manual_tags)")
        return

    text_blob = build_text_blob(data)
    tags = infer_tags(text_blob)

    print(f"  - Tags matched: {tags}")

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
        process_file(path=path, dry_run=False)


if __name__ == "__main__":
    main()

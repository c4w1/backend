"""Load data/tag_vocabulary.yml, the allowed values for description and statistical tags."""

from __future__ import annotations

from pathlib import Path

import yaml

VOCABULARY_PATH = Path(__file__).resolve().parent.parent / "data" / "tag_vocabulary.yml"


def load_vocabulary(path: Path = VOCABULARY_PATH) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    return {
        "description_tags": list(data.get("description_tags") or []),
        "statistical_tags": {k: list(v) for k, v in (data.get("statistical_tags") or {}).items()},
    }

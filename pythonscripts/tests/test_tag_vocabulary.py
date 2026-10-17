import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tag_vocabulary
import tagdataset

SOURCES_DIR = Path(__file__).resolve().parents[2] / "data" / "sources"


def _sources():
    files = sorted(SOURCES_DIR.glob("*.yml"))
    assert files, "expected source YAML files"
    return [(f.name, yaml.safe_load(f.read_text(encoding="utf-8"))) for f in files]


def test_vocabulary_shape():
    vocab = tag_vocabulary.load_vocabulary()
    assert isinstance(vocab["description_tags"], list)
    assert isinstance(vocab["statistical_tags"], dict)
    for group, keys in vocab["statistical_tags"].items():
        assert group.endswith("_tags")
        assert keys and len(keys) == len(set(keys)), group


def test_description_vocabulary_matches_tag_rules():
    vocab = tag_vocabulary.load_vocabulary()
    assert sorted(vocab["description_tags"]) == sorted(tagdataset.TAG_RULES)


def test_sources_use_only_vocabulary_description_tags():
    allowed = set(tag_vocabulary.load_vocabulary()["description_tags"])
    for name, data in _sources():
        unknown = set(data.get("description_tags") or []) - allowed
        assert not unknown, f"{name}: unknown description_tags {sorted(unknown)}"


def check_statistical_tags(name, data, groups):
    """Assert a source's statistical tag groups are all in the vocabulary."""
    for key, value in data.items():
        if not key.endswith("_tags") or key in ("description_tags", "manual_tags"):
            continue
        if not isinstance(value, dict):
            continue
        assert key in groups, f"{name}: tag group {key} missing from vocabulary"
        unknown = set(value) - set(groups[key])
        assert not unknown, f"{name}: {key} has unknown keys {sorted(unknown)}"


def test_sources_use_only_vocabulary_statistical_tags():
    groups = tag_vocabulary.load_vocabulary()["statistical_tags"]
    for name, data in _sources():
        check_statistical_tags(name, data, groups)


def test_manual_tags_list_is_not_a_statistical_group():
    groups = {"anova_tags": ["good-for-one-way-anova"]}
    data = {
        "manual_tags": ["description_tags", "anova_tags"],
        "anova_tags": {"good-for-one-way-anova": True},
    }
    check_statistical_tags("demo", data, groups)

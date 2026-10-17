import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tagdataset


def test_infer_tags_returns_sorted_list(monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {
        "Beta": ["banana"],
        "Alpha": ["apple"],
    })
    result = tagdataset.infer_tags("this text mentions banana and apple")
    assert result == ["Alpha", "Beta"]
    assert isinstance(result, list)


def test_infer_tags_word_boundary_avoids_substring_match(monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {"Meals": ["meal"]})
    assert tagdataset.infer_tags("a warm bowl of oatmeal") == []
    assert tagdataset.infer_tags("school meal programs") == ["Meals"]


def test_infer_tags_keeps_stem_prefix_matches(monkeypatch):
    monkeypatch.setattr(
        tagdataset, "TAG_RULES", {"Government Administration": ["govern"]}
    )
    assert tagdataset.infer_tags("data from the state government") == [
        "Government Administration"
    ]


def test_infer_tags_no_match_returns_empty_list(monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {"Alpha": ["apple"]})
    assert tagdataset.infer_tags("nothing relevant here") == []


def test_build_text_blob_uses_only_local_fields():
    data = {
        "title": "Test Title",
        "description": "Test description",
        "download": {"description": "CSV download"},
        "provider": {"name": "Test Agency", "agency": "Test Department"},
    }
    blob = tagdataset.build_text_blob(data)
    assert "test title" in blob
    assert "test description" in blob
    assert "csv download" in blob
    assert "test agency" in blob
    assert "test department" in blob


def test_build_text_blob_handles_missing_fields():
    assert tagdataset.build_text_blob({}) == ""


def test_tagger_has_no_network_dependency():
    source = inspect.getsource(tagdataset)
    assert "import requests" not in source
    assert "fetch_url_text" not in source
    assert "extract_urls" not in source


def test_wildlife_category_matches_ebird_text():
    blob = tagdataset.build_text_blob({
        "title": "eBird Observation Dataset",
        "provider": {"name": "Cornell Lab of Ornithology"},
    })
    tags = tagdataset.infer_tags(blob)
    assert "Wildlife & Nature" in tags


def test_crime_data_does_not_get_energy_and_environment_tag():
    blob = tagdataset.build_text_blob({
        "title": "National FBI Crime Data Explorer",
        "provider": {
            "name": "Federal Bureau of Investigation",
            "agency": "U.S. Department of Justice",
        },
    })
    tags = tagdataset.infer_tags(blob)
    assert "Energy & Environment" not in tags
    assert "Law & Public Safety" in tags


def test_agriculture_does_not_false_positive_on_oatmeal():
    blob = tagdataset.build_text_blob({"title": "Oatmeal Recall Notices"})
    tags = tagdataset.infer_tags(blob)
    assert "Agriculture" not in tags


def test_no_duplicate_keywords_within_any_category():
    for tag, keywords in tagdataset.TAG_RULES.items():
        assert len(keywords) == len(set(keywords)), f"duplicate keyword in {tag}"


def test_generic_keywords_were_removed():
    removed = {
        "energy", "environment", "green", "efficien", "plant", "security",
        "compliance", "industry", "enterprise", "funds", "structure", "doe",
        "meal", "insect", "organic", "art", "arts", "poll",
    }
    for keywords in tagdataset.TAG_RULES.values():
        for keyword in keywords:
            assert keyword.strip() not in removed


def test_build_text_blob_ignores_non_dict_download():
    blob = tagdataset.build_text_blob({
        "title": "Test Title",
        "download": "a string value",
    })
    assert "test title" in blob
    assert "a string value" not in blob


# Keywords intentionally allowed to carry a trailing space, as a guard against
# matching inflected/unrelated forms of a shorter substring (e.g. "job " so it
# doesn't match "jobsite"). Every other keyword must have no leading/trailing
# whitespace.
KEYWORDS_ALLOWED_PADDING = {"produce ", "job ", "zoo "}


def test_no_keyword_padding_except_documented_exceptions():
    for tag, keywords in tagdataset.TAG_RULES.items():
        for keyword in keywords:
            if keyword in KEYWORDS_ALLOWED_PADDING:
                continue
            assert keyword == keyword.strip(), (
                f"keyword {keyword!r} in {tag!r} has unexpected padding"
            )
